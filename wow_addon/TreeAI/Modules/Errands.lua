-- TreeAI — module « Guichets » (agent NEXT10) : une ligne utile quand une fenêtre du jeu est ouverte, rien sinon.
--  1 boîte aux lettres : lettres, argent et objets à récupérer, lettres de l'HV (« Tout ouvrir » reste TON clic
--    sur le bouton de Blizzard ; l'addon n'ouvre, ne prend, ne paie rien).
--  2 courrier à risque : contre-remboursement (somme, expéditeur) et lettres avec objets qui expirent ;
--    l'échéance est gardée et rappelée au login si tu n'es pas repassé à une boîte.
--  3 maître de vol : « pierre de foyer ou vol » vers ton auberge (temps de recharge de la pierre + prix du vol).
--  4 maître de classe : prix exact de ce qui est à apprendre ; sans assez d'or, quoi prendre d'abord. Le prix des
--    sorts du niveau suivant (lu dans la même fenêtre) est gardé : « garde 3 po 20 pour le niveau 30 » avant d'y être,
--    et « va voir ton maître de classe » au passage de niveau (prix réel, plus de table approximative).
--  5 appel « prêt » (ready check) : ce qui te manque avant de répondre (PV, mana, sacs, munitions, familier,
--    réparation) ; rien n'est dit si tout va bien. La réponse reste au joueur.
--  6 limite d'instances : entrées de la dernière heure (5 par heure), place libre dans X min (onglet « Groupe »).
--  7 courrier (option, coupée par défaut) : bouton « Joindre compos » collé à la fenêtre d'envoi ; UN clic = UNE pile
--    de compos jointe ; le destinataire et « Envoyer » restent à toi.
-- Chaque ligne s'efface à la fermeture de la fenêtre. Aucune fonction protégée, aucun cadre Blizzard touché.
-- Lua 5.1, aucune globale.
local T = _G.TreeAI
if not (T and T.RegisterModule) then return end

local M = T:RegisterModule("Errands", {
  title = "Guichets",
  defaults = { enabled = true, mail = true, taxi = true, trainer = true, ready = true, instances = true, mats = false, inst = {} },
  charDefaults = { mail = false, trainer = false, nextTrain = false, trainPending = false },
  options = {
    { key = "mail", type = "toggle", label = "Boîte aux lettres : à récupérer, contre-remboursement, expiration", section = "Fenêtres" },
    { key = "taxi", type = "toggle", label = "Maître de vol : pierre de foyer ou vol", section = "Fenêtres" },
    { key = "trainer", type = "toggle", label = "Entraîneur : prix exact et quoi prendre d'abord", section = "Fenêtres" },
    { key = "ready", type = "toggle", label = "Appel « prêt » : ce qui te manque", section = "Groupe" },
    { key = "instances", type = "toggle", label = "Limite de 5 instances par heure", section = "Groupe" },
    { key = "mats", type = "toggle", label = "Courrier : bouton « Joindre compos » (une pile par clic)", section = "Fenêtres" },
  },
})
if T.TOPIC_IDS then
  T.TOPIC_IDS["Errands:trainer"] = "train"
  T.TOPIC_IDS["Errands:ready"] = "readycheck"
end

local floor, max, min = math.floor, math.max, math.min
local fmt, tconcat, tsort = string.format, table.concat, table.sort
local HEARTH = 6948
local INST_LIMIT, INST_WINDOW = 5, 3600
local EXPIRE_DAYS = 3

local function g(name) return rawget(_G, name) end
local function try(fn, ...)
  if type(fn) ~= "function" then return nil end
  local ok, a, b, c, d, e, f, h, i, j, k = pcall(fn, ...)
  if ok then return a, b, c, d, e, f, h, i, j, k end
end
local function sv(v)
  local is = g("issecretvalue")
  if v ~= nil and is and try(is, v) then return nil end
  return v
