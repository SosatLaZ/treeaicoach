-- TreeAI — module Region (agent MENU) : suivi automatique des quêtes PAR RÉGION, dans la vue de TreeAI.
--  * L'onglet « Quêtes » (et la section « Quête en cours » de « Maintenant ») regroupe les quêtes :
--      Ici (zone actuelle) → Zones voisines (triées par temps de trajet) → Plus loin.
--    Recalculé à ZONE_CHANGED_NEW_AREA (et quand le journal change) ; rien n'est recalculé en combat.
--  * « Mauvaise région » (autre continent, trop haut niveau, élite seul, donjon, trop loin) : la quête reste listée
--    et peut rester conseillée si c'est la meilleure option, mais avec une ligne claire :
--      « Région déconseillée : élite, tu es seul — on y va quand même parce que c'est prêt à rendre »
--  * Jamais d'appel automatique au suivi d'objectifs de Blizzard (souillure / règles) : seulement sur clic
--    explicite du bouton d'onglet « Suivi du jeu » (UserAction, hors combat). Questie / Shortest Path : rien
--    ne leur est envoyé, on ordonne seulement la vue de TreeAI.
-- Lecture seule de Quests.lastList / Tracker.items. Lua 5.1, aucune globale.
local tonumber = function(v) return _G.tonumber(v) end   -- (4.0.8) tonumber(f()) : un 2e retour de f n'est jamais une base
local T = _G.TreeAI
if not (T and T.RegisterModule) then return end

local R = T:RegisterModule("Region", {
  title = "Quêtes par région",
  defaults = { enabled = true, group = true, warn = true, watchButton = true, farYd = 3000, farSec = 480 },
  options = {
    { key = "group", type = "toggle", label = "Page Quêtes : regrouper Ici > Zones voisines > Plus loin" },
    { key = "warn", type = "toggle", label = "Dire quand une quête est dans une région déconseillée (et pourquoi)" },
    { key = "watchButton", type = "toggle", label = "Bouton « Suivi du jeu » (ajoute les quêtes de la zone au suivi, sur clic)" },
  },
})
T.Region = R
if type(T.MODULE_INFO) == "table" and not T.MODULE_INFO.Region then
  T.MODULE_INFO.Region = { lite = true, desc = "Quêtes rangées par région (ici, à côté, loin)" }
end

local floor, max = math.floor, math.max
local fmt = string.format
local function pass(ok, ...) if ok then return ... end return nil end
local function try(fn, ...) if type(fn) ~= "function" then return nil end return pass(pcall(fn, ...)) end
local function G(name) return rawget(_G, name) end
local function cfg(k) return not (R.db and R.db[k] == false) end
local function num(k, d) return tonumber(R.db and R.db[k]) or d end
local function mod(name) return T.GetModule and T:GetModule(name) or nil end
local function secret(v) local f = G("issecretvalue") return type(f) == "function" and f(v) and true or false end

R.BANDS = { here = 1, near = 2, far = 3 }
R.BAND_LABEL = { here = "Ici", near = "Zones voisines", far = "Plus loin" }
R.BAND_ICON = { here = "Interface\\Icons\\INV_Misc_Map_01", near = "Interface\\Icons\\Ability_Rogue_Sprint",
  far = "Interface\\Minimap\\Tracking\\FlightMaster" }

function R.Zone() return try(G("GetRealZoneText")) or try(G("GetZoneText")) or "" end
function R.GroupSize()
  local n = try(G("GetNumGroupMembers"))
  if type(n) == "number" and n > 0 then return n end
  local p = try(G("GetNumPartyMembers"))
  return (type(p) == "number" and p or 0) + 1
end

