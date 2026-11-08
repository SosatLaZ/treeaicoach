-- TreeAI — module « Économie » (agent X1) : équipement et or, INFORMATIF / un clic du joueur.
-- 16 améliorations vendues par les marchands (mémorisées par PNJ / zone)
-- 17 quoi vendre / jeter en premier (valeur par case : vendeur ou HV si connu)
-- 18 or/heure par activité (butin, quêtes, ventes, HV/courrier ; dépenses à part)
-- 19 envoi de compos à un ami : bouton « Joindre compos » à la boîte aux lettres. UN clic = UNE pièce jointe
--    (UseContainerItem pendant que l'envoi de courrier est ouvert, hors combat) ; le nom du destinataire et
--    « Envoyer » restent au joueur (aucun champ Blizzard n'est modifié). Sinon : texte + instructions.
-- 20 montée de métier la moins chère avec tes sacs (fenêtre de métier ouverte)
-- 21 mémoire des nœuds de récolte vus (zone, position) ; clic = point boussole (TreeAI.Compass si présent)
-- 22 meilleur moment pour aller vendre        23 réparation prévue (quand + coût estimé)
-- 24 durabilité avant donjon                  25 valeur du butin de la session
-- Sortie : onglets « HV » et « Infos » (TreeAI:SetTabLines, bloc "Economy"), repli Suggest. Jamais le chat.
-- Mesures partagées : TreeAI.Predict (durabilité/h, places/kill, foyer) si présent. Prix HV : module Auction
-- (lecture seule). Forever : calculs hors combat. Lua 5.1, aucune globale.
local T = _G.TreeAI
if not T or not T.RegisterModule then return end
local API = T.API or {}

local M = T:RegisterModule("Economy", {
  title = "Économie",
  defaults = {
    enabled = true, vendorUp = true, sellFirst = true, goldHour = true, mail = true, profession = true,
    nodes = true, sellTime = true, repair = true, dungeonDur = true, lootValue = true, durMin = 60,
    repairPerPct = {},
  },
  charDefaults = { vendors = {}, nodes = {}, mailTo = "", craft = nil },
  options = {
    { key = "vendorUp", type = "toggle", label = "Améliorations chez les marchands", section = "Équipement" },
    { key = "repair", type = "toggle", label = "Réparation prévue (quand, coût)", section = "Équipement" },
    { key = "dungeonDur", type = "toggle", label = "Durabilité avant donjon", section = "Équipement" },
    { key = "durMin", type = "range", label = "Durabilité mini avant donjon (%)", min = 30, max = 90, step = 5, section = "Équipement" },
    { key = "sellFirst", type = "toggle", label = "Quoi vendre / jeter en premier", section = "Sacs" },
    { key = "sellTime", type = "toggle", label = "Meilleur moment pour aller vendre", section = "Sacs" },
    { key = "goldHour", type = "toggle", label = "Or/heure par activité", section = "Or" },
    { key = "lootValue", type = "toggle", label = "Valeur du butin de la session", section = "Or" },
    { key = "mail", type = "toggle", label = "Envoi de compos à un ami (bouton courrier)", section = "Métiers" },
    { key = "profession", type = "toggle", label = "Montée de métier la moins chère", section = "Métiers" },
    { key = "nodes", type = "toggle", label = "Mémoire des nœuds de récolte", section = "Métiers" },
  },
})
if T.MODULE_TAB and not T.MODULE_TAB.Economy then T.MODULE_TAB.Economy = "hv" end

local floor, max, min = math.floor, math.max, math.min
local function g(name) return rawget(_G, name) end
local function pass(ok, ...) if ok then return ... end return nil end
local function try(fn, ...) if type(fn) ~= "function" then return nil end return pass(pcall(fn, ...)) end
local function now() return try(g("GetTime")) or 0 end
local function epoch() local f = g("time") or (g("os") and g("os").time) return (f and f()) or 0 end
local function secret(v) local f = g("issecretvalue") return type(f) == "function" and f(v) and true or false end
local function opt(k) return M.db and M.db[k] ~= false end
local function inCombat() return T:InCombat() end
local function level() return tonumber(try(g("UnitLevel"), "player")) or 1 end
local function mod(n) local m = T.modules and T.modules[n] return (m and (not m.IsEnabled or m:IsEnabled())) and m or nil end
local function zoneName() return try(g("GetRealZoneText")) or try(g("GetZoneText")) or "" end
local function cut(s, n) s = tostring(s or "") if #s > n then return s:sub(1, n - 1) .. "…" end return s end
local function U() return T.ProUtil or {} end
local function money(c) local f = U().Money if f then return f(c) end return tostring(floor((c or 0) / 100)) .. " pa" end
local function dur(s) local f = U().Dur if f then return f(s) end return floor((s or 0) / 60) .. " min" end
local function itemInfo(x) if API.ItemInfo then return API.ItemInfo(x) end return try(g("GetItemInfo"), x) end
local function itemID(l) if type(l) == "number" then return l end return tonumber(type(l) == "string" and l:match("item:(%d+)") or nil) end
local function out(tab, lines)
  local f = U().Out
  if f then f(M, tab, lines, "Economy") elseif T.SetTabLines then pcall(T.SetTabLines, T, tab, lines, "Economy") end
end

-- valeur d'un objet : max(vendeur, HV × 0,95 si confiance suffisante) ; source "v" / "hv"
function M.Value(link)
  local id = itemID(link)
  local sell = tonumber((select(11, itemInfo(link)))) or 0
  local A = mod("Auction")
  if A and A.Stats and id then
    local ok, st = pcall(A.Stats, id)
    if ok and type(st) == "table" and tonumber(st.market) and (st.conf or 0) >= 0.3 then
      local hv = floor(st.market * 0.95)
      if hv > sell then return hv, "hv" end
    end
  end
  return sell, "v"
end

-- ------------------------------------------------------------------ session (18, 25)
local S
local function reset()
  S = { start = now(), lastMoney = nil, src = { loot = 0, quest = 0, vendor = 0, mail = 0, other = 0 }, spent = 0,
    lootItems = 0, top = {}, lootT = -100, questT = -100, merchant = false, mailOpen = false, free = {}, dur = {} }
end
reset()
M.ResetSession = reset
function M.Session() return S end

function M:PLAYER_MONEY()
  local m = tonumber(try(g("GetMoney")))
  if not m or secret(m) then return end
  local last = S.lastMoney
  S.lastMoney = m
  if not last then return end
  local d = m - last
  if d < 0 then S.spent = S.spent - d return end
  if d == 0 then return end
  local t = now()
  local k = (S.merchant and "vendor") or (S.mailOpen and "mail") or (t - S.questT < 3 and "quest")
    or (t - S.lootT < 5 and "loot") or "other"
  S.src[k] = S.src[k] + d
end
function M:LOOT_OPENED() S.lootT = now() end
function M:QUEST_TURNED_IN() S.questT = now() end
function M:MAIL_SHOW() S.mailOpen = true M.ShowMailButton(true) end
function M:MAIL_CLOSED() S.mailOpen = false M.ShowMailButton(false) end

local LOOT_SELF = { "^Vous recevez le butin", "^You receive loot", "^Vous recevez", "^You receive" }
function M.ParseLoot(msg)
  if type(msg) ~= "string" or secret(msg) then return nil end
  local mine = false
  for _, p in ipairs(LOOT_SELF) do if msg:find(p) then mine = true break end end
  if not mine then return nil end
  local link = msg:match("|c%x+|Hitem:.-|h.-|h|r") or msg:match("|Hitem:.-|h.-|h")
  if not link then return nil end
  local n = tonumber(msg:match("|h|r%s*[xX×](%d+)") or msg:match("[xX×](%d+)%.?%s*$")) or 1
  return link, n
end
function M:CHAT_MSG_LOOT(_, msg)
  local link, n = M.ParseLoot(msg)
  if not link then return end
  local v = M.Value(link) * n
  S.lootItems = S.lootItems + v
  local name = itemInfo(link) or link:match("%[(.-)%]") or "?"
  S.top[name] = (S.top[name] or 0) + v
end

function M.GoldLines()
  local lines = {}
  local h = max(1 / 60, (now() - S.start) / 3600)
  if opt("goldHour") and now() - S.start >= 300 then
    local s, total = S.src, 0
    for _, v in pairs(s) do total = total + v end
    local parts = {}
    for _, k in ipairs({ { "loot", "butin" }, { "quest", "quêtes" }, { "vendor", "ventes" }, { "mail", "HV/courrier" } }) do
      if s[k[1]] > 0 then parts[#parts + 1] = k[2] .. " " .. money(s[k[1]] / h) end
    end
    lines[#lines + 1] = { text = ("Or/h : %s net (%s)"):format(money((total - S.spent) / h),
      #parts > 0 and table.concat(parts, " · ") or "rien encore"), prio = 1,
      tooltip = { "Gagné : " .. money(total) .. " · dépensé : " .. money(S.spent), "Session : " .. dur(now() - S.start) } }
  end
  if opt("lootValue") and (S.lootItems > 0 or S.src.loot > 0) then
    local best, bv = nil, 0
    for n, v in pairs(S.top) do if v > bv then best, bv = n, v end end
    lines[#lines + 1] = { text = ("Butin de la session : %s (objets %s + or %s)%s"):format(money(S.lootItems + S.src.loot),
      money(S.lootItems), money(S.src.loot), best and (", top : " .. cut(best, 20)) or ""), prio = 1 }
  end
  return lines
end

-- ------------------------------------------------------------------ sacs (17, 22)
local QUEST_CLASS = 12
function M.SellList()
  if API.BagMemo then return API.BagMemo("Economy.SellList", M.SellListRaw) end   -- (LITE)
  return M.SellListRaw()
end
function M.SellListRaw()
  local list = {}
  if not API.IterBags then return list end
  for bag, slot, it in API.IterBags() do
    if it and it.itemID and not secret(it.itemID) then
      local _, _, quality, _, _, _, _, _, _, _, sell, classID = itemInfo(it.link or it.itemID)
      if classID ~= QUEST_CLASS and it.itemID ~= 6948 and (tonumber(sell) or 0) > 0 then
        local v, src = M.Value(it.link or it.itemID)
        list[#list + 1] = { bag = bag, slot = slot, id = it.itemID, name = itemInfo(it.link or it.itemID) or "?", count = it.count or 1,
          value = v * (it.count or 1), src = src, grey = (quality == 0) }
      end
    end
  end
  table.sort(list, function(a, b) if a.grey ~= b.grey then return a.grey end return a.value < b.value end)
  return list
end

local function hearthLeft()
  local P = mod("Predict")
  if P and P.HearthLeft then local ok, v = pcall(P.HearthLeft) if ok then return v end end
  local C = g("C_Container")
  local s, d = nil, nil
  if C and C.GetItemCooldown then s, d = try(C.GetItemCooldown, 6948) end
  if not s then s, d = try(g("GetItemCooldown"), 6948) end
  if (API.ItemCount and API.ItemCount(6948) or 0) <= 0 then return nil end
  if s and d and s > 0 and d > 0 then return max(0, s + d - now()) end
  return 0
end

-- places perdues par minute (Predict : places/kill × kills/h ; sinon nos relevés)
local function fillPerMin()
  local P = mod("Predict")
  if P and P.Rates then
    local ok, r = pcall(P.Rates)
    if ok and type(r) == "table" and r.slotsPerKill and r.killsPerHour then return r.slotsPerKill * r.killsPerHour / 60 end
  end
  local f = S.free
  if #f >= 2 then
    local a, b = f[1], f[#f]
    if b[1] - a[1] > 120 and a[2] > b[2] then return (a[2] - b[2]) / ((b[1] - a[1]) / 60) end
  end
  return nil
end
M.FillPerMin = fillPerMin

function M.BagLines()
  local lines = {}
  local free, total = 0, 0
  if API.FreeSlots then free, total = API.FreeSlots() end
  local sl = M.SellList()
  if opt("sellFirst") and free <= 4 and #sl > 0 then
    local names = {}
    for i = 1, min(3, #sl) do names[#names + 1] = ("%s (%s)"):format(cut(sl[i].name, 18), money(sl[i].value)) end
    lines[#lines + 1] = { text = "Vends/jette d'abord : " .. table.concat(names, ", "), prio = free <= 1 and 2 or 1,
      tooltip = "Classés par valeur par case (prix HV si connu, sinon vendeur)." }
  end
  if opt("sellTime") then
    local junk = 0
    for _, e in ipairs(sl) do if e.grey then junk = junk + e.value end end
    local rate = fillPerMin()
    local hl = hearthLeft()
    if free <= 2 then
      lines[#lines + 1] = { text = ("Va vendre maintenant : %d place(s), camelote %s%s"):format(free, money(junk),
        hl == 0 and " — pierre de foyer prête" or ""), prio = 2 }
    elseif rate and rate > 0 then
      local mins = free / rate
      local txt = ("Sacs pleins dans ~%s"):format(dur(mins * 60))
      if hl and hl <= mins * 60 then txt = txt .. " — pierre prête d'ici là : rentre vendre en rendant tes quêtes" end
      lines[#lines + 1] = { text = txt .. (" (camelote %s)"):format(money(junk)), prio = mins < 10 and 2 or 1 }
    end
  end
  return lines
end

-- ------------------------------------------------------------------ durabilité (23, 24)
local function durability()
  local sum, n, worst = 0, 0, nil
  for slot = 1, 18 do
    local c, m = try(g("GetInventoryItemDurability"), slot)
    if c and m and m > 0 and not secret(c) then
      local p = c * 100 / m
      sum, n = sum + p, n + 1
      if not worst or p < worst then worst = p end
    end
  end
  if n == 0 then return nil end
  return sum / n, worst
end
M.Durability = durability

local function durPerHour()
  local P = mod("Predict")
  if P and P.Rates then
    local ok, r = pcall(P.Rates)
    if ok and type(r) == "table" and tonumber(r.durPerHour) then return r.durPerHour end
  end
  local d = S.dur
  if #d >= 2 and d[#d][1] - d[1][1] > 300 and d[1][2] > d[#d][2] then
    return (d[1][2] - d[#d][2]) / ((d[#d][1] - d[1][1]) / 3600)
  end
  return nil
end

function M.RepairCost(avg)
  local lost = max(0, 100 - (avg or 100))
  local per = M.db.repairPerPct and tonumber(M.db.repairPerPct[tostring(floor(level() / 5))])
  per = per or (level() * level() * 0.9 + 10)  -- cuivre par % perdu (ordre de grandeur, affiné au marchand)
  return floor(lost * per)
end

--- Coût estimé de la réparation (pc) ou nil : pour l'infobulle de la ligne « répare » du module Inventaire.
function M.RepairEstimate()
  local avg = durability()
  if not avg or avg >= 99 then return nil end
  local c = M.RepairCost(avg)
  return (tonumber(c) and c > 0) and c or nil
end

function M.InDungeon()
  local inInst, kind = try(g("IsInInstance"))
  return inInst and (kind == "party" or kind == "raid")
end

function M.DurLines()
  local lines = {}
  local avg, worst = durability()
  if not avg then return lines end
  local cost = M.RepairCost(avg)
  if opt("repair") and avg < 99 then
    local r = durPerHour()
    local txt
    if worst <= 25 then txt = ("Répare dès que possible : un objet à %d %%"):format(floor(worst))
    elseif r and r > 0 then txt = ("Réparation dans ~%s (dur. %d %%)"):format(dur((worst - 25) / r * 3600), floor(avg))
    else txt = ("Durabilité %d %% (pire %d %%)"):format(floor(avg), floor(worst)) end
    lines[#lines + 1] = { text = txt .. ", coût estimé " .. money(cost), prio = worst <= 25 and 2 or 1, repair = true }
  end
  if opt("dungeonDur") then
    local P = T.Pro
    local near = P and P.DungeonsFor and #P.DungeonsFor(level()) > 0
    local grouped = (tonumber(try(g("GetNumGroupMembers"))) or 0) > 0
    local need = tonumber(M.db.durMin) or 60
    if avg < need and (M.InDungeon() or (grouped and near)) then
      lines[#lines + 1] = { text = ("Avant donjon : répare (dur. %d %%, ~%s)"):format(floor(avg), money(cost)),
        prio = M.InDungeon() and 2 or 1, repair = not M.InDungeon() }
    end
  end
  return lines
end

function M:MERCHANT_SHOW()
  S.merchant = true
  local avg = durability()
  local c, can = try(g("GetRepairAllCost"))
  if avg and avg < 98 and tonumber(c) and c > 0 and can then
    M.db.repairPerPct = M.db.repairPerPct or {}
    M.db.repairPerPct[tostring(floor(level() / 5))] = floor(c / (100 - avg))
  end
  pcall(M.ScanMerchant)
  M.Refresh(0.3)
end
function M:MERCHANT_CLOSED() S.merchant = false M.Refresh(0.5) end

-- ------------------------------------------------------------------ marchands (16)
function M.ScanMerchant()
  if not opt("vendorUp") then return end
  local n = tonumber(try(g("GetMerchantNumItems"))) or 0
  local Up = U().Upgrade
  if n == 0 or not Up then return end
  local npc = try(g("UnitName"), "npc") or "Marchand"
  local found = {}
  for i = 1, min(n, 200) do
    local link = try(g("GetMerchantItemLink"), i)
    if link then
      local d, slot = Up(link)
      if d then
        local _, _, price = try(g("GetMerchantItemInfo"), i)
        local cinfo = g("C_MerchantFrame") and g("C_MerchantFrame").GetItemInfo
        if not price and cinfo then local inf = try(cinfo, i) price = type(inf) == "table" and inf.price or nil end
        found[#found + 1] = { link = link, name = itemInfo(link) or link:match("%[(.-)%]") or "?", d = d, slot = slot, price = tonumber(price) or 0 }
      end
    end
  end
  table.sort(found, function(a, b) return a.d > b.d end)
  while #found > 5 do table.remove(found) end
  local v = M.char.vendors
  v[npc] = (#found > 0) and { zone = zoneName(), items = found, t = epoch(), level = level() } or nil
  local cnt = 0
  for k, e in pairs(v) do
    cnt = cnt + 1
    if (e.level or 0) < level() - 6 or cnt > 15 then v[k] = nil end
  end
end

function M:KnownUpgrades()
  local outl, Up = {}, U().Upgrade
  for npc, e in pairs(M.char.vendors or {}) do
    for _, it in ipairs(e.items or {}) do
      local d, slot
      if it.link and Up then d, slot = Up(it.link) end
      if d then outl[#outl + 1] = { npc = npc, zone = e.zone, name = it.name, d = d, slot = slot, price = it.price } end
    end
  end
  table.sort(outl, function(a, b) return a.d > b.d end)
  return outl
end

function M.VendorLines()
  local lines = {}
  if not opt("vendorUp") then return lines end
  local here = zoneName()
  for i, u in ipairs(M:KnownUpgrades()) do
    if i > 3 then break end
    local afford = (tonumber(try(g("GetMoney"))) or 0) >= (u.price or 0)
    lines[#lines + 1] = { text = ("Marchand %s%s : %s (+%d niv. %s, %s)%s"):format(cut(u.npc, 18),
      (u.zone and u.zone ~= here) and (" (" .. u.zone .. ")") or "", cut(u.name, 22), u.d, u.slot or "?", money(u.price),
      afford and "" or " — pas assez d'or"), prio = (u.d >= 5 and afford) and 2 or 1 }
  end
  return lines
end

-- ------------------------------------------------------------------ courrier (19)
local TRADE_CLASS = 7
function M.MailTarget()
  local n = M.char.mailTo
  if type(n) == "string" and n ~= "" then return n end
  local SQ = T.Squad
  if SQ and SQ.Members then
    local ok, list = pcall(SQ.Members, SQ)
    local me = try(g("UnitName"), "player")
    if ok and type(list) == "table" then for _, m in ipairs(list) do if m ~= me then return m end end end
  end
  return nil
end
function M.Mats()
  local list = {}
  if not API.IterBags then return list end
  for bag, slot, it in API.IterBags() do
    local classID = it and select(12, itemInfo(it.link or it.itemID))
    if classID == TRADE_CLASS and not it.locked then list[#list + 1] = { bag = bag, slot = slot, it = it } end
  end
  return list
end
local mailBtn
local function sendOpen()
  local f = g("SendMailFrame")
  return type(f) == "table" and f.IsShown and try(f.IsShown, f) and true or false
end
local function useItem(bag, slot)
  local C = g("C_Container")
  if C and C.UseContainerItem then C.UseContainerItem(bag, slot) return true end
  local f = g("UseContainerItem")
  if f then f(bag, slot) return true end
  return false
end
-- OnClick : une seule action (joindre UNE pile de compos) si permis ; sinon conseil texte
function M.MailClick()
  local to = M.MailTarget()
  if inCombat() then M:Suggest("mail", "Courrier : impossible en combat.", 1, { tab = "hv", ttl = 10 }) return end
  local mats = M.Mats()
  if #mats == 0 then M:Suggest("mail", "Courrier : aucune compo (artisanat) dans tes sacs.", 1, { tab = "hv", ttl = 15 }) return end
  if not sendOpen() then
    M:Suggest("mail", ("Courrier : ouvre l'onglet « Envoyer », puis reclique (%d compos)%s"):format(#mats,
      to and (" — pour " .. to) or ""), 1, { tab = "hv", ttl = 30 })
    return
  end
  local m = mats[1]
  if useItem(m.bag, m.slot) then
    M:Suggest("mail", ("Compo jointe (%d restantes). Destinataire : %s — tape-le puis clique « Envoyer »."):format(#mats - 1,
      to or "ton ami"), 1, { tab = "hv", ttl = 30 })
  else
    M:Suggest("mail", "Courrier : glisse tes compos dans le courrier (clic droit sur chaque pile).", 1, { tab = "hv", ttl = 30 })
  end
end
function M.ShowMailButton(show)
  if not opt("mail") then show = false end
  -- (3.9.2) only when a recipient was chosen (« compos » friend) — it was floating at the top for everyone
  if not (M.char and M.char.mailTo and M.char.mailTo ~= "") then show = false end
  -- (CHECKUP) jamais flottant : seulement collé à une fenêtre de courrier visible (sinon : rien)
  local mframe = g("MailFrame") or g("SendMailFrame")
  if show and not (mframe and (not mframe.IsShown or mframe:IsShown())) then show = false end
  if show and not mailBtn then
    local CF, parent = g("CreateFrame"), g("UIParent")
    if not CF or not parent then return end
    mailBtn = CF("Button", nil, parent, "UIPanelButtonTemplate")
    mailBtn:SetSize(170, 24)
    local mf = g("MailFrame") or g("SendMailFrame")
    mailBtn:SetPoint("TOPLEFT", mf, "TOPRIGHT", 6, -70)   -- anchored next to the mail window (not parented)
    mailBtn:SetClampedToScreen(true)
    mailBtn:SetScript("OnClick", function() pcall(M.MailClick) end)
    M.mailButton = mailBtn
  end
  if not mailBtn then return end
  if show and not inCombat() then
    local n = #M.Mats()
    if n == 0 then mailBtn:Hide() return end
    mailBtn:SetText(("Joindre compos (%d)"):format(n))
    mailBtn:Show()
  else
    mailBtn:Hide()
  end
end

-- ------------------------------------------------------------------ métiers (20)
local DIFF = { optimal = 1, medium = 0.75, easy = 0.3, trivial = 0, difficult = 1 }
local function unitPrice(link, name)
  if link then
    local v, src = M.Value(link)
    if src == "hv" then return floor(v / 0.95) end
    return max(1, v * 4)  -- achat chez un marchand ≈ 4× le prix de revente
  end
  return 100
end
-- recettes → { name, chance, makeable, cost } ; API classique (GetTradeSkillInfo) ou C_TradeSkillUI
function M.Recipes()
  local list = {}
  local n = tonumber(try(g("GetNumTradeSkills"))) or 0
  if n > 0 then
    for i = 1, min(n, 400) do
      local name, kind, avail = try(g("GetTradeSkillInfo"), i)
      local chance = DIFF[kind or ""]
      if name and chance and chance > 0 then
        local cost = 0
        for j = 1, tonumber(try(g("GetTradeSkillNumReagents"), i)) or 0 do
          local rname, _, need, have = try(g("GetTradeSkillReagentInfo"), i, j)
          local link = try(g("GetTradeSkillReagentItemLink"), i, j)
          need, have = tonumber(need) or 1, tonumber(have) or 0
          local miss = max(0, need - have)
          local p = unitPrice(link, rname)
          local sell = link and (tonumber((select(11, itemInfo(link)))) or 0) or 0
          cost = cost + miss * p + min(need, have) * sell
        end
        list[#list + 1] = { name = name, chance = chance, makeable = tonumber(avail) or 0, cost = cost, kind = kind }
      end
    end
    return list
  end
  local C = g("C_TradeSkillUI")
  if C and C.GetAllRecipeIDs and C.GetRecipeInfo then
    local ids = try(C.GetAllRecipeIDs) or {}
    local REL = { [0] = 1, [1] = 0.75, [2] = 0.3, [3] = 0 }
    for i, id in ipairs(ids) do
      if i > 400 then break end
      local info = try(C.GetRecipeInfo, id)
      local chance = type(info) == "table" and info.learned and REL[info.relativeDifficulty or 3] or 0
      if chance > 0 then
        local cost = 0
        local sch = C.GetRecipeSchematic and try(C.GetRecipeSchematic, id, false)
        for _, slot in ipairs(type(sch) == "table" and sch.reagentSlotSchematics or {}) do
          local r = slot.reagents and slot.reagents[1]
          if r and r.itemID then
            local need = tonumber(slot.quantityRequired) or 1
            local have = API.ItemCount and API.ItemCount(r.itemID) or 0
            cost = cost + max(0, need - have) * unitPrice(r.itemID) + min(need, have) * (tonumber((select(11, itemInfo(r.itemID)))) or 0)
          end
        end
        list[#list + 1] = { name = info.name, chance = chance, makeable = tonumber(info.numAvailable) or 0, cost = cost }
      end
    end
  end
  return list
end
function M.CheapestSkillUp()
  local list = M.Recipes()
  for _, r in ipairs(list) do r.perPoint = (r.cost + 1) / r.chance end
  table.sort(list, function(a, b)
    if (a.makeable > 0) ~= (b.makeable > 0) then return a.makeable > 0 end
    return a.perPoint < b.perPoint
  end)
  return list[1], list[2]
end
function M:TRADE_SKILL_SHOW() M.Refresh(0.5) M.TradeOpen = true end
function M:TRADE_SKILL_UPDATE() M.Refresh(0.5) end
function M:TRADE_SKILL_CLOSE() M.TradeOpen = false end
local function craftScan()
  if not opt("profession") or not M.TradeOpen then return end
  local a, b = M.CheapestSkillUp()
  if not a then return end
  local prof = try(g("GetTradeSkillLine")) or "Métier"
  local function one(r) return ("« %s » (%s/point%s)"):format(cut(r.name, 24), money(r.perPoint),
    r.makeable > 0 and (", " .. r.makeable .. " avec tes sacs") or "") end
  M.char.craft = { prof = prof, text = one(a) .. (b and (" puis " .. one(b)) or ""), t = epoch() }
end

-- ------------------------------------------------------------------ nœuds (21)
local GATHER = { "herbori", "minage", "minière", "miniere", "mining", "herb gathering", "fouille", "pêche" }
local function isGather(spell)
  local s = type(spell) == "string" and spell:lower() or ""
  for _, k in ipairs(GATHER) do if s:find(k, 1, true) then return true end end
  return false
end
local function myPos()
  local C = g("C_Map")
  if not (C and C.GetBestMapForUnit) then return nil end
  local map = try(C.GetBestMapForUnit, "player")
  local p = map and C.GetPlayerMapPosition and try(C.GetPlayerMapPosition, map, "player")
  if type(p) ~= "table" then return nil end
  local x, y
  if p.GetXY then x, y = try(p.GetXY, p) else x, y = p.x, p.y end
  if not x or secret(x) then return nil end
  return map, x, y
end
function M.RecordNode(name)
  if type(name) ~= "string" or name == "" or secret(name) then return end
  local map, x, y = myPos()
  local z = zoneName()
  local list = M.char.nodes
  for _, e in ipairs(list) do
    if e.n == name and e.m == map and x and e.x and math.abs(e.x - x) < 0.01 and math.abs(e.y - y) < 0.01 then
      e.t, e.c = epoch(), (e.c or 1) + 1
      return e
    end
  end
  local e = { n = name, z = z, m = map, x = x and floor(x * 1000) / 1000, y = y and floor(y * 1000) / 1000, t = epoch(), c = 1 }
  list[#list + 1] = e
  while #list > 300 do table.remove(list, 1) end
  return e
end
function M:UNIT_SPELLCAST_SENT(_, unit, target, _, spellID)
  if unit ~= "player" or not opt("nodes") then return end
  local spell = API.SpellName and API.SpellName(spellID) or nil
  if isGather(spell) or isGather(tostring(target)) then M.RecordNode(target) end
end
function M.NodeLines()
  local lines = {}
  if not opt("nodes") then return lines end
  local z = zoneName()
  local map, x, y = myPos()
  local counts, nearest, nd = {}, nil, nil
  for _, e in ipairs(M.char.nodes) do
    if e.z == z then
      counts[e.n] = (counts[e.n] or 0) + 1
      if x and e.x and e.m == map then
        local d = (e.x - x) ^ 2 + (e.y - y) ^ 2
        if not nd or d < nd then nearest, nd = e, d end
      end
    end
  end
  local parts = {}
  for n, c in pairs(counts) do parts[#parts + 1] = cut(n, 18) .. " ×" .. c end
  table.sort(parts)
  if #parts == 0 then return lines end
  local l = { text = ("Nœuds connus ici : %s%s"):format(table.concat(parts, ", ", 1, min(#parts, 4)),
    nearest and (" — le plus proche : %s (%d, %d)"):format(cut(nearest.n, 16), floor(nearest.x * 100), floor(nearest.y * 100)) or ""), prio = 1 }
  if nearest then
    l.onClick = function()
      local C = T.Compass
      if C and C.SetWaypoint then pcall(C.SetWaypoint, nearest.x, nearest.y, nearest.n, nearest.m) end
    end
    l.tooltip = "Clic : point sur la boussole TreeAI (si présente)."
  end
  lines[1] = l
  return lines
end

-- ------------------------------------------------------------------ assemblage
local function sample()
  local t = now()
  if API.FreeSlots then
    local f = API.FreeSlots()
    local list = S.free
    if f and (not list[#list] or list[#list][2] ~= f) then
      if list[#list] and f > list[#list][2] then S.free = {} list = S.free end  -- vendu : on repart
      list[#list + 1] = { t, f }
      while #list > 40 do table.remove(list, 1) end
    end
  end
  local avg = durability()
  if avg then
    local d = S.dur
    if d[#d] and avg > d[#d][2] + 1 then S.dur = {} d = S.dur end
    if not d[#d] or d[#d][2] ~= avg then d[#d + 1] = { t, avg } while #d > 40 do table.remove(d, 1) end end
  end
end

local lastUpdate = 0
function M.Update(force)
  if not M:IsEnabled() or inCombat() then return end  -- Forever : calculs hors combat
  if not force and now() - lastUpdate < 2 then return end
  lastUpdate = now()
  if S.lastMoney == nil then S.lastMoney = tonumber(try(g("GetMoney"))) end
  sample()
  craftScan()
  local hv, info = {}, {}
  local function append(dst, src) for _, l in ipairs(src) do dst[#dst + 1] = l end end
  append(hv, M.VendorLines())
  append(hv, M.BagLines())
  append(hv, M.GoldLines())
  if opt("profession") and M.char.craft then
    hv[#hv + 1] = { text = ("%s : monte avec %s"):format(M.char.craft.prof, M.char.craft.text), prio = 1 }
  end
  append(info, M.DurLines())
  append(info, M.NodeLines())
  M.lines = { hv = hv, info = info }
  out("hv", hv)
  -- une seule ligne « répare » : celle du module Inventaire (« Maintenant ») ; le coût estimé passe dans son
  -- infobulle (M.RepairEstimate) ; M.lines garde tout (tests, résumé)
  local inv = T._suggestions and T._suggestions["Inventory:inv_repair"]
  if inv then
    local shown = {}
    for _, l in ipairs(info) do if not l.repair then shown[#shown + 1] = l end end
    out("info", shown)
  else
    out("info", info)
  end
end
local pending = false
function M.Refresh(delay)
  if pending then return end
  pending = true
  T:After(delay or 0.5, function() pending = false pcall(M.Update, true) end)
end
function M:AutoRefresh() M.Update() end

function M:WriteSummary()
  local s = T:Summary()
  if type(s) ~= "table" then return end
  local h = max(1 / 60, (now() - S.start) / 3600)
  local total = 0
  for _, v in pairs(S.src) do total = total + v end
  local avg = durability()
  s.economy = { goldPerHour = floor((total - S.spent) / h), sources = S.src, spent = S.spent, lootValue = S.lootItems + S.src.loot,
    durability = avg and floor(avg), repairCost = avg and M.RepairCost(avg), upgrades = #M:KnownUpgrades(),
    nodes = #M.char.nodes, updated = epoch() }
end

function M:OnEnable()
  for _, ev in ipairs({ "PLAYER_MONEY", "LOOT_OPENED", "QUEST_TURNED_IN", "MAIL_SHOW", "MAIL_CLOSED", "CHAT_MSG_LOOT",
    "MERCHANT_SHOW", "MERCHANT_CLOSED", "TRADE_SKILL_SHOW", "TRADE_SKILL_UPDATE", "TRADE_SKILL_CLOSE",
    "UNIT_SPELLCAST_SENT", "UPDATE_INVENTORY_DURABILITY", "BAG_UPDATE_DELAYED", "PLAYER_ENTERING_WORLD" }) do
    M:RegisterEvent(ev, M[ev] or function() M.Refresh(1) end)
  end
  M:On("SUMMARY", function() pcall(M.WriteSummary, M) end)
  M:On("COMBAT_START", function() if mailBtn then mailBtn:Hide() end end)
  if T.RegisterSlash then
    T:RegisterSlash("compos", function(rest)
      rest = tostring(rest or ""):gsub("^%s+", ""):gsub("%s+$", "")
      M.char.mailTo = rest
      M:Print(rest ~= "" and ("Compos : destinataire = " .. rest) or "Compos : destinataire = premier ami du groupe")
    end, "compos <Nom> : ami à qui envoyer tes compos (bouton à la boîte aux lettres)")
    T:RegisterSlash("eco", function() M.Update(true) end, "économie : recalcule (ventes, réparation, or/h…)")
  end
  M.Refresh(2)
end
function M:OnDisable()
  if mailBtn then mailBtn:Hide() end
  if T.SetTabLines then pcall(T.SetTabLines, T, "hv", {}, "Economy") pcall(T.SetTabLines, T, "info", {}, "Economy") end
end
function M:OnOptionChanged() M.Refresh(0.2) end
TreeAI.Economy = M