end
local function num(v) return tonumber(sv(v)) end
local function now() return num(try(g("time"))) or 0 end
local function clock() return num(try(g("GetTime"))) or 0 end
local function opt(k) return not (M.db and M.db[k] == false) end
local Fmt = T.Fmt or {}
local function money(c) return Fmt.Money and Fmt.Money(c) or (floor((c or 0) / 10000) .. " po") end
local function dur(s) return Fmt.Time and Fmt.Time(s) or (floor((s or 0) / 60) .. " min") end
local function short(s, len)
  s = tostring(s or ""):gsub("|c%x%x%x%x%x%x%x%x", ""):gsub("|r", ""):gsub("[%[%]|]", "")
  if T.TextLen and T.TextLen(s) > len then s = s:sub(1, len) end
  return s
end
local function plural(k, one, many) return k .. " " .. (k > 1 and many or one) end
-- clic sur une ligne = la ranger jusqu'à la prochaine ouverture de la fenêtre
local hidden = {}
local function dismiss(id) return function() hidden[id] = true; M:Clear(id) end end

-- ------------------------------------------------------------------ 1-2 courrier
--- lettres = { {sender, subject, money, cod, days, items, ah}, ... } -> résumé (fonction pure, testée)
function M.MailDigest(letters)
  local d = { n = 0, money = 0, items = 0, ah = 0, ahMoney = 0, cod = {}, expiring = 0, soonest = nil }
  for _, l in ipairs(letters or {}) do
    d.n = d.n + 1
    local m, it = tonumber(l.money) or 0, tonumber(l.items) or 0
    d.money = d.money + m
    if it > 0 then d.items = d.items + 1 end
    if l.ah then d.ah, d.ahMoney = d.ah + 1, d.ahMoney + m end
    if (tonumber(l.cod) or 0) > 0 then d.cod[#d.cod + 1] = l end
    local days = tonumber(l.days)
    if days and (it > 0 or m > 0) then
      if days < EXPIRE_DAYS then d.expiring = d.expiring + 1 end
      if not d.soonest or days < d.soonest then d.soonest = days end
    end
  end
  return d
end

function M.MailLines(d, openAll)
  local out = {}
  if not d or d.n == 0 then return out end
  if d.ah > 0 then
    out[#out + 1] = { id = "mail", prio = 2, text = fmt("%s de l'HV%s : %s", plural(d.ah, "lettre", "lettres"),
      d.ahMoney > 0 and (", " .. money(d.ahMoney)) or "", openAll and "clique « Tout ouvrir »" or "ouvre-les") }
  elseif d.money > 0 or d.items > 0 then
    local what = {}
    if d.money > 0 then what[#what + 1] = money(d.money) end
    if d.items > 0 then what[#what + 1] = plural(d.items, "lettre avec objets", "lettres avec objets") end
    out[#out + 1] = { id = "mail", prio = 1, text = "Courrier à récupérer : " .. tconcat(what, ", ") }
  end
  for i, l in ipairs(d.cod) do
    if i > 2 then break end
    out[#out + 1] = { id = "cod" .. i, prio = 2, text = fmt("Contre-remboursement : %s demandés par %s, vérifie avant de payer",
      money(l.cod), short(l.sender ~= "" and l.sender or "un inconnu", 24)) }
  end
  if d.expiring > 0 then
    out[#out + 1] = { id = "expire", prio = 2, text = fmt("%s bientôt périmée%s (moins de %d jours) : ouvre-%s",
      plural(d.expiring, "lettre", "lettres"), d.expiring > 1 and "s" or "", EXPIRE_DAYS, d.expiring > 1 and "les" or "la") }
  end
  return out
end

function M.ReadInbox()
  local k = min(100, num(try(g("GetInboxNumItems"))) or 0)
  local A = T.modules and T.modules.Auction
  local letters = {}
  for i = 1, k do
    local _, _, sender, subject, m, cod, days, items = try(g("GetInboxHeaderInfo"), i)
    if subject or sender then
      local ah = false
      if A and type(A.MailKind) == "function" then ah = try(A.MailKind, subject) and true or false end
      if not ah and type(sender) == "string" then
        local low = sender:lower()
        ah = low:find("hôtel des ventes", 1, true) or low:find("auction house", 1, true) or false
        ah = ah and true or false
      end
      letters[#letters + 1] = { sender = short(sender or "", 24), subject = subject, money = num(m) or 0, cod = num(cod) or 0,
        days = num(days), items = num(items) or 0, ah = ah }
    end
  end
  return letters
end

function M.UpdateMail()
  if not opt("mail") or not M.mailOpen then return end
  local d = M.MailDigest(M.ReadInbox())
  if M.char then
    M.char.mail = d.soonest and { expireAt = now() + floor(d.soonest * 86400), seen = now() } or false
  end
  local openAll = g("OpenAllMail") ~= nil
  local lines = M.MailLines(d, openAll)
  local shown = {}
  for _, l in ipairs(lines) do
    if hidden[l.id] then l.skip = true end
    shown[l.id] = not l.skip
  end
  for _, l in ipairs(lines) do
    if not l.skip then
      M:Suggest(l.id, l.text, l.prio, { ttl = 0, tab = "hv", sound = false, onClick = dismiss(l.id),
        tooltip = { "Boîte aux lettres", "L'addon ne prend rien : c'est ton clic sur la lettre ou le bouton du jeu", "Clic : ranger" } })
    end
  end
  for _, id in ipairs({ "mail", "cod1", "cod2", "expire" }) do if not shown[id] then M:Clear(id) end end
end

function M.ClearMail()
  M.mailOpen = false
  for _, id in ipairs({ "mail", "cod1", "cod2", "expire" }) do M:Clear(id) end
end

--- au login : objets qui vont expirer dans une boîte où tu n'es pas repassé
function M.MailReminder()
  local m = M.char and M.char.mail
  if not (opt("mail") and type(m) == "table" and m.expireAt) then return end
  local left = m.expireAt - now()
  if left <= 0 or left > EXPIRE_DAYS * 86400 then return end
  M:Suggest("expireLogin", "Passe à une boîte aux lettres : des objets expirent dans " .. dur(left), 2,
    { ttl = 600, tab = "hv", sound = false, onClick = dismiss("expireLogin"), tooltip = { "Clic : ranger" } })
end

-- ------------------------------------------------------------------ 7 « Joindre compos » (option)
local matsBtn
local function sendOpen()
  local f = g("SendMailFrame")
  return type(f) == "table" and f.IsShown and try(f.IsShown, f) and true or false
end
--- compos d'artisanat (classe d'objet 7) non verrouillées dans les sacs : { {bag, slot} }
function M.Mats()
  local list, API = {}, T.API or {}
  if not API.IterBags then return list end
  for bag, slot, it in API.IterBags() do
    local classID = it and select(12, try(API.ItemInfo, it.link or it.itemID))
    if classID == 7 and not it.locked then list[#list + 1] = { bag = bag, slot = slot } end
  end
  return list
end
--- clic du joueur : joint UNE pile (UseContainerItem, onglet « Envoyer » ouvert, hors combat) — jamais autre chose
function M.MatsClick()
  if try(g("InCombatLockdown")) or not sendOpen() then return false end
  local m = M.Mats()[1]
  if not m then return false end
  local C = g("C_Container")
  local use = (C and C.UseContainerItem) or g("UseContainerItem")
  if use then pcall(use, m.bag, m.slot) end
  if T.After then T:After(0.3, function() pcall(M.ShowMats, true) end) end
  return true
end
function M.ShowMats(show)
  if not opt("mats") or try(g("InCombatLockdown")) then show = false end
  local mf = g("MailFrame")
  if show and not (mf and (not mf.IsShown or mf:IsShown())) then show = false end
  if show and not matsBtn then
    local CF, parent = g("CreateFrame"), g("UIParent")
    if not (CF and parent) then return end
    matsBtn = CF("Button", nil, parent, "UIPanelButtonTemplate")
    matsBtn:SetSize(170, 24)
    matsBtn:SetPoint("TOPLEFT", mf, "TOPRIGHT", 6, -70)   -- ancré à la fenêtre du courrier (jamais son enfant)
    if matsBtn.SetClampedToScreen then matsBtn:SetClampedToScreen(true) end
    matsBtn:SetScript("OnClick", function() pcall(M.MatsClick) end)
    matsBtn:SetScript("OnEnter", function(self)
      local tt = g("GameTooltip")
      if not tt then return end
      tt:SetOwner(self, "ANCHOR_RIGHT")
      tt:AddLine("Joindre compos", 1, 1, 1)
      tt:AddLine("Onglet « Envoyer » ouvert : chaque clic joint une pile de compos.", 0.85, 0.85, 0.85, true)
      tt:AddLine("Le destinataire et « Envoyer » restent à toi.", 0.85, 0.85, 0.85, true)
      tt:Show()
    end)
    matsBtn:SetScript("OnLeave", function() local tt = g("GameTooltip") if tt then tt:Hide() end end)
  end
  if not matsBtn then return end
  local n = show and #M.Mats() or 0
  if n > 0 then matsBtn:SetText(fmt("Joindre compos (%d)", n)) matsBtn:Show() else matsBtn:Hide() end
end

-- ------------------------------------------------------------------ 3 pierre de foyer ou vol
function M.HearthLeft()
  local API = T.API or {}
  if (try(API.ItemCount, HEARTH) or 0) <= 0 then return nil end
  local CC, CI = g("C_Container"), g("C_Item")
  local start, d = try(CC and CC.GetItemCooldown, HEARTH)
  if not start then start, d = try(CI and CI.GetItemCooldown, HEARTH) end
  if not start then start, d = try(g("GetItemCooldown"), HEARTH) end
  start, d = num(start), num(d)
  if not (start and d) then return nil end
  if start <= 0 or d <= 1.5 then return 0 end
  return max(0, start + d - clock())
end

function M.TaxiNodes()
  local out = {}
  local NT, NN, TT, TC = g("NumTaxiNodes"), g("TaxiNodeName"), g("TaxiNodeGetType"), g("TaxiNodeCost")
  local k = num(try(NT)) or 0
  for i = 1, min(k, 120) do
    local name, kind = try(NN, i), try(TT, i)
    if type(name) == "string" and kind == "REACHABLE" then
      out[#out + 1] = { name = name, cost = num(try(TC, i)) }
    end
  end
  if #out == 0 then
    local TM, MP = g("C_TaxiMap"), g("C_Map")
    local map = MP and try(MP.GetBestMapForUnit, "player")
    local nodes = TM and map and try(TM.GetAllTaxiNodes, map)
    for _, nd in ipairs(type(nodes) == "table" and nodes or {}) do
      if type(nd) == "table" and type(nd.name) == "string" and nd.state == 1 then
        out[#out + 1] = { name = nd.name, cost = nd.slotIndex and num(try(TC, nd.slotIndex)) or nil }
      end
    end
  end
  return out
end

--- nœud de vol qui mène à l'auberge de ta pierre (fonction pure)
function M.MatchBind(nodes, bind)
  if type(bind) ~= "string" or bind == "" then return nil end
  local b = bind:lower()
  for _, nd in ipairs(nodes or {}) do
    local full = nd.name:lower()
    local head = full:match("^([^,]+)") or full
    head = head:gsub("%s+$", "")
    if #head >= 4 and (b:find(head, 1, true) or full:find(b, 1, true)) then return nd end
  end
end

function M.HearthOrFly(left, node, bind)
  if not node then return nil end
  local cost = node.cost and node.cost > 0 and money(node.cost) or nil
  local where = short(bind, 30)
  if left == 0 then
    return fmt("Pour %s, ta pierre de foyer est prête : garde %s", where, cost or "le prix du vol"), 2
  elseif left and left > 0 then
    return fmt("Pierre de foyer dans %s : vole vers %s%s", dur(left), where, cost and (" pour " .. cost) or ""), 1
  end
end

function M.UpdateTaxi()
  if not opt("taxi") then return end
  local bind = try(g("GetBindLocation"))
  local text, prio = M.HearthOrFly(M.HearthLeft(), M.MatchBind(M.TaxiNodes(), bind), bind)
  if text then
    M:Suggest("taxi", text, prio, { ttl = 0, tab = "now", sound = false, onClick = dismiss("taxi"),
      tooltip = { "Pierre de foyer ou vol", "Tu choisis ta destination sur la carte du maître de vol", "Clic : ranger" } })
  else
    M:Clear("taxi")
  end
end

-- ------------------------------------------------------------------ 4 entraîneur
--- services = { {name, cost}, ... } (disponibles), have = cuivre -> texte, prio (fonction pure)
function M.TrainerPlan(services, have, nextLevel)
  local k = #(services or {})
  if k == 0 then
    if nextLevel then return fmt("Rien de neuf ici : prochains sorts au niveau %d", nextLevel), 1 end
    return nil
  end
  local total = 0
  for _, s in ipairs(services) do total = total + (tonumber(s.cost) or 0) end
  have = tonumber(have) or 0
  if total <= have then
    return fmt("Entraîneur : %s pour %s, tu as assez, apprends tout", plural(k, "sort", "sorts"), money(total)), 2
  end
  local sorted = {}
  for _, s in ipairs(services) do sorted[#sorted + 1] = s end
  tsort(sorted, function(a, b)
    if (a.cost or 0) ~= (b.cost or 0) then return (a.cost or 0) < (b.cost or 0) end
    return tostring(a.name) < tostring(b.name)
  end)
  local pick, spent = {}, 0
  for _, s in ipairs(sorted) do
    if spent + (s.cost or 0) <= have then pick[#pick + 1] = s; spent = spent + (s.cost or 0) end
  end
  if #pick == 0 then
    return fmt("Entraîneur : %s pour %s, il te manque %s", plural(k, "sort", "sorts"), money(total), money(total - have)), 2
  end
  local names = {}
  for i, s in ipairs(pick) do if i <= 2 then names[#names + 1] = short(s.name, 22) end end
  local more = #pick > 2 and fmt(" +%d", #pick - 2) or ""
  return fmt("Apprends d'abord %s%s (%s), il manque %s pour le reste", tconcat(names, ", "), more, money(spent),
    money(total - have)), 2
end

function M.ReadTrainer()
  if try(g("IsTradeskillTrainer")) then return nil end
  local k = num(try(g("GetNumTrainerServices"))) or 0
  local avail, nextLevel, later = {}, nil, {}
  local me = num(try(g("UnitLevel"), "player")) or 1
  for i = 1, min(k, 200) do
    local name, _, cat = try(g("GetTrainerServiceInfo"), i)
    if type(name) == "string" then
      if cat == "available" then
        avail[#avail + 1] = { name = name, cost = num(try(g("GetTrainerServiceCost"), i)) or 0 }
      elseif cat == "unavailable" then
        local req = num(try(g("GetTrainerServiceLevelReq"), i))
        if req and req > me then
          if not nextLevel or req < nextLevel then nextLevel = req end
          later[#later + 1] = { req = req, cost = num(try(g("GetTrainerServiceCost"), i)) or 0 }
        end
      end
    end
  end
  local nextCost = 0
  for _, l in ipairs(later) do if l.req == nextLevel then nextCost = nextCost + l.cost end end
  return avail, nextLevel, nextCost
end

--- rappel « garde ton or » (pur) : prochain palier lu chez le maître, niveau, or -> texte ou nil
function M.SaveText(nt, lvl, have)
  if type(nt) ~= "table" or not tonumber(nt.level) or not tonumber(nt.cost) or nt.cost <= 0 then return nil end
  lvl, have = tonumber(lvl) or 0, tonumber(have) or 0
  if lvl < nt.level - 2 or lvl >= nt.level or have >= nt.cost then return nil end
  return fmt("Garde %s pour ton maître de classe au niveau %d : tu as %s", money(nt.cost), nt.level, money(have))
end
function M.UpdateSave()
  local nt = M.char and M.char.nextTrain
  local text = opt("trainer") and M.SaveText(nt, num(try(g("UnitLevel"), "player")), num(try(g("GetMoney"))))
  if T.SetTabLines then
    pcall(T.SetTabLines, T, "info", text and { { text = text, prio = 1,
      tooltip = { "Prix des sorts de ce niveau, lu dans la fenêtre de ton maître de classe à ta dernière visite." } } } or {},
      "Errands")
  end
end

--- passage de niveau : nouveaux sorts chez le maître de classe (prix réel s'il a été lu)
function M.TrainerReminder()
  local lvl = num(try(g("UnitLevel"), "player")) or 1
  local p = M.char and M.char.trainPending
  if not (opt("trainer") and p) or try(g("InCombatLockdown")) then return end
  local nt = M.char.nextTrain
  local known = type(nt) == "table" and tonumber(nt.level) and nt.level <= lvl and tonumber(nt.cost) and nt.cost > 0
  if not known and p % 2 == 1 then return end   -- sans prix lu : seulement aux niveaux pairs (nouveaux sorts Classic)
  local text = known and fmt("Va voir ton maître de classe : nouveaux sorts pour %s", money(nt.cost))
    or fmt("Va voir ton maître de classe : nouveaux sorts au niveau %d", p)
  local resting = try(g("IsResting")) and true or false
  M:Suggest("trainer", text, resting and 2 or 1, { ttl = 0, tab = "now", topic = "train", sound = false,
    onClick = dismiss("trainer"),
    tooltip = { known and "Prix lu dans la fenêtre de ton maître de classe." or "Le prix exact s'affichera dans sa fenêtre.",
      "Clic : ranger" } })
end

function M.UpdateTrainer()
  if not opt("trainer") then return end
  local avail, nextLevel, nextCost = M.ReadTrainer()
  if not avail then M:Clear("trainer") return end
  local text, prio = M.TrainerPlan(avail, num(try(g("GetMoney"))), nextLevel)
  if M.char then
    M.char.trainPending = false
    if #avail > 0 then
      local total = 0
      for _, s in ipairs(avail) do total = total + s.cost end
      M.char.trainer = { level = num(try(g("UnitLevel"), "player")), cost = total, n = #avail, at = now() }
    end
    if nextLevel and (nextCost or 0) > 0 then M.char.nextTrain = { level = nextLevel, cost = nextCost, at = now() } end
    M.UpdateSave()
  end
  if text then
    M:Suggest("trainer", text, prio, { ttl = 0, tab = "now", topic = "train", sound = false, onClick = dismiss("trainer"),
      tooltip = { "Entraîneur", "Prix lus dans la fenêtre de l'entraîneur ; c'est toi qui cliques « Apprendre »", "Clic : ranger" } })
  else
    M:Clear("trainer")
  end
end

-- ------------------------------------------------------------------ 5 appel « prêt »
function M.ReadyGaps()
  local gaps = {}
  local function pct(cur, mx)
    cur, mx = num(cur), num(mx)
    if cur and mx and mx > 0 then return floor(cur * 100 / mx) end
  end
  local hp = pct(try(g("UnitHealth"), "player"), try(g("UnitHealthMax"), "player"))
  if hp and hp < 60 then gaps[#gaps + 1] = "PV " .. hp .. " %" end
  local ptype = num(try(g("UnitPowerType"), "player"))
  if ptype == 0 then
    local mp = pct(try(g("UnitPower"), "player", 0), try(g("UnitPowerMax"), "player", 0))
    if mp and mp < 60 then gaps[#gaps + 1] = "mana " .. mp .. " %" end
  end
  local API = T.API or {}
  local free, total = try(API.FreeSlots)
  if free == 0 and (tonumber(total) or 0) > 0 then gaps[#gaps + 1] = "sacs pleins" end
  local cls = T.PlayerClass and T:PlayerClass()
  if cls == "HUNTER" then
    local ammo = num(try(g("GetInventoryItemCount"), "player", 0))
    if ammo and ammo < 200 then gaps[#gaps + 1] = "munitions " .. ammo end
  end
  local lvl = num(try(g("UnitLevel"), "player")) or 1
  if ((cls == "HUNTER" and lvl >= 10) or cls == "WARLOCK") and not try(g("UnitExists"), "pet") then gaps[#gaps + 1] = "familier absent" end
  local worst
  for slot = 1, 18 do
    local c, mx = try(g("GetInventoryItemDurability"), slot)
    c, mx = num(c), num(mx)
    if c and mx and mx > 0 then
      local p = c * 100 / mx
      if not worst or p < worst then worst = p end
    end
  end
  if worst and worst < 20 then gaps[#gaps + 1] = "équipement à réparer" end
  return gaps
end

function M.OnReadyCheck()
  if not opt("ready") then return end
  local gaps = M.ReadyGaps()
  if #gaps == 0 then M:Clear("ready") return end
  local list = {}
  for i, x in ipairs(gaps) do if i <= 3 then list[#list + 1] = x end end
  M:Suggest("ready", "Appel prêt : " .. tconcat(list, ", ") .. ", règle ça avant de répondre", 2,
    { ttl = 40, tab = "group", topic = "readycheck", sound = false, onClick = dismiss("ready"),
      tooltip = { "Appel « prêt » du chef", "Ta réponse reste dans la fenêtre du jeu", "Clic : ranger" } })
end

-- ------------------------------------------------------------------ 6 limite d'instances
--- entrées = { epoch, ... }, t = maintenant -> nb dans l'heure, secondes avant une place libre (fonction pure)
function M.InstanceWindow(entries, t)
  local recent = {}
  for _, e in ipairs(entries or {}) do
    if t - e < INST_WINDOW and t >= e then recent[#recent + 1] = e end
  end
  tsort(recent)
  local wait = (#recent >= INST_LIMIT) and (recent[#recent - INST_LIMIT + 1] + INST_WINDOW - t) or nil
  return #recent, wait, recent
end

function M.InstanceLines()
  local out = {}
  if not opt("instances") or not M.db then return out end
  local k, wait = M.InstanceWindow(M.db.inst, now())
  if k < 3 then return out end
  local text
  if wait then text = fmt("Instances : %d sur %d cette heure, prochaine entrée dans %s", k, INST_LIMIT, dur(max(60, wait)))
  else text = fmt("Instances : %d sur %d cette heure, encore %d possible%s", k, INST_LIMIT, INST_LIMIT - k, INST_LIMIT - k > 1 and "s" or "") end
  out[#out + 1] = { text = text, prio = wait and 2 or 1, onClick = function() M.ShowInstances() end,
    tooltip = { "Le jeu limite les entrées en instance (5 par heure)", "Une instance réinitialisée compte à nouveau", "Clic : rafraîchir" } }
  return out
end

function M.ShowInstances() pcall(M.UpdateGroupTab) end

function M.UpdateGroupTab()
  if T.SetTabLines then pcall(T.SetTabLines, T, "group", M.InstanceLines(), "Errands") end
end

function M.OnZone()
  if not M.db then return end
  local inInst, kind = try(g("IsInInstance"))
  inInst = inInst and (kind == "party" or kind == "raid")
  local name = inInst and (try(g("GetInstanceInfo")) or "") or nil
  if inInst and not M.inInst then
    M.db.inst = type(M.db.inst) == "table" and M.db.inst or {}
    local t = now()
    if name ~= M.db.lastInst or M.resetSeen or t - (tonumber(M.db.lastAt) or 0) > INST_WINDOW then
      M.db.inst[#M.db.inst + 1] = t
    end
    M.db.lastInst, M.db.lastAt, M.resetSeen = name, t, false
    local _, _, recent = M.InstanceWindow(M.db.inst, t)
    M.db.inst = recent
    local k = #recent
    if k >= INST_LIMIT and opt("instances") then
      M:Suggest("inst", fmt("Prévois une pause donjon : %d instances en une heure, limite du jeu atteinte", k), 2,
        { ttl = 300, tab = "group", sound = false, onClick = dismiss("inst"), tooltip = { "Clic : ranger" } })
    end
  end
  M.inInst = inInst and true or false
  M.UpdateGroupTab()
end

local resetPat
function M.OnSystem(msg)
  if type(msg) ~= "string" then return end
  if resetPat == nil then
    local p = g("INSTANCE_RESET_SUCCESS")
    resetPat = type(p) == "string" and ("^" .. p:gsub("([%(%)%.%+%-%*%?%[%]%^%$])", "%%%1"):gsub("%%%%s", ".+") .. "$") or false
  end
  if resetPat and msg:find(resetPat) then M.resetSeen = true end
end

-- ------------------------------------------------------------------ cycle de vie
function M:AutoRefresh()
  if M.mailOpen then pcall(M.UpdateMail) pcall(M.ShowMats, sendOpen()) end
  if (now() - (M.lastGroup or 0)) >= 60 then
    M.lastGroup = now()
    M.UpdateGroupTab()
    pcall(M.UpdateSave)
    pcall(M.TrainerReminder)
  end
end

function M:OnEnable()
  M:RegisterEvent("MAIL_SHOW", function() M.mailOpen = true; hidden.mail, hidden.cod1, hidden.cod2, hidden.expire = nil, nil, nil, nil; M:Clear("expireLogin"); pcall(M.UpdateMail) end)
  M:RegisterEvent("MAIL_INBOX_UPDATE", function() pcall(M.UpdateMail) end)
  M:RegisterEvent("MAIL_CLOSED", function() M.ClearMail() pcall(M.ShowMats, false) end)
  M:RegisterEvent("MAIL_SEND_INFO_UPDATE", function() pcall(M.ShowMats, sendOpen()) end)
  M:RegisterEvent("PLAYER_LEVEL_UP", function(_, _, lv)
    if M.char then M.char.trainPending = num(lv) or num(try(g("UnitLevel"), "player")) end
    T:After(2, function() pcall(M.TrainerReminder) pcall(M.UpdateSave) end)
  end)
  M:RegisterEvent("PLAYER_MONEY", function() pcall(M.UpdateSave) end)
  M:RegisterEvent("PLAYER_UPDATE_RESTING", function() pcall(M.TrainerReminder) end)
  M:RegisterEvent("TAXIMAP_OPENED", function() pcall(M.UpdateTaxi) end)
  M:RegisterEvent("TAXIMAP_CLOSED", function() M:Clear("taxi") end)
  M:RegisterEvent("TRAINER_SHOW", function() pcall(M.UpdateTrainer) end)
  M:RegisterEvent("TRAINER_UPDATE", function() pcall(M.UpdateTrainer) end)
  M:RegisterEvent("TRAINER_CLOSED", function() M:Clear("trainer") pcall(M.TrainerReminder) end)
  M:RegisterEvent("READY_CHECK", function() pcall(M.OnReadyCheck) end)
  M:RegisterEvent("READY_CHECK_FINISHED", function() M:Clear("ready") end)
  M:RegisterEvent("PLAYER_ENTERING_WORLD", function() pcall(M.OnZone) end)
  M:RegisterEvent("ZONE_CHANGED_NEW_AREA", function() pcall(M.OnZone) end)
  M:RegisterEvent("CHAT_MSG_SYSTEM", function(_, _, msg) pcall(M.OnSystem, msg) end)
  M:On("SUMMARY", function()
    local s = T:Summary()
    if type(s) ~= "table" or not M.db then return end
    local k = M.InstanceWindow(M.db.inst, now())
    s.errands = { instancesHour = k, trainer = M.char and M.char.trainer or nil,
      mailExpire = M.char and type(M.char.mail) == "table" and M.char.mail.expireAt or nil }
  end)
  T:After(10, function() pcall(M.MailReminder) pcall(M.UpdateSave) pcall(M.TrainerReminder) end)
  M.UpdateGroupTab()
end

function M:OnDisable()
  M.ClearMail()
  pcall(M.ShowMats, false)
  if T.SetTabLines then pcall(T.SetTabLines, T, "group", {}, "Errands") pcall(T.SetTabLines, T, "info", {}, "Errands") end
end
function M:OnOptionChanged() M.UpdateGroupTab() end

T.Errands = M
