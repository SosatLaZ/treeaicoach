-- TreeAI — module Highlight (agent HIGHLIGHT) : surbrillance des objets et monstres de quête.
-- Ce que font déjà les addons connus, réunis et poussés plus loin :
--  * Bagnon / Baganator / AdiBags : bordure jaune + « ! » sur les objets de quête (GetContainerItemQuestInfo).
--    Ici : 6 états distincts (objectif x/y + barre de progression, objectif rempli « à rendre » ✓, objet qui
--    LANCE une quête « ! » or animé, objet utile pour une quête À VENIR (Questie : quêtes dispo + suites de
--    chaînes), objet de quête « inutile maintenant » assombri, objet à utiliser de la quête), « +1 » quand le
--    compteur monte.
--  * Questie / QuestPlates / Plater : icône sur la barre de nom des monstres d'objectif (scan d'infobulle).
--    Ici : monstres à tuer ET monstres qui lâchent l'objet (Questie npcDrops + taux de chute MESURÉS par
--    Predict), reste à faire, badge sur le cadre de cible, pulsation sur notre boussole quand un objectif est
--    proche, ligne d'infobulle « Quête : X — encore 4 » (hors combat seulement : Forever masque les données).
--  * fenêtre de butin + jets de dés : objets de quête en surbrillance, « pour la quête X (3/10) ».
-- Taint : uniquement NOS cadres (enfants d'UIParent), ANCRÉS (SetPoint) sur les boutons Blizzard / tiers, jamais
-- SetParent, jamais d'écriture de champ sur un cadre Blizzard, aucun clic intercepté (EnableMouse false),
-- hooksecurefunc / HookScript / API publiques des addons de sacs en lecture seule. Événementiel, budget ≤ 1 ms.
-- Lua 5.1, aucune globale, toutes les API testées.
local T = _G.TreeAI
if not T or not T.RegisterModule then return end
local API = T.API or {}
local G = _G

local COLOR_HELP = "1 or · 2 vert · 3 bleu · 4 violet · 5 rouge · 6 orange · 7 cyan · 8 blanc"
local M = T:RegisterModule("Highlight", {
  title = "Surbrillance quêtes",
  defaults = {
    enabled = true, intensity = 8, anim = true, popup = true, iconSize = 14,
    bags = true, bagNeed = true, bagDone = true, bagStarter = true, bagUpcoming = true, bagUseless = true,
    thirdParty = true,
    colNeed = 7, colDone = 2, colStarter = 1, colUpcoming = 4, colMob = 7, uselessDim = 6,
    loot = true, roll = true,
    plates = true, plateDrops = true, plateTooltipScan = true, target = true,
    compass = true,
    tooltip = true, tooltipWithQuestie = false,
  },
  options = {
    { key = "intensity", type = "range", label = "Intensité de la lueur", min = 1, max = 10, step = 1, section = "Général" },
    { key = "anim", type = "toggle", label = "Lueur animée (pulsation)" },
    { key = "popup", type = "toggle", label = "« +1 » quand un objectif avance" },
    { key = "iconSize", type = "range", label = "Taille des icônes", min = 8, max = 24, step = 1 },
    { key = "bags", type = "toggle", label = "Sacs : surbrillance des objets", section = "Sacs" },
    { key = "bagNeed", type = "toggle", label = "Objets d'objectif (x/y + barre)" },
    { key = "bagDone", type = "toggle", label = "Objectif rempli : à rendre ✓" },
    { key = "bagStarter", type = "toggle", label = "Objets qui lancent une quête « ! »" },
    { key = "bagUpcoming", type = "toggle", label = "Objets utiles pour une quête à venir (Questie)" },
    { key = "bagUseless", type = "toggle", label = "Assombrir les objets de quête devenus inutiles" },
    { key = "uselessDim", type = "range", label = "Assombrissement des inutiles", min = 1, max = 10, step = 1 },
    { key = "thirdParty", type = "toggle", label = "Aussi dans Baganator / Bagnon / AdiBags / ElvUI" },
    { key = "colNeed", type = "range", label = "Couleur objectif (" .. COLOR_HELP .. ")", min = 1, max = 8, step = 1, section = "Couleurs" },
    { key = "colDone", type = "range", label = "Couleur « à rendre »", min = 1, max = 8, step = 1 },
    { key = "colStarter", type = "range", label = "Couleur « lance une quête »", min = 1, max = 8, step = 1 },
    { key = "colUpcoming", type = "range", label = "Couleur « quête à venir »", min = 1, max = 8, step = 1 },
    { key = "colMob", type = "range", label = "Couleur des monstres d'objectif", min = 1, max = 8, step = 1 },
    { key = "loot", type = "toggle", label = "Fenêtre de butin : objets de quête", section = "Butin" },
    { key = "roll", type = "toggle", label = "Jets de dés (groupe) : objets de quête" },
    { key = "plates", type = "toggle", label = "Barres de nom : monstres à tuer", section = "Monstres" },
    { key = "plateDrops", type = "toggle", label = "Barres de nom : monstres qui lâchent l'objet (+ taux mesuré)" },
    { key = "plateTooltipScan", type = "toggle", label = "Lire l'infobulle du monstre (hors combat)" },
    { key = "target", type = "toggle", label = "Badge sur le cadre de cible" },
    { key = "compass", type = "toggle", label = "Boussole : pulsation quand un objectif est proche", section = "Monde" },
    { key = "tooltip", type = "toggle", label = "Infobulles : « Quête : X — encore 4 » (hors combat)", section = "Infobulles" },
    { key = "tooltipWithQuestie", type = "toggle", label = "Même si Questie est installé" },
  },
})
T.Highlight = M

-- ------------------------------------------------------------------ utilitaires
local function pass(ok, ...) if ok then return ... end return nil end
local function try(fn, ...) if type(fn) ~= "function" then return nil end return pass(pcall(fn, ...)) end
local function meth(o, k, ...) if type(o) == "table" and type(o[k]) == "function" then return try(o[k], o, ...) end end
local function secret(v) local f = rawget(G, "issecretvalue") return type(f) == "function" and try(f, v) and true or false end
local function sv(v) if v == nil or secret(v) then return nil end return v end
local function low(s) return tostring(s or ""):lower() end
local function cut(s, n) s = tostring(s or "") if #s > n then return s:sub(1, n - 1) .. "…" end return s end
local function opt(k) return M.db and M.db[k] end
-- (LITE) profil essentiel : sacs + butin seulement (pas de barres de nom, badge de cible, boussole, infobulles)
local LITE_OFF = { plates = true, plateDrops = true, plateTooltipScan = true, target = true, compass = true,
  tooltip = true }
local function on(k)
  if LITE_OFF[k] and T.Lite and T:Lite() then return false end
  return M.db and M.db.enabled ~= false and M.db[k] ~= false
end
local function inCombat() return (T.InCombat and T:InCombat()) or try(G.InCombatLockdown) or false end
local function clock() return try(G.debugprofilestop) or ((try(G.GetTime) or 0) * 1000) end
local function mod(name) return T.GetModule and T:GetModule(name) or (T.modules and T.modules[name]) end
local function after(sec, fn) if T.After then T:After(sec, fn) else fn() end end

local COLORS = { { 1, 0.82, 0 }, { 0.25, 1, 0.3 }, { 0.3, 0.6, 1 }, { 0.75, 0.4, 1 }, { 1, 0.25, 0.2 },
  { 1, 0.55, 0.1 }, { 0.2, 1, 1 }, { 1, 1, 1 } }
M.COLORS = COLORS
local function color(key)
  local c = COLORS[math.floor(tonumber(opt(key)) or 1)] or COLORS[1]
  return c[1], c[2], c[3]
end
local function intensity() return math.max(0.1, math.min(1, (tonumber(opt("intensity")) or 8) / 10)) end

local TEX = {
  white = "Interface\\Buttons\\WHITE8X8",
  glow = "Interface\\Buttons\\UI-ActionButton-Border",
  bang = "Interface\\GossipFrame\\AvailableQuestIcon",
  ready = "Interface\\RaidFrame\\ReadyCheck-Ready",
  quest = "Interface\\GossipFrame\\ActiveQuestIcon",
  drop = "Interface\\Icons\\INV_Misc_Bag_08",
  kill = "Interface\\TargetingFrame\\UI-TargetingFrame-Skull",
}

local function itemIDOf(link)
  if type(link) == "number" then return link end
  return tonumber(tostring(link or ""):match("item:(%d+)"))
end
local function linkName(link) return tostring(link or ""):match("%[(.-)%]") end
local function classIDOf(itemID)
  local fn = (G.C_Item and G.C_Item.GetItemInfoInstant) or G.GetItemInfoInstant
  if not (fn and itemID) then return nil end
  local _, _, _, _, _, classID = try(fn, itemID)
  return classID
end
local function npcIDOf(guid)
  guid = sv(guid)
  if type(guid) ~= "string" then return nil end
  local kind, id = guid:match("^(%a+)%-%d+%-%d+%-%d+%-%d+%-(%d+)")
  if kind == "Creature" or kind == "Vehicle" then return tonumber(id) end
  return nil
end
local function questTitle(qid)
  local QL = G.C_QuestLog
  local t = QL and QL.GetTitleForQuestID and try(QL.GetTitleForQuestID, qid)
  if not t and QL and QL.GetQuestInfo then t = try(QL.GetQuestInfo, qid) end
  if not t then
    local db = T.Compass and T.Compass.Questie and T.Compass.Questie()
    t = db and try(db.QueryQuestSingle, qid, "name")
  end
  return type(t) == "string" and t or nil
end
local function questDone(qid)
  local QL = G.C_QuestLog
  if QL and QL.IsQuestFlaggedCompleted then return try(QL.IsQuestFlaggedCompleted, qid) and true or false end
  return try(G.IsQuestFlaggedCompleted, qid) and true or false
end
local function bagQuestInfo(bag, slot)
  if bag == nil or slot == nil then return nil end
  local C = G.C_Container
  if C and C.GetContainerItemQuestInfo then
    local t = try(C.GetContainerItemQuestInfo, bag, slot)
    if type(t) == "table" then return t.isQuestItem, t.questID, t.isActive end
    return nil
  end
  return try(G.GetContainerItemQuestInfo, bag, slot)
end

-- ------------------------------------------------------------------ données (reconstruites à QUEST_LOG_UPDATE)
-- entrée : { quest=titre, qid=, obj=nom, have=, need=, done=objectif rempli, ready=quête finie, use=objet à utiliser,
--            provided=objet fourni par la quête, item=itemID (pour les monstres qui le lâchent) }
local D = { byName = {}, byID = {}, upcoming = {}, mobName = {}, mobNpc = {}, ver = 0 }
M.Data = function() return D end
local kindCache = {}   -- itemID -> {kind, entry} pour la version D.ver
local lastHave = {}    -- clé objectif -> dernier « have » vu (pour « +1 »)
local bumped = {}      -- clé objectif -> GetTime() du dernier +1

local function mobKey(objName)
  local s = low(objName)
  s = s:gsub("%s+tu[ée]+s?$", ""):gsub("%s+slain$", ""):gsub("%s+tu[ée]+e?s?$", "")
  return s
end

local function questList()
  local Q = mod("Quests")
  if not Q then return {} end
  if type(Q.lastList) == "table" then return Q.lastList end
  return (type(Q.Scan) == "function" and try(Q.Scan)) or {}
end

local function better(old, new)  -- garde l'objectif non rempli en priorité
  if not old then return new end
  if old.done and not new.done then return new end
  return old
end

function M.Rebuild()
  local t0 = clock()
  local byName, byID, upcoming, mobName, mobNpc = {}, {}, {}, {}, {}
  local list = questList()
  for _, q in ipairs(list) do
    for _, o in ipairs(q.obj or {}) do
      local e = { quest = q.title, qid = q.id, obj = o.name, have = o.have or 0, need = o.need or 1,
        done = o.finished and true or false, ready = q.complete and true or false, key = q.id .. ":" .. low(o.name) }
      local k = low(o.name)
      if k ~= "" then
        byName[k] = better(byName[k], e)
        if not e.done and (o.t == nil or o.t == "m") then mobName[mobKey(o.name)] = better(mobName[mobKey(o.name)], e) end
        local prev = lastHave[e.key]
        if prev and e.have > prev then bumped[e.key] = try(G.GetTime) or 0 end
        lastHave[e.key] = e.have
      end
    end
    if type(q.item) == "table" and q.item.id then
      byID[q.item.id] = { quest = q.title, qid = q.id, use = true, ready = q.complete, obj = linkName(q.item.link) }
    end
  end
  -- Questie (lecture seule) : IDs d'objets d'objectif, monstres qui les lâchent, objets fournis, quêtes à venir
  local C = T.Compass
  local db = C and C.Questie and C.Questie()
  local budget = 400
  local function qq(id, key) budget = budget - 1 if budget < 0 then return nil end return try(db.QueryQuestSingle, id, key) end
  if db then
    for i, q in ipairs(list) do
      if i > 30 or budget < 0 then break end
      local objs = type(q.id) == "number" and qq(q.id, "objectives")
      if type(objs) == "table" then
        for _, it in ipairs(type(objs[3]) == "table" and objs[3] or {}) do
          local itemID = type(it) == "table" and it[1] or it
          if type(itemID) == "number" then
            local nm = (db.QueryItemSingle and try(db.QueryItemSingle, itemID, "name")) or (API.ItemName and API.ItemName(itemID))
            local e = nm and byName[low(nm)]
            if not e then  -- un seul objectif objet : on l'associe quand même
              for _, o in ipairs(q.obj or {}) do
                if o.t == "i" then e = byName[low(o.name)] break end
              end
            end
            if e then
              byID[itemID] = better(byID[itemID], e)
              if not e.done and db.QueryItemSingle then
                local de = { quest = e.quest, qid = e.qid, obj = e.obj, have = e.have, need = e.need, item = itemID, key = e.key }
                for n, npc in ipairs(try(db.QueryItemSingle, itemID, "npcDrops") or {}) do
                  if n > 40 then break end
                  if type(npc) == "number" and not mobNpc[npc] then mobNpc[npc] = de end
                end
              end
            end
          end
        end
        for _, c in ipairs(type(objs[1]) == "table" and objs[1] or {}) do
          local id = type(c) == "table" and c[1] or c
          if type(id) == "number" and db.QueryNPCSingle then
            local nm = try(db.QueryNPCSingle, id, "name")
            local e = nm and (mobName[low(nm)] or mobName[mobKey(nm)])
            if e then mobNpc[id] = e end
          end
        end
      end
      for _, it in ipairs(type(q.id) == "number" and qq(q.id, "requiredSourceItems") or {}) do
        if type(it) == "number" and not byID[it] then
          byID[it] = { quest = q.title, qid = q.id, provided = true, ready = q.complete }
        end
      end
    end
    -- à venir : quêtes Questie disponibles ici + suites de chaînes des quêtes du journal
    local Q = mod("Quests")
    local ids = {}
    for _, e in ipairs((Q and Q.lastQuestie and Q.lastQuestie.here) or {}) do ids[#ids + 1] = e.id end
    for i, q in ipairs(list) do
      if i > 30 then break end
      local nx = type(q.id) == "number" and tonumber((qq(q.id, "nextQuestInChain")))
      if nx and nx > 0 then ids[#ids + 1] = nx end
    end
    for i, id in ipairs(ids) do
      if i > 40 or budget < 0 then break end
      if type(id) == "number" then
        local name = qq(id, "name")
        local items = {}
        for _, it in ipairs(qq(id, "requiredSourceItems") or {}) do items[#items + 1] = it end
        local objs = qq(id, "objectives")
        for _, it in ipairs(type(objs) == "table" and type(objs[3]) == "table" and objs[3] or {}) do
          items[#items + 1] = type(it) == "table" and it[1] or it
        end
        for _, itemID in ipairs(items) do
          if type(itemID) == "number" and not byID[itemID] and not upcoming[itemID] then
            upcoming[itemID] = { quest = name or ("#" .. id), qid = id }
          end
        end
      end
    end
  end
  D.byName, D.byID, D.upcoming, D.mobName, D.mobNpc = byName, byID, upcoming, mobName, mobNpc
  D.ver = D.ver + 1
  kindCache = {}
  M.rebuildCost = clock() - t0
  return D
end

-- kind : "need" | "done" | "use" | "starter" | "upcoming" | "useless" | nil
function M.Classify(itemID, name, bag, slot)
  itemID = itemIDOf(itemID)
  if not itemID and not name then return nil end
  local ck = itemID and kindCache[itemID]
  if ck and ck.k ~= "maybe" then return ck.k, ck.e end
  name = name or (itemID and API.ItemName and API.ItemName(itemID))
  local e = (itemID and D.byID[itemID]) or (name and D.byName[low(name)])
  local kind
  if e then
    if e.use then kind = "use"
    elseif e.ready or e.done then kind = "done"
    else kind = "need" end
  else
    local isQuest, qid, active = bagQuestInfo(bag, slot)
    if not isQuest and not qid and itemID then
      local Cn = G.C_Item
      if Cn and Cn.GetItemQuestInfo then isQuest, qid = try(Cn.GetItemQuestInfo, itemID) end
    end
    qid = tonumber(qid)
    if qid and qid > 0 and not active and not questDone(qid) then
      e = { quest = questTitle(qid) or "une nouvelle quête", qid = qid }
      kind = "starter"
    elseif itemID and D.upcoming[itemID] then
      e, kind = D.upcoming[itemID], "upcoming"
    elseif isQuest or (itemID and classIDOf(itemID) == 12) then
      e, kind = { quest = qid and questTitle(qid) or nil, qid = qid }, "useless"
    end
  end
  if itemID then kindCache[itemID] = { k = kind, e = e } end
  return kind, e
end

local KIND_OPT = { need = "bagNeed", use = "bagNeed", done = "bagDone", starter = "bagStarter",
  upcoming = "bagUpcoming", useless = "bagUseless" }
local KIND_COL = { need = "colNeed", use = "colNeed", done = "colDone", starter = "colStarter",
  upcoming = "colUpcoming", useless = "colUpcoming" }

-- texte court « Quête : X — encore 4 » (infobulles, butin, cible)
function M.Describe(kind, e)
  if not kind or not e then return nil end
  local q = cut(e.quest or "?", 34)
  if kind == "need" then
    if e.provided then return ("Quête : %s — objet requis"):format(q) end
    local left = math.max(0, (e.need or 1) - (e.have or 0))
    return ("Quête : %s — encore %d (%d/%d)"):format(q, left, e.have or 0, e.need or 1)
  elseif kind == "done" then return ("Quête : %s — prêt, à rendre"):format(q)
  elseif kind == "use" then return ("Quête : %s — objet à utiliser"):format(q)
  elseif kind == "starter" then return ("Lance la quête : %s"):format(q)
  elseif kind == "upcoming" then return ("Utile pour une quête à venir : %s"):format(q)
  elseif kind == "useless" then return "Objet de quête inutile maintenant (quête rendue ou absente)" end
end

-- ------------------------------------------------------------------ calques (nos cadres, ancrés)
local overlays = setmetatable({}, { __mode = "k" })  -- bouton cible -> calque
local active = {}                                     -- calque -> bouton cible (visibles)
local driver
local function fontOf(fs, size)
  if fs and fs.SetFontObject and G.GameFontNormalSmall then pcall(fs.SetFontObject, fs, G.GameFontNormalSmall) end
  if fs and fs.SetFont then pcall(fs.SetFont, fs, "Fonts\\FRIZQT__.TTF", size or 11, "OUTLINE") end
end

local function ensureDriver()
  if driver or not G.CreateFrame then return driver end
  driver = G.CreateFrame("Frame", nil, G.UIParent)
  local acc = 0
  driver:SetScript("OnUpdate", function(_, el)
    acc = acc + (el or 0)
    if acc < 0.2 then return end
    acc = 0
    local any = false
    for ov, owner in pairs(active) do
      local vis = meth(owner, "IsVisible")
      if not vis then ov:Hide() active[ov] = nil else any = true end
    end
    if not any then driver:Hide() end
  end)
  return driver
end

local function newOverlay(owner)
  local ov = G.CreateFrame("Frame", nil, G.UIParent)
  if ov.EnableMouse then ov:EnableMouse(false) end
  ov.edges = {}
  for i = 1, 4 do
    local t = ov:CreateTexture(nil, "OVERLAY")
    t:SetTexture(TEX.white)
    ov.edges[i] = t
  end
  ov.edges[1]:SetPoint("TOPLEFT", ov, "TOPLEFT") ov.edges[1]:SetPoint("TOPRIGHT", ov, "TOPRIGHT") ov.edges[1]:SetHeight(2)
  ov.edges[2]:SetPoint("BOTTOMLEFT", ov, "BOTTOMLEFT") ov.edges[2]:SetPoint("BOTTOMRIGHT", ov, "BOTTOMRIGHT") ov.edges[2]:SetHeight(2)
  ov.edges[3]:SetPoint("TOPLEFT", ov, "TOPLEFT") ov.edges[3]:SetPoint("BOTTOMLEFT", ov, "BOTTOMLEFT") ov.edges[3]:SetWidth(2)
  ov.edges[4]:SetPoint("TOPRIGHT", ov, "TOPRIGHT") ov.edges[4]:SetPoint("BOTTOMRIGHT", ov, "BOTTOMRIGHT") ov.edges[4]:SetWidth(2)
  ov.glow = ov:CreateTexture(nil, "OVERLAY")
  ov.glow:SetTexture(TEX.glow)
  if ov.glow.SetBlendMode then ov.glow:SetBlendMode("ADD") end
  ov.glow:SetPoint("TOPLEFT", ov, "TOPLEFT", -14, 14)
  ov.glow:SetPoint("BOTTOMRIGHT", ov, "BOTTOMRIGHT", 14, -14)
  ov.shade = ov:CreateTexture(nil, "ARTWORK")
  ov.shade:SetTexture(TEX.white)
  ov.shade:SetAllPoints(ov)
  ov.barBg = ov:CreateTexture(nil, "OVERLAY")
  ov.barBg:SetTexture(TEX.white)
  ov.barBg:SetPoint("BOTTOMLEFT", ov, "BOTTOMLEFT", 2, 2)
  ov.barBg:SetPoint("BOTTOMRIGHT", ov, "BOTTOMRIGHT", -2, 2)
  ov.barBg:SetHeight(3)
  ov.bar = ov:CreateTexture(nil, "OVERLAY")
  ov.bar:SetTexture(TEX.white)
  ov.bar:SetPoint("BOTTOMLEFT", ov.barBg, "BOTTOMLEFT")
  ov.bar:SetHeight(3)
  ov.badge = ov:CreateTexture(nil, "OVERLAY")
  ov.badge:SetPoint("TOPLEFT", ov, "TOPLEFT", -3, 3)
  ov.text = ov:CreateFontString(nil, "OVERLAY")
  fontOf(ov.text, 10)
  ov.text:SetPoint("TOPRIGHT", ov, "TOPRIGHT", 1, 1)
  ov.label = ov:CreateFontString(nil, "OVERLAY")
  fontOf(ov.label, 11)
  ov.label:SetPoint("BOTTOM", ov, "TOP", 0, 2)
  ov.pop = ov:CreateFontString(nil, "OVERLAY")
  fontOf(ov.pop, 14)
  ov.pop:SetPoint("CENTER", ov, "CENTER")
  ov.pop:SetText("+1")
  ov.pop:Hide()
  local ag = ov.glow.CreateAnimationGroup and ov.glow:CreateAnimationGroup()
  if ag then
    local a = ag.CreateAnimation and ag:CreateAnimation("Alpha")
    if a then
      if a.SetFromAlpha then a:SetFromAlpha(0.25) end
      if a.SetToAlpha then a:SetToAlpha(1) end
      if a.SetDuration then a:SetDuration(0.7) end
      if a.SetSmoothing then a:SetSmoothing("IN_OUT") end
    end
    if ag.SetLooping then ag:SetLooping("BOUNCE") end
    ov.ag = ag
  end
  local pg = ov.pop.CreateAnimationGroup and ov.pop:CreateAnimationGroup()
  if pg then
    local tr = pg.CreateAnimation and pg:CreateAnimation("Translation")
    if tr and tr.SetOffset then tr:SetOffset(0, 18) end
    if tr and tr.SetDuration then tr:SetDuration(0.9) end
    local fa = pg.CreateAnimation and pg:CreateAnimation("Alpha")
    if fa and fa.SetFromAlpha then fa:SetFromAlpha(1) fa:SetToAlpha(0) end
    if fa and fa.SetDuration then fa:SetDuration(0.9) end
    if pg.SetScript then pg:SetScript("OnFinished", function() ov.pop:Hide() end) end
    ov.pg = pg
  end
  ov:Hide()
  return ov
end

local function anchor(ov, owner)
  if ov.owner ~= owner then
    ov:ClearAllPoints()
    ov:SetPoint("TOPLEFT", owner, "TOPLEFT")
    ov:SetPoint("BOTTOMRIGHT", owner, "BOTTOMRIGHT")
    ov.owner = owner
  end
  local strata = meth(owner, "GetFrameStrata")
  if strata and ov.SetFrameStrata then ov:SetFrameStrata(strata) end
  local lvl = tonumber(meth(owner, "GetFrameLevel")) or 1
  if ov.SetFrameLevel then ov:SetFrameLevel(lvl + 4) end
end

local function hideOverlay(owner)
  local ov = owner and overlays[owner]
  if ov then
    if ov.ag and ov.ag.Stop then ov.ag:Stop() end
    ov:Hide()
    active[ov] = nil
  end
end
M.HideOverlay = hideOverlay

local function setEdges(ov, r, g, b, a)
  for i = 1, 4 do ov.edges[i]:SetVertexColor(r, g, b, a) ov.edges[i]:SetShown(a > 0) end
end

-- applique un état sur un bouton (sac, butin, jet de dés). label = texte au-dessus (facultatif).
function M.Apply(owner, kind, e, label)
  if not (owner and G.CreateFrame) then return nil end
  if not kind or (KIND_OPT[kind] and not on(KIND_OPT[kind]) and not label) then hideOverlay(owner) return nil end
  local ov = overlays[owner]
  if not ov then ov = newOverlay(owner) overlays[owner] = ov end
  anchor(ov, owner)
  local I = intensity()
  local r, g, b = color(KIND_COL[kind] or "colNeed")
  local size = tonumber(opt("iconSize")) or 14
  ov.kind, ov.entry = kind, e
  ov.shade:Hide() ov.bar:Hide() ov.barBg:Hide() ov.badge:Hide() ov.glow:Hide()
  ov.text:SetText("") ov.label:SetText(label or "")
  local glow, animate = false, false
  if kind == "need" or kind == "use" then
    setEdges(ov, r, g, b, 0.9 * I)
    glow, animate = true, true
    if kind == "need" and not e.provided and (e.need or 1) > 1 then
      ov.text:SetText(("%d/%d"):format(e.have or 0, e.need or 1))
      ov.barBg:SetVertexColor(0, 0, 0, 0.7) ov.barBg:Show()
      local w = (tonumber(meth(owner, "GetWidth")) or 36) - 4
      ov.bar:SetWidth(math.max(1, w * math.min(1, (e.have or 0) / (e.need or 1))))
      ov.bar:SetVertexColor(r, g, b, 1) ov.bar:Show()
    elseif kind == "use" then
      ov.text:SetText("U")
    end
    ov.text:SetTextColor(r, g, b)
    ov.badge:SetTexture(TEX.quest)
    ov.badge:SetSize(size, size) ov.badge:Show()
  elseif kind == "done" then
    setEdges(ov, r, g, b, 0.8 * I)
    ov.badge:SetTexture(TEX.ready) ov.badge:SetSize(size, size) ov.badge:Show()
  elseif kind == "starter" then
    setEdges(ov, r, g, b, I)
    glow, animate = true, true
    ov.badge:SetTexture(TEX.bang) ov.badge:SetSize(size + 4, size + 4) ov.badge:Show()
  elseif kind == "upcoming" then
    setEdges(ov, r, g, b, 0.7 * I)
    ov.text:SetText("»") ov.text:SetTextColor(r, g, b)
  elseif kind == "useless" then
    setEdges(ov, 0.4, 0.4, 0.4, 0.5)
    local dim = math.max(0.1, math.min(0.9, (tonumber(opt("uselessDim")) or 6) / 10 * 0.85))
    ov.shade:SetVertexColor(0, 0, 0, dim) ov.shade:Show()
    ov.text:SetText("×") ov.text:SetTextColor(0.7, 0.7, 0.7)
  end
  if glow then
    ov.glow:SetVertexColor(r, g, b, I) ov.glow:Show()
    if ov.ag then
      if animate and opt("anim") ~= false then
        if not (ov.ag.IsPlaying and ov.ag:IsPlaying()) and ov.ag.Play then ov.ag:Play() end
      elseif ov.ag.Stop then ov.ag:Stop() end
    end
  elseif ov.ag and ov.ag.Stop then ov.ag:Stop() end
  -- « +1 » quand le compteur vient de monter (≤ 3 s)
  if e and e.key and bumped[e.key] and opt("popup") ~= false then
    local t = try(G.GetTime) or 0
    if t - bumped[e.key] < 3 then
      ov.pop:SetTextColor(r, g, b) ov.pop:Show()
      if ov.pg and ov.pg.Play then ov.pg:Play() end
    end
    bumped[e.key] = nil
  end
  ov:Show()
  active[ov] = owner
  local d = ensureDriver()
  if d then d:Show() end
  return ov
end
M.Overlays = function() return overlays end
M.Active = function() return active end

-- ------------------------------------------------------------------ sacs : Blizzard + tiers (lecture seule)
local ext = setmetatable({}, { __mode = "k" })   -- boutons d'addons tiers -> {bag, slot}
local function slotItem(bag, slot)
  local it = API.BagItem and API.BagItem(bag, slot)
  return it
end
local function applySlot(btn, bag, slot, stats)
  local it = slotItem(bag, slot)
  if not it or not it.itemID then hideOverlay(btn) return end
  local kind, e = M.Classify(it.itemID, nil, bag, slot)
  if kind and on(KIND_OPT[kind]) then
    M.Apply(btn, kind, e)
    if stats then stats[kind] = (stats[kind] or 0) + 1 end
  else hideOverlay(btn) end
end

-- boutons de sac Blizzard visibles : { {btn, bag, slot}, ... }
function M.BlizzardBagButtons()
  local out = {}
  local function add(btn, bag)
    if not (btn and meth(btn, "IsShown")) then return end
    local b = tonumber(meth(btn, "GetBagID")) or bag
    local s = tonumber(meth(btn, "GetID"))
    if b and s and s > 0 then out[#out + 1] = { btn, b, s } end
  end
  local frames = {}
  local n = tonumber(G.NUM_CONTAINER_FRAMES) or 13
  for i = 1, n do frames[#frames + 1] = rawget(G, "ContainerFrame" .. i) end
  frames[#frames + 1] = rawget(G, "ContainerFrameCombinedBags")
  for _, f in ipairs(frames) do
    if type(f) == "table" and meth(f, "IsShown") then
      local bag = tonumber(meth(f, "GetID"))
      local ok, iter, st, ctl = false, nil, nil, nil
      if type(f.EnumerateValidItems) == "function" then ok, iter, st, ctl = pcall(f.EnumerateValidItems, f) end
      if ok and type(iter) == "function" then  -- Mainline
        for _, btn in iter, st, ctl do add(btn, bag) end
      else  -- Classic : ContainerFrameNItemM
        local name = meth(f, "GetName")
        if name then
          for j = 1, 36 do
            local btn = rawget(G, name .. "Item" .. j)
            if not btn then break end
            add(btn, bag)
          end
        end
      end
    end
  end
  return out
end

M.stats = {}
function M.RefreshBags()
  if not on("bags") then
    for btn in pairs(overlays) do hideOverlay(btn) end
    return 0
  end
  local t0 = clock()
  local stats = {}
  for _, rec in ipairs(M.BlizzardBagButtons()) do applySlot(rec[1], rec[2], rec[3], stats) end
  if on("thirdParty") then
    for btn, rec in pairs(ext) do
      if meth(btn, "IsVisible") then applySlot(btn, rec[1], rec[2], stats) end
    end
  end
  M.stats = stats
  M.bagCost = clock() - t0
  return M.bagCost
end

-- résumé « Quêtes » : objets de quête dans les sacs (aussi sans fenêtre de sac ouverte)
function M.ScanBags()
  local res = { need = {}, done = {}, starter = {}, upcoming = {}, useless = {}, use = {} }
  if not API.IterBags then return res end
  local ok, iter, s, v = pcall(API.IterBags)
  if not ok or type(iter) ~= "function" then return res end
  for bag, slot, item in iter, s, v do
    local kind, e = M.Classify(item and item.itemID, nil, bag, slot)
    if kind and res[kind] then
      res[kind][#res[kind] + 1] = { itemID = item.itemID, link = item.link, e = e, bag = bag, slot = slot }
    end
  end
  return res
end

local tabSent
function M.PushTab()
  if not T.SetTabLines then return end
  local r = M.ScanBags()
  local lines = {}
  local function nm(x) return linkName(x.link) or (API.ItemName and API.ItemName(x.itemID)) or ("objet " .. tostring(x.itemID)) end
  for _, x in ipairs(r.starter) do
    lines[#lines + 1] = { text = ("« ! » %s lance « %s » (clic droit)"):format(nm(x), cut(x.e and x.e.quest, 30)), prio = 2,
      icon = TEX.bang, pin = true }
  end
  if #r.useless > 0 then
    local names = {}
    for i, x in ipairs(r.useless) do if i <= 3 then names[#names + 1] = nm(x) end end
    lines[#lines + 1] = { text = ("Inutile maintenant : %s%s"):format(table.concat(names, ", "), #r.useless > 3 and "…" or ""),
      prio = 1, tooltip = "Objets de quête qui ne servent plus à aucune quête de ton journal : vends ou détruis-les." }
  end
  if #r.upcoming > 0 then
    lines[#lines + 1] = { text = ("Garde %s : pour « %s »"):format(nm(r.upcoming[1]), cut(r.upcoming[1].e.quest, 30)), prio = 1 }
  end
  local key = #lines .. ":" .. table.concat((function() local t = {} for i, l in ipairs(lines) do t[i] = l.text end return t end)(), "|")
  if key == tabSent then return lines end
  tabSent = key
  try(T.SetTabLines, T, "quests", lines, "Highlight")
  return lines
end

-- adaptateurs tiers : uniquement leurs API publiques / hooksecurefunc, aucun champ écrit chez eux
local hookedThird = {}
local function hookThirdParty()
  if not on("thirdParty") then return end
  local hsf = G.hooksecurefunc
  -- Baganator : widget de coin officiel (API.RegisterCornerWidget), classement par itemID
  local BG = rawget(G, "Baganator")
  if not hookedThird.baganator and type(BG) == "table" and type(BG.API) == "table" and type(BG.API.RegisterCornerWidget) == "function" then
    hookedThird.baganator = true
    pcall(BG.API.RegisterCornerWidget, "TreeAI : quête", "treeai_quest",
      function(widget, details)
        if not (on("bags") and on("thirdParty")) or type(details) ~= "table" then return false end
        local id = details.itemID or itemIDOf(details.itemLink)
        local kind, e = M.Classify(id, details.itemName)
        if not kind or not on(KIND_OPT[kind]) or kind == "useless" then return false end
        local r, g, b = color(KIND_COL[kind])
        if widget.tex then widget.tex:SetTexture(kind == "starter" and TEX.bang or kind == "done" and TEX.ready or TEX.quest) end
        if widget.txt then
          widget.txt:SetText(kind == "need" and not e.provided and (e.need or 1) > 1 and ((e.have or 0) .. "/" .. (e.need or 1)) or "")
          widget.txt:SetTextColor(r, g, b)
        end
        return true
      end,
      function(itemButton)
        local f = G.CreateFrame("Frame", nil, itemButton)
        f:SetSize(16, 16)
        f.tex = f:CreateTexture(nil, "OVERLAY") f.tex:SetAllPoints(f)
        f.txt = f:CreateFontString(nil, "OVERLAY") fontOf(f.txt, 10) f.txt:SetPoint("LEFT", f, "RIGHT", 0, 0)
        return f
      end, { corner = "top_left", priority = 1 })
  end
  -- AdiBags : message public AdiBags_UpdateButton (button.bag / button.slot)
  local LS = rawget(G, "LibStub")
  if not hookedThird.adibags and type(LS) == "table" and rawget(G, "AdiBags") then
    local AE = try(LS.GetLibrary or LS, LS, "AceEvent-3.0", true)
    if type(AE) == "table" and type(AE.Embed) == "function" then
      hookedThird.adibags = true
      local listener = {}
      pcall(AE.Embed, AE, listener)
      if type(listener.RegisterMessage) == "function" then
        pcall(listener.RegisterMessage, listener, "AdiBags_UpdateButton", function(_, btn)
          if type(btn) == "table" and tonumber(btn.bag) and tonumber(btn.slot) then
            ext[btn] = { btn.bag, btn.slot }
            if on("bags") and on("thirdParty") then applySlot(btn, btn.bag, btn.slot) end
          end
        end)
      end
    end
  end
  -- Bagnon (Wildpants) : hooksecurefunc sur la classe d'objet
  local BN = rawget(G, "Bagnon")
  if not hookedThird.bagnon and type(BN) == "table" and type(hsf) == "function" then
    local cls = type(BN.Item) == "table" and BN.Item or (type(BN.ItemSlot) == "table" and BN.ItemSlot)
    if cls and type(cls.Update) == "function" then
      hookedThird.bagnon = true
      pcall(hsf, cls, "Update", function(btn)
        local bag = tonumber(meth(btn, "GetBag")) or tonumber(type(btn) == "table" and btn.bag)
        local slot = tonumber(meth(btn, "GetID"))
        if bag and slot then
          ext[btn] = { bag, slot }
          if on("bags") and on("thirdParty") then applySlot(btn, bag, slot) end
        end
      end)
    end
  end
  -- ElvUI : B:UpdateSlot(frame, bagID, slotID)
  local EL = rawget(G, "ElvUI")
  local E = type(EL) == "table" and EL[1]
  if not hookedThird.elvui and type(E) == "table" and type(E.GetModule) == "function" and type(hsf) == "function" then
    local B = try(E.GetModule, E, "Bags", true)
    if type(B) == "table" and type(B.UpdateSlot) == "function" then
      hookedThird.elvui = true
      pcall(hsf, B, "UpdateSlot", function(_, frame, bag, slot)
        local bags = type(frame) == "table" and frame.Bags
        local btn = type(bags) == "table" and type(bags[bag]) == "table" and bags[bag][slot]
        if btn and slot then
          ext[btn] = { bag, slot }
          if on("bags") and on("thirdParty") then applySlot(btn, bag, slot) end
        end
      end)
    end
  end
end
M.HookThirdParty = hookThirdParty
M.External = function() return ext end

-- ------------------------------------------------------------------ butin + jets de dés
local lootInfo
local function lootPanel()
  if lootInfo or not G.CreateFrame then return lootInfo end
  lootInfo = G.CreateFrame("Frame", nil, G.UIParent)
  if lootInfo.EnableMouse then lootInfo:EnableMouse(false) end
  lootInfo:SetSize(260, 20)
  lootInfo.text = lootInfo:CreateFontString(nil, "OVERLAY")
  fontOf(lootInfo.text, 12)
  lootInfo.text:SetPoint("TOPLEFT", lootInfo, "TOPLEFT")
  if lootInfo.text.SetJustifyH then lootInfo.text:SetJustifyH("LEFT") end
  if T.Theme then T.Theme:Skin(lootInfo, "hud") end  -- thème commun (notre cadre, ancré seulement)
  lootInfo:Hide()
  return lootInfo
end

local function lootButtonFor(slot)
  -- Classic : LootButton1..N (champ .slot lu, jamais écrit)
  for i = 1, tonumber(G.LOOTFRAME_NUMBUTTONS) or 4 do
    local b = rawget(G, "LootButton" .. i)
    if type(b) == "table" and meth(b, "IsShown") and (b.slot == slot or (b.slot == nil and tonumber(meth(b, "GetID")) == slot)) then return b end
  end
  -- Mainline : LootFrame.ScrollBox (lecture des éléments)
  local LF = rawget(G, "LootFrame")
  local SB = type(LF) == "table" and LF.ScrollBox
  local found
  if type(SB) == "table" and type(SB.ForEachFrame) == "function" then
    pcall(SB.ForEachFrame, SB, function(f)
      local data = meth(f, "GetElementData")
      local idx = (type(data) == "table" and data.slotIndex) or tonumber(meth(f, "GetSlotIndex"))
      if idx == slot then found = f end
    end)
  end
  return found
end

function M.CheckLoot()
  local panel = lootPanel()
  local lines = {}
  if not on("loot") then if panel then panel:Hide() end return lines end
  local n = try(G.GetNumLootItems) or 0
  for i = 1, n do
    local _, name, _, _, _, _, isQuestItem, questID, isActive = try(G.GetLootSlotInfo, i)
    name = sv(name)
    if name then
      local link = try(G.GetLootSlotLink, i)
      local id = itemIDOf(link)
      local kind, e = M.Classify(id, name)
      questID = tonumber(questID)
      if not kind and questID and questID > 0 and not isActive then
        kind, e = "starter", { quest = questTitle(questID) or "une nouvelle quête", qid = questID }
      elseif not kind and isQuestItem then
        kind, e = "need", { quest = "quête", provided = true }
      end
      if kind and kind ~= "useless" then
        local btn = lootButtonFor(i)
        if btn then M.Apply(btn, kind, e) end
        local nextTxt = ""
        if kind == "need" and not e.provided then
          nextTxt = (" (%d/%d → %d/%d)"):format(e.have or 0, e.need or 1, math.min(e.need or 1, (e.have or 0) + 1), e.need or 1)
        end
        lines[#lines + 1] = ("%s : %s%s"):format(name, M.Describe(kind, e) or "", nextTxt)
      end
    end
  end
  if panel then
    local LF = rawget(G, "LootFrame")
    if #lines > 0 and type(LF) == "table" and meth(LF, "IsShown") then
      panel:ClearAllPoints()
      panel:SetPoint("TOPLEFT", LF, "TOPRIGHT", 6, -6)
      panel.text:SetText(table.concat(lines, "\n"))
      local r, g, b = color("colNeed")
      panel.text:SetTextColor(r, g, b)
      panel:Show()
      active[panel] = LF
      local d = ensureDriver() if d then d:Show() end
    else
      panel:Hide()
    end
  end
  M.lootLines = lines
  return lines
end

function M.OnLootClosed()
  if lootInfo then lootInfo:Hide() active[lootInfo] = nil end
end

local function rollFrameFor(rollID)
  for i = 1, tonumber(G.NUM_GROUP_LOOT_FRAMES) or 4 do
    local f = rawget(G, "GroupLootFrame" .. i)
    if type(f) == "table" and f.rollID == rollID then return f end
  end
  local C = rawget(G, "GroupLootContainer")
  if type(C) == "table" and type(C.rollFrames) == "table" then
    for _, f in pairs(C.rollFrames) do if type(f) == "table" and f.rollID == rollID then return f end end
  end
end

function M.OnRoll(rollID)
  if not on("roll") then return nil end
  local link = try(G.GetLootRollItemLink, rollID)
  local id = itemIDOf(link)
  if not id then return nil end
  local kind, e = M.Classify(id, linkName(link))
  if not kind or kind == "useless" then return nil end
  local f = rollFrameFor(rollID)
  local owner = f and (type(f.IconFrame) == "table" and f.IconFrame or f)
  local txt = M.Describe(kind, e)
  if owner then M.Apply(owner, kind, e, txt) end
  M.lastRoll = { rollID = rollID, kind = kind, text = txt }
  return M.lastRoll
end

-- ------------------------------------------------------------------ monstres : barres de nom, cible, boussole
local plates = {}       -- unit -> icône
local plateInfo = {}    -- unit -> {e, how}
local guidCache = {}    -- guid -> {e, how} | false (calculé hors combat)
local tipScanCache = {}

-- lecture d'infobulle (C_TooltipInfo, Mainline) : lignes d'objectif « 3/10 » non complètes. Hors combat.
local function tooltipObjective(unit)
  local TI = G.C_TooltipInfo
  if not (TI and TI.GetUnit) or inCombat() or not on("plateTooltipScan") then return nil end
  local data = try(TI.GetUnit, unit)
  if type(data) ~= "table" or type(data.lines) ~= "table" then return nil end
  local QO = G.Enum and G.Enum.TooltipDataLineType and G.Enum.TooltipDataLineType.QuestObjective or 8
  local QT = G.Enum and G.Enum.TooltipDataLineType and G.Enum.TooltipDataLineType.QuestTitle or 17
  local title
  for _, l in ipairs(data.lines) do
    local txt = sv(l.leftText)
    if l.type == QT and txt then title = txt end
    if l.type == QO and txt and not l.completed then
      local h, n = txt:match("(%d+)%s*/%s*(%d+)")
      h, n = tonumber(h), tonumber(n)
      if not (h and n and h >= n) then
        local nm = txt:gsub("^%s*%-%s*", ""):gsub("%d+%s*/%s*%d+", ""):gsub("^%s*:?%s*", ""):gsub("%s*:?%s*$", "")
        local e = D.byName[low(nm)]
        return e or { quest = title or "quête", obj = nm, have = h or 0, need = n or 1 }
      end
    end
  end
  return nil
end

function M.UnitObjective(unit)
  if try(G.UnitIsPlayer, unit) or try(G.UnitIsDead, unit) then return nil end
  local guid = sv(try(G.UnitGUID, unit))
  if guid and guidCache[guid] ~= nil then
    local c = guidCache[guid]
    if c and c.ver == D.ver then return c.e, c.how end
    if c == false and inCombat() then return nil end
  end
  local e, how
  local name = sv(try(G.UnitName, unit))
  if name and on("plates") then
    local n = low(name)
    e = D.mobName[n]
    if e then how = "kill" end
  end
  local npc = npcIDOf(guid)
  if not e and npc and D.mobNpc[npc] then
    e = D.mobNpc[npc]
    how = e.item and "drop" or "kill"
    if how == "drop" and not on("plateDrops") then e, how = nil, nil end
  end
  if not e and name and on("plateDrops") then  -- taux mesurés par Predict (nom du monstre -> objet)
    local P = mod("Predict")
    if P and type(P.DropRate) == "function" then
      for k, ent in pairs(D.byName) do
        if not ent.done and not ent.ready then
          local r = try(P.DropRate, name, k)
          if r and r > 0 then e, how = ent, "drop" break end
        end
      end
    end
  end
  if not e and guid and not tipScanCache[guid] then
    tipScanCache[guid] = true
    e = tooltipObjective(unit)
    if e then how = "kill" end
  end
  if not e then  -- TreeAI.Detect : C_QuestLog.UnitIsRelatedToActiveQuest / UnitIsQuestBoss (sans infobulle)
    local Dt = T.Detect
    local qi = type(Dt) == "table" and type(Dt.QuestUnit) == "function" and Dt.QuestUnit(unit)
    if type(qi) == "table" and qi.hostile and on("plates") then
      e = qi.obj and D.byName[qi.obj] or { quest = qi.quest or "quête", obj = qi.obj or qi.name or "?",
        have = qi.have or 0, need = qi.need or 0 }
      how = "kill"
    end
  end
  if e and how == "drop" and name then
    local P = mod("Predict")
    local r = P and type(P.DropRate) == "function" and try(P.DropRate, name, low(e.obj))
    e = setmetatable({ rate = r }, { __index = e })
  end
  if guid and not inCombat() then guidCache[guid] = e and { e = e, how = how, ver = D.ver } or false end
  return e, how
end

local function plateIcon(unit, plate)
  local f = plates[unit]
  if not f then
    f = G.CreateFrame("Frame", nil, G.UIParent)
    if f.EnableMouse then f:EnableMouse(false) end
    f.icon = f:CreateTexture(nil, "OVERLAY")
    f.icon:SetAllPoints(f)
    f.glow = f:CreateTexture(nil, "ARTWORK")
    f.glow:SetTexture(TEX.glow)
    if f.glow.SetBlendMode then f.glow:SetBlendMode("ADD") end
    f.glow:SetPoint("TOPLEFT", f, "TOPLEFT", -8, 8)
    f.glow:SetPoint("BOTTOMRIGHT", f, "BOTTOMRIGHT", 8, -8)
    f.text = f:CreateFontString(nil, "OVERLAY")
    fontOf(f.text, 12)
    f.text:SetPoint("LEFT", f, "RIGHT", 1, 0)
    local ag = f.glow.CreateAnimationGroup and f.glow:CreateAnimationGroup()
    if ag then
      local a = ag.CreateAnimation and ag:CreateAnimation("Alpha")
      if a and a.SetFromAlpha then a:SetFromAlpha(0.2) a:SetToAlpha(1) end
      if a and a.SetDuration then a:SetDuration(0.6) end
      if ag.SetLooping then ag:SetLooping("BOUNCE") end
      f.ag = ag
    end
    plates[unit] = f
  end
  f:ClearAllPoints()
  f:SetPoint("RIGHT", plate, "LEFT", 4, 0)
  local strata = meth(plate, "GetFrameStrata")
  if strata and f.SetFrameStrata then f:SetFrameStrata(strata) end
  if f.SetFrameLevel then f:SetFrameLevel((tonumber(meth(plate, "GetFrameLevel")) or 1) + 10) end
  return f
end

function M.UpdatePlate(unit)
  plateInfo[unit] = nil
  local NP = G.C_NamePlate
  local plate = NP and NP.GetNamePlateForUnit and try(NP.GetNamePlateForUnit, unit)
  if not (plate and (on("plates") or on("plateDrops"))) then if plates[unit] then plates[unit]:Hide() end return nil end
  local e, how = M.UnitObjective(unit)
  if not e then if plates[unit] then plates[unit]:Hide() end return nil end
  local f = plateIcon(unit, plate)
  local size = (tonumber(opt("iconSize")) or 14) + 4
  f:SetSize(size, size)
  local r, g, b = color("colMob")
  f.icon:SetTexture(how == "drop" and TEX.drop or TEX.quest)
  local left = math.max(0, (e.need or 1) - (e.have or 0))
  local txt = left > 0 and tostring(left) or ""
  if how == "drop" and e.rate then txt = txt .. (" (%d %%)"):format(math.floor(e.rate * 100 + 0.5)) end
  f.text:SetText(txt) f.text:SetTextColor(r, g, b)
  f.glow:SetVertexColor(r, g, b, intensity())
  if f.ag then
    if opt("anim") ~= false then if f.ag.Play and not (f.ag.IsPlaying and f.ag:IsPlaying()) then f.ag:Play() end
    elseif f.ag.Stop then f.ag:Stop() end
  end
  f:Show()
  plateInfo[unit] = { e = e, how = how, name = sv(try(G.UnitName, unit)) }
  return f, e, how
end
M.Plates = function() return plates, plateInfo end

function M.RemovePlate(unit)
  if plates[unit] then plates[unit]:Hide() end
  plateInfo[unit] = nil
end

function M.RefreshPlates()
  for unit in pairs(plates) do M.UpdatePlate(unit) end
  local NP = G.C_NamePlate
  if NP and NP.GetNamePlates then
    for _, p in ipairs(try(NP.GetNamePlates) or {}) do
      local u = type(p) == "table" and (p.namePlateUnitToken or (p.UnitFrame and p.UnitFrame.unit))
      if type(u) == "string" and not plates[u] then M.UpdatePlate(u) end
    end
  end
  M.UpdateCompass()
end

-- badge du cadre de cible
local badge
function M.UpdateTarget()
  local TF = rawget(G, "TargetFrame")
  if not on("target") or not try(G.UnitExists, "target") then if badge then badge:Hide() end return nil end
  local e, how = M.UnitObjective("target")
  if not e then if badge then badge:Hide() end return nil end
  if not badge then
    badge = G.CreateFrame("Frame", nil, G.UIParent)
    if badge.EnableMouse then badge:EnableMouse(false) end
    badge:SetSize(18, 18)
    badge.icon = badge:CreateTexture(nil, "OVERLAY")
    badge.icon:SetAllPoints(badge)
    badge.text = badge:CreateFontString(nil, "OVERLAY")
    fontOf(badge.text, 11)
    badge.text:SetPoint("LEFT", badge, "RIGHT", 2, 0)
  end
  badge:ClearAllPoints()
  if type(TF) == "table" then badge:SetPoint("BOTTOMLEFT", TF, "TOPLEFT", 8, -4)
  else badge:SetPoint("TOP", G.UIParent, "TOP", 0, -120) end
  badge.icon:SetTexture(how == "drop" and TEX.drop or TEX.quest)
  local left = math.max(0, (e.need or 1) - (e.have or 0))
  local txt = left > 0 and ("%s — encore %d"):format(cut(e.quest, 28), left) or cut(e.quest, 28)
  if how == "drop" then txt = txt .. (e.rate and (" (chute %d %%)"):format(math.floor(e.rate * 100 + 0.5)) or " (butin)") end
  badge.text:SetText(txt)
  local r, g, b = color("colMob")
  badge.text:SetTextColor(r, g, b)
  badge:Show()
  if type(TF) == "table" then active[badge] = TF local d = ensureDriver() if d then d:Show() end end
  M.targetText = txt
  return txt
end

-- boussole (notre cadre TreeAICompassFrame uniquement) : pulsation + « Objectif proche : Loup ×3 »
local pulse, nearShown = nil, 0
function M.UpdateCompass()
  local CF = rawget(G, "TreeAICompassFrame")
  local count, first = 0, nil
  for _, inf in pairs(plateInfo) do
    count = count + 1
    first = first or inf
  end
  M.nearCount = count
  if not (on("compass") and type(CF) == "table" and meth(CF, "IsShown")) or count == 0 then
    if pulse then pulse:Hide() end
    nearShown = 0
    return count
  end
  if not pulse then
    pulse = G.CreateFrame("Frame", nil, G.UIParent)
    if pulse.EnableMouse then pulse:EnableMouse(false) end
    pulse.ring = pulse:CreateTexture(nil, "OVERLAY")
    pulse.ring:SetTexture(TEX.glow)
    if pulse.ring.SetBlendMode then pulse.ring:SetBlendMode("ADD") end
    pulse.ring:SetPoint("TOPLEFT", pulse, "TOPLEFT", -10, 10)
    pulse.ring:SetPoint("BOTTOMRIGHT", pulse, "BOTTOMRIGHT", 10, -10)
    pulse.text = pulse:CreateFontString(nil, "OVERLAY")
    fontOf(pulse.text, 11)
    pulse.text:SetPoint("TOP", pulse, "BOTTOM", 0, -2)
    local ag = pulse.ring.CreateAnimationGroup and pulse.ring:CreateAnimationGroup()
    if ag then
      local a = ag.CreateAnimation and ag:CreateAnimation("Alpha")
      if a and a.SetFromAlpha then a:SetFromAlpha(0.15) a:SetToAlpha(1) end
      if a and a.SetDuration then a:SetDuration(0.5) end
      if ag.SetLooping then ag:SetLooping("BOUNCE") end
      pulse.ag = ag
    end
  end
  pulse:ClearAllPoints()
  pulse:SetPoint("TOPLEFT", CF, "TOPLEFT")
  pulse:SetPoint("BOTTOMRIGHT", CF, "BOTTOMRIGHT")
  if pulse.SetFrameLevel then pulse:SetFrameLevel((tonumber(meth(CF, "GetFrameLevel")) or 1) + 5) end
  local r, g, b = color("colMob")
  pulse.ring:SetVertexColor(r, g, b, intensity())
  local nm = first and (first.name or first.e.obj) or "?"
  pulse.text:SetText(("Objectif proche : %s%s"):format(cut(nm, 24), count > 1 and (" ×" .. count) or ""))
  pulse.text:SetTextColor(r, g, b)
  pulse:Show()
  if pulse.ag and pulse.ag.Play and opt("anim") ~= false and not (pulse.ag.IsPlaying and pulse.ag:IsPlaying()) then pulse.ag:Play() end
  active[pulse] = CF
  local d = ensureDriver() if d then d:Show() end
  nearShown = count
  return count
end

-- ------------------------------------------------------------------ infobulles (hors combat)
function M.TooltipActive()
  if not on("tooltip") then return false end
  local questie = (type(rawget(G, "QuestieLoader")) == "table")
  if questie and not opt("tooltipWithQuestie") then return false end
  return true
end
local function addLine(tt, txt, r, g, b)
  if not (tt and tt.AddLine and txt) then return end
  pcall(tt.AddLine, tt, txt, r, g, b)
  if tt.Show then pcall(tt.Show, tt) end
end
function M.OnItemTooltip(tt, data)
  if not M.TooltipActive() or inCombat() then return nil end
  local id = type(data) == "table" and sv(data.id)
  local name, link
  if tt and tt.GetItem then name, link = try(tt.GetItem, tt) end
  id = id or itemIDOf(link)
  local kind, e = M.Classify(id, sv(name))
  if not kind then return nil end
  local txt = M.Describe(kind, e)
  local r, g, b = color(KIND_COL[kind] or "colNeed")
  if kind == "useless" then r, g, b = 0.6, 0.6, 0.6 end
  addLine(tt, txt, r, g, b)
  return txt
end
function M.OnUnitTooltip(tt)
  if not M.TooltipActive() or inCombat() then return nil end
  local _, unit = nil, nil
  if tt and tt.GetUnit then _, unit = try(tt.GetUnit, tt) end
  unit = sv(unit) or "mouseover"
  if not try(G.UnitExists, unit) then return nil end
  local e, how = M.UnitObjective(unit)
  if not e then return nil end
  local left = math.max(0, (e.need or 1) - (e.have or 0))
  local txt = ("Quête : %s — encore %d"):format(cut(e.quest, 34), left)
  if how == "drop" then txt = txt .. (e.rate and (" (chute %d %%)"):format(math.floor(e.rate * 100 + 0.5)) or " (butin)") end
  addLine(tt, txt, color("colMob"))
  return txt
end
local tipHooked = false
local function hookTooltips()
  if tipHooked then return end
  tipHooked = true
  local TDP, E = G.TooltipDataProcessor, G.Enum
  if TDP and TDP.AddTooltipPostCall and E and E.TooltipDataType then
    pcall(TDP.AddTooltipPostCall, E.TooltipDataType.Item, function(tt, data) if tt == G.GameTooltip or tt == G.ItemRefTooltip then M.OnItemTooltip(tt, data) end end)
    pcall(TDP.AddTooltipPostCall, E.TooltipDataType.Unit, function(tt) if tt == G.GameTooltip then M.OnUnitTooltip(tt) end end)
  elseif G.GameTooltip and G.GameTooltip.HookScript then
    pcall(G.GameTooltip.HookScript, G.GameTooltip, "OnTooltipSetItem", function(tt) M.OnItemTooltip(tt) end)
    pcall(G.GameTooltip.HookScript, G.GameTooltip, "OnTooltipSetUnit", function(tt) M.OnUnitTooltip(tt) end)
  end
end

-- ------------------------------------------------------------------ orchestration (événementiel, regroupé)
local pending = {}
local function schedule(what, delay)
  if pending[what] then return end
  pending[what] = true
  after(delay or 0.1, function()
    pending[what] = nil
    if not M:IsEnabled() then return end
    if what == "rebuild" then
      M.Rebuild()
      M.RefreshBags()
      M.RefreshPlates()
      M.UpdateTarget()
      M.PushTab()
    elseif what == "bags" then
      M.RefreshBags()
      M.PushTab()
    end
  end)
end
M.Schedule = schedule

function M.Cost() return { rebuild = M.rebuildCost or 0, bags = M.bagCost or 0 } end

local bagHooked = false
local function hookBagOpen()
  if bagHooked or type(G.hooksecurefunc) ~= "function" then return end
  bagHooked = true
  for _, fn in ipairs({ "ToggleBag", "OpenBag", "OpenAllBags", "ToggleAllBags", "OpenBackpack", "ToggleBackpack" }) do
    if type(rawget(G, fn)) == "function" then pcall(G.hooksecurefunc, fn, function() schedule("bags", 0.05) end) end
  end
end

function M:OnEnable()
  self:RegisterEvent("BAG_UPDATE_DELAYED", function() schedule("bags", 0.05) end)
  self:RegisterEvent("QUEST_LOG_UPDATE", function() schedule("rebuild", 0.6) end)
  self:RegisterEvent("QUEST_ACCEPTED", function() schedule("rebuild", 0.6) end)
  self:RegisterEvent("QUEST_TURNED_IN", function() schedule("rebuild", 0.6) end)
  self:RegisterEvent("GET_ITEM_INFO_RECEIVED", function() kindCache = {} schedule("bags", 0.3) end)
  if not (T.Lite and T:Lite()) then   -- (LITE) barres de nom / cible : seulement hors profil essentiel
    self:RegisterEvent("NAME_PLATE_UNIT_ADDED", function(_, _, unit) M.UpdatePlate(unit) M.UpdateCompass() end)
    self:RegisterEvent("NAME_PLATE_UNIT_REMOVED", function(_, _, unit) M.RemovePlate(unit) M.UpdateCompass() end)
    self:RegisterEvent("PLAYER_TARGET_CHANGED", function() M.UpdateTarget() end)
  end
  self:RegisterEvent("PLAYER_REGEN_ENABLED", function() tipScanCache = {} schedule("rebuild", 0.5) end)
  self:RegisterEvent("LOOT_OPENED", function() M.CheckLoot() end)
  self:RegisterEvent("LOOT_SLOT_CLEARED", function() M.CheckLoot() end)
  self:RegisterEvent("LOOT_CLOSED", function() M.OnLootClosed() end)
  self:RegisterEvent("START_LOOT_ROLL", function(_, _, rollID) M.OnRoll(rollID) end)
  self:RegisterEvent("PLAYER_ENTERING_WORLD", function() guidCache = {} tipScanCache = {} hookThirdParty() schedule("rebuild", 1) end)
  hookBagOpen()
  hookTooltips()
  hookThirdParty()
  schedule("rebuild", 1)
end

function M:OnDisable()
  for btn in pairs(overlays) do hideOverlay(btn) end
  for u in pairs(plates) do M.RemovePlate(u) end
  if badge then badge:Hide() end
  if pulse then pulse:Hide() end
  M.OnLootClosed()
end

function M:OnOptionChanged()
  kindCache, guidCache = {}, {}
  schedule("rebuild", 0.1)
end

if T.RegisterSlash then
  T:RegisterSlash("surbrillance", function()
    if not M:IsEnabled() then M:Print("Surbrillance désactivée (/tai module Highlight on).") return end
    M.Rebuild()
    M.RefreshBags()
    local r = M.ScanBags()
    M:Print(("Sacs : %d objectif(s), %d à rendre, %d lance(nt) une quête, %d pour plus tard, %d inutile(s). Monstres proches : %d. Coût %.2f ms + %.2f ms.")
      :format(#r.need + #r.use, #r.done, #r.starter, #r.upcoming, #r.useless, M.nearCount or 0, M.rebuildCost or 0, M.bagCost or 0))
  end, "surbrillance : état de la surbrillance des objets / monstres de quête")
end