-- ------------------------------------------------------------------ classement (pur : testé)
--- q = quête de Quests.lastList ; it = entrée Tracker (dist, travelSec, xpm, mates) ; ctx = { zone, level, group, onCont }
--- → { band, reasons = { … }, bad, dist, sec }
function R.ClassifyRaw(q, it, ctx)
  local zone = ctx.zone or ""
  local qz = (q.complete and q.turnZone) or q.zone
  local dist = (it and it.dist) or q.dist
  local sec = it and it.travelSec
  local band
  if qz and qz ~= "" and qz == zone then band = "here"
  elseif ctx.onCont == false then band = "far"
  elseif (sec and sec <= num("farSec", 480)) or (dist and dist <= num("farYd", 3000)) then band = "near"
  else band = "far" end
  local reasons = {}
  if ctx.onCont == false then reasons[#reasons + 1] = "autre continent" end
  local pl, ql = tonumber(ctx.level) or 1, tonumber(q.level) or 0
  if ql > 0 and ql >= pl + 5 and not q.complete then reasons[#reasons + 1] = fmt("trop haut niveau (niv %d, toi %d)", ql, pl) end
  local k = max(1, tonumber(ctx.group) or 1)
  if not q.complete then
    if q.dungeon then reasons[#reasons + 1] = "quête de donjon"
    elseif q.elite and k == 1 then reasons[#reasons + 1] = "élite, tu es seul"
    elseif (tonumber(q.suggestedGroup) or 0) > k and k == 1 then reasons[#reasons + 1] = fmt("groupe de %d conseillé", q.suggestedGroup) end
  end
  if band == "far" and ctx.onCont ~= false then
    if sec and sec > num("farSec", 480) then reasons[#reasons + 1] = fmt("loin (~%d min de trajet)", floor(sec / 60 + 0.5))
    elseif dist and dist > num("farYd", 3000) then reasons[#reasons + 1] = "loin d'ici" end
  end
  return { band = band, reasons = reasons, bad = #reasons > 0, dist = dist, sec = sec, zone = qz }
end

--- Pourquoi on la garde quand même (meilleure option).
function R.WhyAnyway(q, it, ctx)
  if q.complete then return "elle est prête à rendre (XP tout de suite)" end
  local open = 0
  for _, g in ipairs((it and it.mates) or {}) do if not g.done then open = open + 1 end end
  if open > 0 then return "ton groupe l'a aussi" end
  if (ctx.hereOK or 0) == 0 then return "il n'y a plus rien de mieux dans cette zone" end
  return "c'est la meilleure XP/min de ton journal"
end

function R.WarnText(info, why)
  return fmt("Région déconseillée : %s — on y va quand même parce que %s", table.concat(info.reasons, ", "), why)
end

-- ------------------------------------------------------------------ cache (journal + zone)
local cache = { list = nil, n = -1, zone = nil, level = nil, items = nil, map = {} }
function R.Invalidate() cache.list = nil end

local function questsIndex()
  local Q = mod("Quests")
  local list = Q and Q.lastList or {}
  local Tr = T.Tracker
  local items = Tr and Tr.items
  local zone, level = R.Zone(), try(G("UnitLevel"), "player") or 1
  if cache.list == list and cache.n == #list and cache.zone == zone and cache.level == level and cache.items == items then
    return cache
  end
  -- en combat : on garde l'ancien classement (aucun calcul, aucune API)
  if cache.list and T.InCombat and T:InCombat() then return cache end
  local byIt = {}
  for _, it in ipairs(items or {}) do if it.key then byIt[it.key] = it end end
  local ctx = { zone = zone, level = level, group = R.GroupSize() }
  local map, hereOK = {}, 0
  local QL = G("C_QuestLog")
  local dist2 = QL and QL.GetDistanceSqToQuest
  for _, q in ipairs(list) do
    if q.id and not secret(q.title) and not secret(q.complete) then
      local onCont
      if dist2 then
        local d2, oc = try(dist2, q.id)
        if type(d2) == "number" and d2 >= 0 then onCont = oc ~= false end
      end
      ctx.onCont = onCont
      local info = R.ClassifyRaw(q, byIt[q.id], ctx)
      info.q, info.it = q, byIt[q.id]
      map[q.id] = info
      if info.band == "here" and not info.bad and not q.grey then hereOK = hereOK + 1 end
    end
  end
  ctx.onCont = nil
  ctx.hereOK = hereOK
  cache.list, cache.n, cache.zone, cache.level, cache.items, cache.map, cache.ctx = list, #list, zone, level, items, map, ctx
  return cache
end
R._Index = questsIndex

function R.Info(qid)
  qid = tonumber(qid)
  if not qid then return nil end
  return questsIndex().map[qid]
end

--- quête d'une ligne : questID, sinon l'étape « À faire maintenant » de Todo (même texte), sinon nil
function R.QuestOf(e)
  if type(e) ~= "table" then return nil end
  if e.questID then return e.questID end
  if type(e.data) == "table" and tonumber(e.data.questID) then return tonumber(e.data.questID) end
  local Td = T.Todo
  local a = Td and Td.model and Td.model.now
  if e.pin and type(a) == "table" and a.questID and type(e.text) == "string" and type(a.title) == "string"
      and e.text:find(a.title, 1, true) then
    return a.questID
  end
  return nil
end

--- Rang de région d'une ligne (1 ici, 2 voisine, 3 loin, 2 sans quête) : pour trier « Quête en cours ».
function R.Rank(e)
  if not cfg("group") then return 2 end
  local info = R.Info(R.QuestOf(e))
  return info and R.BANDS[info.band] or 2
end

--- Ligne de mise en garde pour une ligne épinglée (section « Quête en cours ») ou nil.
function R.NowNote(e)
  if not cfg("warn") then return nil end
  local info = R.Info(R.QuestOf(e))
  if not (info and info.bad) then return nil end
  return R.WarnText(info, R.WhyAnyway(info.q, info.it, questsIndex().ctx or {}))
end

local function copy(e)
  local c = {}
  for k, v in pairs(e) do c[k] = v end
  return c
end

--- Onglet « Quêtes » : lignes du bloc principal (Todo / Tracker) regroupées par région ; les autres blocs suivent.
--- Chaque ligne reçoit _band / _bandLabel (titres de section du panneau). Renvoie une NOUVELLE liste.
function R.Arrange(list)
  if not (cfg("group") and type(list) == "table") then return list end
  local idx = questsIndex()
  local zone = idx.zone or ""
  local pre, bands, post, others = {}, { here = {}, near = {}, far = {} }, {}, {}
  local seenQuest, firstQ = false, nil
  for _, e in ipairs(list) do
    local qid = e.questID
    local info = qid and idx.map[qid]
    if e.source ~= "main" then others[#others + 1] = e
    elseif info then
      seenQuest = true
      firstQ = firstQ or e
      local b = bands[info.band]
      b[#b + 1] = e
    elseif e.header and not e.onClick then   -- ancien titre de zone : remplacé par nos sections
    elseif seenQuest then post[#post + 1] = e
    else pre[#pre + 1] = e end
  end
  if not firstQ then return list end
  -- zones voisines : par temps de trajet (puis distance)
  local near = bands.near
  for i, e in ipairs(near) do e._ord = i end
  table.sort(near, function(a, b)
    local ia, ib = idx.map[a.questID], idx.map[b.questID]
    local sa = ia.sec or (ia.dist and ia.dist / 7) or 1e9
    local sb = ib.sec or (ib.dist and ib.dist / 7) or 1e9
    if sa ~= sb then return sa < sb end
    return a._ord < b._ord
  end)
  local Td = T.Todo
  local nowQ = Td and Td.model and Td.model.now and Td.model.now.questID
  local best = nowQ and idx.map[nowQ] and nowQ or firstQ.questID
  local out = {}
  for _, e in ipairs(pre) do e._band, e._bandLabel = "step", "En cours"; out[#out + 1] = e end
  for _, band in ipairs({ "here", "near", "far" }) do
    local label = band == "here" and (R.BAND_LABEL.here .. (zone ~= "" and (" · " .. zone) or "")) or R.BAND_LABEL[band]
    for _, e in ipairs(bands[band]) do
      local info = idx.map[e.questID]
      local row = e
      if info.bad and cfg("warn") then
        row = copy(e)
        if e.questID ~= best then
          row.text = e.text .. " |cffff9933· déconseillée|r"
          local tip = { "|cffff9933Région déconseillée|r : " .. table.concat(info.reasons, ", ") }
          if type(e.tooltip) == "table" then for _, l in ipairs(e.tooltip) do tip[#tip + 1] = l end
          elseif e.tooltip then tip[#tip + 1] = e.tooltip end
          row.tooltip = tip
        end
      end
      row._band, row._bandLabel = band, label
      out[#out + 1] = row
      if info.bad and cfg("warn") and e.questID == best then
        out[#out + 1] = { text = R.WarnText(info, R.WhyAnyway(info.q, info.it, idx.ctx or {})), prio = 2, kind = "warn",
          source = "main", _band = band, _bandLabel = label, warnFor = e.questID,
          tooltip = { "Cette quête reste la meilleure option, mais la région n'est pas idéale.",
            "Raisons : " .. table.concat(info.reasons, ", ") } }
      end
    end
  end
  for _, e in ipairs(post) do e._band, e._bandLabel = "misc", "Autour de toi"; out[#out + 1] = e end
  for _, e in ipairs(others) do e._band, e._bandLabel = nil, nil; out[#out + 1] = e end
  return out
end

-- ------------------------------------------------------------------ « Suivi du jeu » (clic explicite seulement)
R.WATCH_MAX = 5
--- Ajoute au suivi d'objectifs du jeu les quêtes de la zone (au plus 5, non suivies). Seulement depuis un clic.
-- (QA-QUEST F27) réponse au clic : une ligne courte dans « Maintenant » (6 s), jamais le chat
local function say(text) pcall(R.Suggest, R, "watch", text, 1, { ttl = 6, sound = false }) end
function R.WatchZone()
  if not (T.InUserAction and T:InUserAction()) then return 0, "clic" end
  if T.InCombat and T:InCombat() then say("Suivi du jeu : impossible en combat") return 0, "combat" end
  local idx = questsIndex()
  local QL = G("C_QuestLog")
  local addModern = QL and QL.AddQuestWatch
  local addOld = G("AddQuestWatch")
  if not (addModern or addOld) then say("Le suivi du jeu n'est pas disponible ici") return 0, "api" end
  local list = {}
  for _, info in pairs(idx.map) do
    if info.band == "here" and not info.q.watched then list[#list + 1] = info.q end
  end
  table.sort(list, function(a, b)
    if (a.complete and 1 or 0) ~= (b.complete and 1 or 0) then return a.complete and true or false end
    return (a.id or 0) < (b.id or 0)
  end)
  local n = 0
  for _, q in ipairs(list) do
    if n >= R.WATCH_MAX then break end
    local ok
    if addModern then ok = pcall(addModern, q.id) else ok = q.index and pcall(addOld, q.index) end
    if ok then n = n + 1 end
  end
  say(n > 0 and fmt("%d quête%s ajoutée%s au suivi du jeu", n, n > 1 and "s" or "", n > 1 and "s" or "")
    or "Les quêtes de la zone sont déjà suivies")
  return n
end

local function pushControl()
  if not T.SetTabLines then return end
  if not (R:IsEnabled() and cfg("watchButton")) then pcall(T.SetTabLines, T, "quests", {}, "Region") return end
  pcall(T.SetTabLines, T, "quests", { { text = "Suivre ces quêtes dans le suivi du jeu", label = "Suivi du jeu",
    control = true, prio = 1, clickHint = "Clic : ajoute les quêtes de la zone au suivi d'objectifs du jeu",
    tooltip = { "Ajoute jusqu'à 5 quêtes de la zone actuelle au suivi d'objectifs du jeu.",
      "Rien n'est fait sans ton clic (hors combat). Questie / Shortest Path ne sont pas touchés." },
    onClick = function() R.WatchZone() end } }, "Region")
end
R.PushControl = pushControl

function R:OnEnable()
  pushControl()
  local function changed()
    R.Invalidate()
    if T.RequestRefresh then T:RequestRefresh() end
  end
  self:RegisterEvent("ZONE_CHANGED_NEW_AREA", changed)
  self:RegisterEvent("PLAYER_ENTERING_WORLD", changed)
  self:RegisterEvent("GROUP_ROSTER_UPDATE", changed)
end
function R:OnDisable()
  if T.SetTabLines then pcall(T.SetTabLines, T, "quests", {}, "Region") end
  if T.RequestRefresh then T:RequestRefresh() end
end
function R:OnOptionChanged()
  R.Invalidate()
  pushControl()
  if T.RequestRefresh then T:RequestRefresh() end
end
