-- TreeAI — module Suivi des quêtes (agent QUEST-PRO). Complète Questie (épingles / suivi) sans doublon :
--  * suivi compact, trié par itinéraire conseillé (plus proche voisin sur les points d'objectifs / de remise),
--    objectifs + barres de progression, distance, « prête à rendre » en vert, XP ; clic = cible de la
--    boussole. Onglet « Quêtes » du panneau (TreeAI:SetTabLines) si présent, sinon petit cadre à nous.
--  * astuces légitimes : quêtes faciles à côté (Questie, pas encore prises), « passe prendre X chez Y en
--    chemin », suites de chaînes (« rendre ici → suite »), alerte « tu quittes le coin sans prendre… »,
--    infobulle des monstres qui lâchent un objet demandé (+ taux mesuré par Predict). Texte seulement.
-- Règles 3.8.2 : aucune fonction / aucun cadre de Blizzard touché (pas de suivi Blizzard modifié, pas de
-- sélection du journal) ; infobulles : AddLine sur GameTooltip via TooltipDataProcessor / HookScript.
-- Résumé IA : TreeAIDB.chars[perso].summary.tracker. Lua 5.1, aucune globale.
local _, ns = ...
ns = ns or {}

local T = _G.TreeAI
if not (T and T.RegisterModule) then return end

local M = T:RegisterModule("Tracker", {
  title = "Suivi des quêtes",
  defaults = { enabled = true, frame = true, easy = true, pickup = true, hubWarn = true, dropTips = true, maxLines = 12 },
  charDefaults = { point = "RIGHT", x = -40, y = 120, warned = {} },
  options = {
    { key = "frame", type = "toggle", label = "Petit cadre de suivi (si le panneau n'a pas d'onglets)" },
    { key = "easy", type = "toggle", label = "Quêtes faciles à côté (Questie)" },
    { key = "pickup", type = "toggle", label = "« Passe prendre … en chemin »" },
    { key = "hubWarn", type = "toggle", label = "Alerte : tu quittes un coin sans prendre ses quêtes" },
    { key = "dropTips", type = "toggle", label = "Infobulle : ce monstre lâche un objet de quête" },
  },
})
ns.Tracker = M
T.Tracker = M

local floor, min = math.floor, math.min

local function try(fn, ...)
  if type(fn) ~= "function" then return nil end
  local r = { pcall(fn, ...) }
  if not r[1] then return nil end
  return unpack(r, 2)
end
local function now() return try(_G.GetTime) or 0 end
local function cut(s, n) s = tostring(s or "") if #s > n then return s:sub(1, n - 1) .. "…" end return s end
local function low(s) return tostring(s or ""):lower() end
local function cfg(k) return not (M.db and M.db[k] == false) end
local function mod(name) return T.GetModule and T:GetModule(name) or nil end
local function fmtNum(n)
  local s = tostring(floor((n or 0) + 0.5))
  return (s:reverse():gsub("(%d%d%d)", "%1 "):reverse():gsub("^ ", ""))
end
local function playerLevel() return try(_G.UnitLevel, "player") or 1 end
local function inCombat() return T.InCombat and T:InCombat() or false end
-- Forever : certaines valeurs sont « secrètes » pour les addons en combat (comparer / concaténer = erreur)
local function secret(v) local f = rawget(_G, "issecretvalue") return type(f) == "function" and f(v) and true or false end
local function shift() return _G.IsShiftKeyDown and try(_G.IsShiftKeyDown) and true or false end

-- ------------------------------------------------------------------ pur : barres, itinéraire
function M.Bar(have, need, width)
  width = width or 8
  need = (need and need > 0) and need or 1
  local n = floor(min(1, (have or 0) / need) * width + 0.5)
  return "[" .. ("="):rep(n) .. ("-"):rep(width - n) .. "]"
end

-- Plus proche voisin : items = { {key, p = {mapID,x,y}} } ; dist(a, b) → yards|nil. Départ = me.
function M.Route(items, me, dist)
  local left, order = {}, {}
  for _, it in ipairs(items) do if it.p then left[#left + 1] = it end end
  local cur = me
  while #left > 0 do
    local bi, bd
    for i, it in ipairs(left) do
      local d = cur and dist(cur, it.p)
      if d and (not bd or d < bd) then bi, bd = i, d end
    end
    if not bi then break end
    local it = table.remove(left, bi)
    it.leg = bd
    order[#order + 1] = it
    cur = it.p
  end
  for _, it in ipairs(left) do order[#order + 1] = it end  -- inaccessibles (autre continent) à la fin
  return order
end

-- distance d'un point p au segment [a, b] (en yards, via la géométrie de la boussole)
function M.DetourDist(a, b, p, vector)
  local dab = vector(a, b)
  local dap, bap = vector(a, p)
  local dbp = vector(b, p)
  if not (dab and dap and dbp) then return nil end
  if dab < 1 then return dap end
  local _, bab = vector(a, b)
  -- projection
  local along = dap * math.cos(bap - bab)
  if along <= 0 then return dap end
  if along >= dab then return dbp end
  return math.abs(dap * math.sin(bap - bab))
end

-- ------------------------------------------------------------------ données
local function compass() return T.Compass end
local function geo() local C = compass() return C and C.Geo end

-- Quêtes du journal enrichies : point le plus proche, distance, ordre.
function M.Build()
  local Q, C = mod("Quests"), compass()
  local list = Q and Q.lastList or {}
  local me = C and C.Player() or nil
  local vector = geo() and geo().Vector
  local items = {}
  for _, q in ipairs(list) do
    if not q.failed then
      local p, d
      if C then
        local pts = C.QuestPoints(q, me)
        p, d = C.Nearest(pts, me)
      end
      items[#items + 1] = { key = q.id, q = q, p = p and p.mapID and p or nil, dist = d or q.dist, npc = p and p.name }
    end
  end
  local order = vector and M.Route(items, me, function(a, b) return (vector(a, b)) end) or items
  -- efficacité = XP / (distance + 200 yd) ; quêtes finies d'abord si à moins de 400 yd
  for i, it in ipairs(order) do
    it.rank = i
    it.eff = (it.q.xp or 0) / ((it.dist or 3000) + 200)
    -- temps réel de trajet (Shortest Path Forever, hors combat, 8 premières)
    it.travel = (i <= 8 and C and C.Travel and it.p) and C.Travel(me, it.p) or nil
  end
  M.me, M.items = me, order
  return order
end

-- ------------------------------------------------------------------ astuces Questie
local function questieDB()
  local C = compass()
  if C and C.Questie then return C.Questie() end
  return nil
end
local function starterPoints(id, me)
  local db, zdb = questieDB()
  local C = compass()
  if not (db and C) then return nil end
  local sb = try(db.QueryQuestSingle, id, "startedBy")
  if type(sb) ~= "table" then return nil end
  local out = {}
  for _, npc in ipairs(type(sb[1]) == "table" and sb[1] or {}) do C.SpawnsOf("npc", npc, db, zdb, me and me.mapID, out) end
  for _, ob in ipairs(type(sb[2]) == "table" and sb[2] or {}) do C.SpawnsOf("obj", ob, db, zdb, me and me.mapID, out) end
  return out
end
M.StarterPoints = starterPoints

-- quêtes disponibles ici (Quests.QuestieScan) avec la position du donneur
function M.Available(me)
  local Q, C = mod("Quests"), compass()
  local qs = Q and Q.lastQuestie
  if not (qs and C and me) then return {} end
  local out = {}
  for _, e in ipairs(qs.here or {}) do
    local p, d = C.Nearest(starterPoints(e.id, me) or {}, me)
    if p and d then
      out[#out + 1] = { id = e.id, title = e.title, level = e.level, giver = e.giver or p.name, p = p, dist = d }
    end
  end
  table.sort(out, function(a, b) return a.dist < b.dist end)
  return out
end

-- « faciles à côté » : donneur à < 300 yd de toi ou d'un de tes objectifs, niveau ≤ le tien + 1
function M.EasyNearby(avail, items)
  local vector = geo() and geo().Vector
  local out = {}
  if not vector then return out end
  local pl = playerLevel()
  for _, a in ipairs(avail) do
    if (a.level or 0) <= pl + 1 then
      local near = a.dist <= 300
      if not near then
        for _, it in ipairs(items or {}) do
          local d = it.p and vector(it.p, a.p)
          if d and d <= 250 then near = true break end
        end
      end
      if near then out[#out + 1] = a end
    end
  end
  return out
end

-- « passe prendre » : donneur à < 120 yd du trajet vers la cible de la boussole (et pas déjà à côté)
function M.OnTheWay(avail, me, target)
  local vector = geo() and geo().Vector
  if not (vector and me and target and target.mapID) then return nil end
  local best, bd
  for _, a in ipairs(avail) do
    if a.dist > 60 then
      local d = M.DetourDist(me, target, a.p, vector)
      local total = vector(me, target)
      if d and d < 120 and total and a.dist < total and (not bd or d < bd) then best, bd = a, d end
    end
  end
  return best
end

-- Alerte de départ : on mémorise le « coin » quand tu es à < 40 yd d'un donneur dont la quête n'est pas
-- prise ; à > 200 yd de ce coin, si elles ne sont toujours pas prises → une alerte par coin.
local hub
function M.CheckHub(avail, me)
  if not (cfg("hubWarn") and me) then return nil end
  local vector = geo() and geo().Vector
  if not vector then return nil end
  local close = {}
  for _, a in ipairs(avail) do if a.dist <= 40 then close[#close + 1] = a end end
  if #close > 0 then
    hub = { p = { mapID = me.mapID, x = me.x, y = me.y }, quests = close }
    return nil
  end
  if not hub then return nil end
  local d = vector(me, hub.p)
  if d and d > 200 then
    local inLog = {}
    for _, q in ipairs((mod("Quests") or {}).lastList or {}) do inLog[q.id] = true end
    local missed = {}
    for _, a in ipairs(hub.quests) do if not inLog[a.id] then missed[#missed + 1] = a end end
    hub = nil
    if #missed > 0 then
      local key = tostring(missed[1].id)
      if not M.char.warned[key] then
        M.char.warned[key] = true
        local names = {}
        for i = 1, min(3, #missed) do names[#names + 1] = "« " .. cut(missed[i].title, 24) .. " »" .. (missed[i].giver and (" (" .. missed[i].giver .. ")") or "") end
        local text = "Tu pars sans prendre : " .. table.concat(names, ", ") .. (#missed > 3 and (" +" .. (#missed - 3)) or "")
        M:Suggest("hub", text, 2, { ttl = 90, cooldown = 600 })
        return text
      end
    end
  end
  return nil
end

-- objets demandés lâchés par des monstres : [nom du monstre en minuscules] = { item, have, need, quest }
function M.NeededDrops()
  local Q = mod("Quests")
  local db = questieDB()
  local out = {}
  if not (Q and Q.lastList) then return out end
  for _, q in ipairs(Q.lastList) do
    if not q.complete and db then
      local objs = try(db.QueryQuestSingle, q.id, "objectives")
      local items = type(objs) == "table" and objs[3]
      for _, it in ipairs(type(items) == "table" and items or {}) do
        local itemID = type(it) == "table" and it[1] or it
        if type(itemID) == "number" and db.QueryItemSingle then
          local iname = try(db.QueryItemSingle, itemID, "name") or (T.API and T.API.ItemName and T.API.ItemName(itemID))
          local have, need, done
          for _, o in ipairs(q.obj or {}) do
            if iname and low(o.name) == low(iname) then have, need, done = o.have, o.need, o.finished end
          end
          if not done then
            for _, npc in ipairs(try(db.QueryItemSingle, itemID, "npcDrops") or {}) do
              local nm = try(db.QueryNPCSingle, npc, "name")
              if nm then out[low(nm)] = { item = iname or ("objet " .. itemID), have = have, need = need, quest = q.title } end
            end
          end
        end
      end
    end
  end
  M.drops = out
  return out
end

function M.DropTip(unitName)
  if not cfg("dropTips") or not unitName or secret(unitName) or inCombat() then return nil end
  local d = (M.drops or {})[low(unitName)]
  if not d then return nil end
  local P = T.Predict
  local rate = P and P.DropRate and P.DropRate(unitName, d.item)
  local prog = (d.have and d.need) and (" " .. d.have .. "/" .. d.need) or ""
  return ("TreeAI : lâche %s%s%s"):format(cut(d.item, 30), prog, rate and (" (~" .. floor(rate * 100 + 0.5) .. " %)") or "")
end

local hooked
local function hookTips()
  if hooked then return end
  hooked = true
  local function onUnit(tt)
    if tt ~= _G.GameTooltip then return end
    local name = tt.GetUnit and try(tt.GetUnit, tt)
    local line = M.DropTip(name)
    if line and tt.AddLine then pcall(tt.AddLine, tt, line, 1, 0.82, 0.2) end
  end
  local TDP, E = _G.TooltipDataProcessor, _G.Enum
  if TDP and TDP.AddTooltipPostCall and E and E.TooltipDataType then
    pcall(TDP.AddTooltipPostCall, E.TooltipDataType.Unit, onUnit)
  elseif _G.GameTooltip and _G.GameTooltip.HookScript then
    pcall(_G.GameTooltip.HookScript, _G.GameTooltip, "OnTooltipSetUnit", onUnit)
  end
end

-- ------------------------------------------------------------------ lignes
local function objLines(q)
  local o = {}
  for _, ob in ipairs(q.obj or {}) do
    local col = ob.finished and "|cff55ff55" or "|cffdddddd"
    o[#o + 1] = ("%s%s %s %d/%d|r"):format(col, M.Bar(ob.have, ob.need, 6), cut(ob.name, 28), ob.have or 0, ob.need or 1)
  end
  return o
end

function M.Lines()
  local Q, C = mod("Quests"), compass()
  local G = geo()
  local units = C and C.db and C.db.units
  local lines = {}
  if Q and Q.nextText then lines[#lines + 1] = { text = Q.nextText, prio = 2 } end
  for _, it in ipairs(M.items or {}) do
    local q = it.q
    local parts = {}
    local head
    if q.complete then
      head = ("|cff33ff33Prête à rendre|r %s"):format(cut(q.title, 30))
      local nx = Q and Q.QuestieNext and try(Q.QuestieNext, q.id)
      if nx then parts[#parts + 1] = "→ " .. nx:gsub("^Suite : ", "suite ") end
      if it.npc then parts[#parts + 1] = it.npc end
    else
      head = ("[%d] %s"):format(q.level or 0, cut(q.title, 30))
      local o = {}
      for _, ob in ipairs(q.obj or {}) do
        if not ob.finished then o[#o + 1] = M.Bar(ob.have, ob.need, 5) .. " " .. ob.have .. "/" .. ob.need end
        if #o >= 2 then break end
      end
      if #o > 0 then parts[#parts + 1] = table.concat(o, " ") end
    end
    if it.dist and G then parts[#parts + 1] = G.Dist(it.dist, units) .. (it.travel and (" ~" .. G.ETA(it.travel * 7, 7)) or "") elseif q.zone and q.zone ~= (Q and Q.last and Q.last.zone) then parts[#parts + 1] = cut(q.zone, 20) end
    if q.xp then parts[#parts + 1] = fmtNum(q.xp) .. " XP" end
    local qid = q.id
    local tip = objLines(q)
    table.insert(tip, 1, q.title)
    lines[#lines + 1] = {
      text = it.rank .. ". " .. head .. (#parts > 0 and (" · " .. table.concat(parts, " · ")) or ""),
      prio = q.complete and 2 or 1, tooltip = table.concat(tip, "\n"),
      -- clic = cible de la boussole ; Maj-clic = « Aller là » (Shortest Path / TomTom / repère), clic du joueur
      onClick = function()
        local t = C and C.SetQuestTarget and C.SetQuestTarget(qid)
        if t and shift() and C.GoTo then C.GoTo(t) end
      end,
      questID = qid,
    }
    if #lines >= (tonumber(M.db.maxLines) or 12) then break end
  end
  for _, a in ipairs(M.easy or {}) do
    lines[#lines + 1] = { text = ("|cff88ccffFacile à côté|r : « %s » (%s, %s)"):format(cut(a.title, 28), a.giver or "?",
      G and G.Dist(a.dist, units) or "?"), prio = 1 }
  end
  if M.pickup then
    lines[#lines + 1] = { text = ("Passe prendre « %s » chez %s en chemin"):format(cut(M.pickup.title, 28), M.pickup.giver or "?"), prio = 2 }
  end
  if #lines == 0 then lines[1] = { text = "Aucune quête dans le journal.", prio = 1 } end
  return lines
end

-- ------------------------------------------------------------------ affichage : onglet ou cadre
local frame, rows = nil, {}
local function createFrame()
  if frame or type(_G.CreateFrame) ~= "function" then return frame end
  frame = _G.CreateFrame("Frame", "TreeAITrackerFrame", _G.UIParent)
  frame:SetSize(300, 20)
  frame:SetPoint(M.char.point or "RIGHT", _G.UIParent, M.char.point or "RIGHT", M.char.x or -40, M.char.y or 120)
  frame:SetMovable(true)
  frame:EnableMouse(true)
  frame:SetClampedToScreen(true)
  frame:RegisterForDrag("LeftButton")
  frame:SetScript("OnDragStart", function(f) f:StartMoving() end)
  frame:SetScript("OnDragStop", function(f)
    f:StopMovingOrSizing()
    local p, _, _, x, y = f:GetPoint(1)
    M.char.point, M.char.x, M.char.y = p or "RIGHT", x or -40, y or 120
  end)
  local title = frame:CreateFontString(nil, "OVERLAY", "GameFontNormal")
  title:SetPoint("TOPLEFT", frame, "TOPLEFT", 2, -2)
  title:SetText("Suivi TreeAI")
  frame:Hide()
  return frame
end

local function row(i)
  if rows[i] then return rows[i] end
  local b = _G.CreateFrame("Button", nil, frame)
  b:SetSize(300, 14)
  b:SetPoint("TOPLEFT", frame, "TOPLEFT", 2, -4 - i * 14)
  b.fs = b:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
  b.fs:SetAllPoints(b)
  if b.fs.SetJustifyH then b.fs:SetJustifyH("LEFT") end
  b:SetScript("OnClick", function(self) if self.line and self.line.onClick then pcall(self.line.onClick) end end)
  b:SetScript("OnEnter", function(self)
    local tt = _G.GameTooltip
    if not (tt and self.line and self.line.tooltip) then return end
    pcall(tt.SetOwner, tt, self, "ANCHOR_LEFT")
    for l in tostring(self.line.tooltip):gmatch("[^\n]+") do pcall(tt.AddLine, tt, l, 1, 1, 1) end
    pcall(tt.Show, tt)
  end)
  b:SetScript("OnLeave", function() if _G.GameTooltip then pcall(_G.GameTooltip.Hide, _G.GameTooltip) end end)
  rows[i] = b
  return b
end

local lastPush, pushPending = 0, false
function M.Push()
  local lines = M.Lines()
  M.lines = lines
  if T.SetTabLines then
    pcall(T.SetTabLines, T, "quests", lines)
    if frame then frame:Hide() end
    return "tab"
  end
  if not cfg("frame") then if frame then frame:Hide() end return "off" end
  createFrame()
  if not frame then return "none" end
  local n = min(#lines, 14)
  for i = 1, n do
    local b = row(i)
    b.line = lines[i]
    b.fs:SetText(lines[i].text)
    b:Show()
  end
  for i = n + 1, #rows do rows[i]:Hide() end
  frame:SetHeight(20 + n * 14)
  frame:Show()
  return "frame"
end

local function pushSoon()
  if pushPending then return end
  local wait = math.max(0, 0.5 - (now() - lastPush))
  pushPending = true
  T:After(wait, function()
    pushPending = false
    lastPush = now()
    pcall(M.Push)
  end)
end

-- ------------------------------------------------------------------ mise à jour
function M.Update()
  -- planification lourde hors combat seulement ; refaite en sortie de combat
  if inCombat() then M.dirty = true return M.items end
  M.dirty = nil
  local order = M.Build()
  local me = M.me
  local avail = M.Available(me)
  M.avail = avail
  M.easy = cfg("easy") and M.EasyNearby(avail, order) or {}
  local C = compass()
  local target = C and C.Current and C.Current()
  M.pickup = cfg("pickup") and M.OnTheWay(avail, me, target) or nil
  if M.pickup then
    M:Suggest("pickup", ("Passe prendre « %s » chez %s en chemin."):format(cut(M.pickup.title, 28), M.pickup.giver or "?"), 2,
      { ttl = 120, cooldown = 900 })
  end
  if #M.easy > 0 then
    local a = M.easy[1]
    M:Suggest("easy", ("Facile à côté : « %s » (%s)%s"):format(cut(a.title, 28), a.giver or "?",
      #M.easy > 1 and (" +" .. (#M.easy - 1)) or ""), 1, { ttl = 120, cooldown = 1200, sound = false })
  end
  M.CheckHub(avail, me)
  M.NeededDrops()
  M.WriteSummary()
  pushSoon()
  return order
end

function M.WriteSummary()
  local s = T.Summary and T:Summary()
  if type(s) ~= "table" then return end
  local route = {}
  for i, it in ipairs(M.items or {}) do
    if i > 15 then break end
    route[#route + 1] = { id = it.q.id, title = cut(it.q.title, 60), ready = it.q.complete or nil,
      dist = it.dist and floor(it.dist + 0.5) or nil, xp = it.q.xp, zone = it.q.zone,
      done = it.q.done, total = it.q.total }
  end
  local easy = {}
  for i, a in ipairs(M.easy or {}) do
    if i > 5 then break end
    easy[#easy + 1] = { id = a.id, title = cut(a.title, 60), giver = a.giver, dist = floor(a.dist + 0.5) }
  end
  local nd = 0
  for _ in pairs(M.drops or {}) do nd = nd + 1 end
  s.tracker = {
    updated = try(_G.time), route = route, easyNearby = easy, dropMobs = nd,
    pickup = M.pickup and { id = M.pickup.id, title = cut(M.pickup.title, 60), giver = M.pickup.giver } or nil,
    available = #(M.avail or {}),
  }
end

local pending
function M.Refresh(delay)
  if pending then return end
  pending = true
  T:After(delay or 1, function() pending = false pcall(M.Update) end)
end

function M:OnEnable()
  if T.RegisterTab then pcall(T.RegisterTab, T, "quests", "Quêtes", 20) end
  for _, ev in ipairs({ "QUEST_LOG_UPDATE", "QUEST_ACCEPTED", "QUEST_REMOVED", "QUEST_TURNED_IN",
    "ZONE_CHANGED_NEW_AREA", "PLAYER_LEVEL_UP" }) do
    self:RegisterEvent(ev, function() M.Refresh(1.2) end)
  end
  self:On("SUMMARY", function() pcall(M.WriteSummary) end)
  self:On("QUESTS_UPDATED", function() M.Refresh(1) end)
  self:RegisterEvent("PLAYER_REGEN_ENABLED", function() if M.dirty then M.Refresh(1.5) end end)
  hookTips()
  if not M.ticker then M.ticker = T:NewTicker(4, function() pcall(M.Update) end) end
  M.Refresh(2.5)
end

function M:OnDisable()
  if M.ticker and M.ticker.Cancel then M.ticker:Cancel() end
  M.ticker = nil
  if frame then frame:Hide() end
end

function M:OnOptionChanged() M.Refresh(0.2) end

-- Itinéraire complet confié à Shortest Path Forever (NavigateRoute, ≤ 64 étapes) — sur commande du joueur.
function M.RouteStops()
  local stops = {}
  for _, it in ipairs(M.items or {}) do
    if it.p and #stops < 64 then
      local q = it.q
      stops[#stops + 1] = { map = it.p.mapID, x = it.p.x, y = it.p.y, questID = q.id,
        title = cut((q.complete and "Rendre « " .. q.title .. " »") or q.title, 60),
        tooltip = q.complete and "Prête à rendre" or (mod("Quests") and mod("Quests").Remaining and mod("Quests").Remaining(q, 3)) or nil }
    end
  end
  return stops
end
function M.SendRoute()
  if not M.items then M.Update() end
  local stops = M.RouteStops()
  local C = compass()
  if #stops == 0 then M:Print("Itinéraire : aucun point connu (Questie / API de quêtes).") return nil end
  local A = C and C.SPF and C.SPF()
  if A and not inCombat() and C.SPFCall(A, "NavigateRoute", "TreeAI", stops) then
    M:Print(("Itinéraire (%d étapes) envoyé à Shortest Path."):format(#stops))
    return "spf"
  end
  if C and C.GoTo and #M.items > 0 then
    C.SetQuestTarget(M.items[1].q.id)
    return C.GoTo()
  end
end
T:RegisterSlash("itineraire", function() M.SendRoute() end, "itinéraire conseillé → Shortest Path (ou boussole)")
T:RegisterSlash("itinéraire", function() M.SendRoute() end)

T:RegisterSlash("suivi", function(rest)
  if low(rest):find("cach") or low(rest):find("off") then M.db.frame = false if frame then frame:Hide() end return end
  M.db.frame = true
  M.Update()
  M.Push()
  for i, l in ipairs(M.lines or {}) do
    if i > 14 then break end
    M:Print(l.text)
  end
end, "suivi des quêtes : itinéraire conseillé, distances, faciles à côté")
