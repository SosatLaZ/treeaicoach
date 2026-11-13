-- TreeAI — « Suivi auto par zone » (agent ZONEFIX, 4.0.1) : extension du module Trajet (ZonePlan : même module, mêmes
-- réglages, même page d'options « Trajet »).
--  * Le suivi d'objectifs du jeu (et les suivis qui le recopient : Questie, Suivi Forever) montre les quêtes du
--    trajet, dans son ordre (les plus proches / rentables d'abord, l'histoire en cours ensemble), au plus 6 (3 à 8).
--    Avant : il gardait toutes les quêtes prises, de toutes les zones (« des quêtes suivies pas du tout de la
--    région »).
--  * Ce que TU suis ou retires à la main (journal, suivi, carte) n'est jamais repris : un changement du suivi qui
--    n'est ni de TreeAI ni du jeu lui-même (le jeu suit tout seul une quête prise ou qui avance) est un choix du
--    joueur, gardé tant que la quête est dans le journal.
--  * Hors combat seulement (en combat : attend la fin), un passage toutes les 2 s au plus ; jamais la sélection du
--    journal, rien d'autre que C_QuestLog.AddQuestWatch / RemoveQuestWatch (AddQuestWatch / RemoveQuestWatch par index
--    sur les clients classiques). Option coupée : plus rien n'est touché (rien n'est « restauré » de force).
-- Lua 5.1, aucune globale.
local tonumber = function(v) return _G.tonumber(v) end   -- (4.0.8) tonumber(f()) : un 2e retour de f n'est jamais une base
local T = _G.TreeAI
local Z = T and T.ZonePlan
if not (Z and Z.RegisterEvent) then return end

local fmt, min = string.format, math.min
local function pass(ok, ...) if ok then return ... end local sw = _G.TreeAI and _G.TreeAI.Swallow if sw then sw((...), "ZoneWatch") end return nil end
local function try(fn, ...) if type(fn) ~= "function" then return nil end return pass(pcall(fn, ...)) end
local function G(name) return rawget(_G, name) end
local function C(ns, fn) local t = G(ns) return type(t) == "table" and t[fn] or nil end
local function ns(v) local f = G("issecretvalue") if f and f(v) then return nil end return v end
local function now() return tonumber(try(G("GetTime"))) or 0 end
local function inCombat() return T.InCombat and T:InCombat() or false end
-- (5.1 ROUTE6, audit B11) le suivi du jeu appartient au joueur : TreeAI n'y ajoute ni n'y retire RIEN par défaut. Le suivi
-- auto par zone est une option à cocher soi-même (Trajet → « Suivi du jeu », ou la pastille) — coupée d'office, et
-- remise à « non » une fois chez tout le monde (elle était cochée sans avoir été demandée).
local function on() return Z:IsEnabled() and Z.db and Z.db.autoWatch == true end

Z.WATCH_GAP = 2                 -- s entre deux passages
Z.AUTO_WINDOW = 1.5             -- s : un changement du suivi aussi près d'une prise / d'un objectif = le jeu lui-même

-- ------------------------------------------------------------------ API du suivi (client moderne / classique)
local function legacyIndex(q)
  local f = G("GetQuestLogIndexByID")
  local i = f and tonumber((try(f, q)))
  if i and i > 0 then return i end
  for k = 1, min(80, tonumber((try(G("GetNumQuestLogEntries")))) or 0) do
    local _, _, _, header, _, _, _, id = try(G("GetQuestLogTitle"), k)
    if not header and tonumber(id) == q then return k end
  end
  return nil
end
--- → { watched(q), add(q), remove(q) } ou nil (pas de suivi d'objectifs sur ce client)
function Z.WatchAPI()
  local add, rem, typ = C("C_QuestLog", "AddQuestWatch"), C("C_QuestLog", "RemoveQuestWatch"), C("C_QuestLog", "GetQuestWatchType")
  if add and rem and typ then
    return { watched = function(q) return ns(try(typ, q)) ~= nil end,
      add = function(q) return try(add, q) end, remove = function(q) return try(rem, q) end }
  end
  local gAdd, gRem, gIs = G("AddQuestWatch"), G("RemoveQuestWatch"), G("IsQuestWatched")
  if gAdd and gRem and gIs then
    return { watched = function(q) local i = legacyIndex(q) return i and try(gIs, i) and true or false end,
      add = function(q) local i = legacyIndex(q) return i and try(gAdd, i) end,
      remove = function(q) local i = legacyIndex(q) return i and try(gRem, i) end }
  end
  return nil
end

-- ------------------------------------------------------------------ qui a changé le suivi ?
local pending, autoAt = {}, {}  -- [q] = { added, t } changements pas encore jugés ; [q] = dernier geste du jeu
Z._watchPending, Z._watchAuto = pending, autoAt
local function onListChanged(q, added)
  q = tonumber(q)
  if not q or Z._watchBusy then return end                 -- le nôtre
  pending[q] = { added = added and true or false, t = now() }
end
-- un changement qui n'est ni de TreeAI ni du jeu (quête prise / objectif qui avance / quête partie, à 1,5 s près) =
-- un geste du joueur, gardé tant que la quête est dans le journal
local function classify(log)
  local ch = Z.char
  if not ch then return end
  local tnow = now()
  for q, p in pairs(pending) do
    if tnow - p.t >= 0.4 then
      pending[q] = nil
      local a = autoAt[q]
      local auto = a and math.abs(a - p.t) <= Z.AUTO_WINDOW
      if not auto and log[q] then ch.watchManual[q] = p.added end
    end
  end
end

-- ------------------------------------------------------------------ ce qu'on veut voir dans le suivi
--- les quêtes du journal que le trajet fait, dans son ordre ; celles du thème de l'arrêt en cours d'abord
function Z:WatchWanted(log)
  local P = T.Planner
  local steps, cur = P and P.steps, P and P.cursor
  if not (steps and cur and P.route) then return nil end
  local R = T.Route
  if R and R.char and R.char.paused then return nil end
  local maxN = math.max(3, math.min(8, tonumber(self.db and self.db.watchMax) or 6))
  local order, seen = {}, {}
  for i = cur, #steps do
    local s = steps[i]
    local q = tonumber(s.q)
    -- (JOURNAL) jamais une quête d'un donjon déjà fait (le trajet ne la garde pas non plus ; une prête à rendre, si)
    if q and not seen[q] and not s._done and not s._other and log[q] and not (log[q].failed == 1 or log[q].failed == true)
        and not (log[q].done ~= 1 and Z.DungeonDone and Z.DungeonDone(q)) then
      seen[q] = true
      order[#order + 1] = q
    end
  end
  local th = self.themes or {}
  local first = order[1] and th[order[1]] and th[order[1]].key
  local out = {}
  if first then for _, q in ipairs(order) do if th[q] and th[q].key == first then out[#out + 1] = q end end end
  for _, q in ipairs(order) do
    if not (first and th[q] and th[q].key == first) then out[#out + 1] = q end
  end
  while #out > maxN do out[#out] = nil end
  return out
end

-- ------------------------------------------------------------------ un passage
--- → nombre de changements faits (nil = rien à faire maintenant)
function Z:WatchPass()
  self._watchTimer = nil
  if not on() then return nil end
  if inCombat() then self._watchQueued = true return nil end
  local tnow = now()
  local wait = (self._watchAt or -100) + Z.WATCH_GAP - tnow
  if wait > 0 then self:WatchSoon(wait) return nil end
  self._watchQueued = nil
  local api = Z.WatchAPI()
  local Xm = T.Export
  local log = (Xm and Xm.LogSnapshot and Xm.LogSnapshot()) or {}
  classify(log)
  local ch = self.char
  if not (api and ch) then return nil end
  for q in pairs(ch.watchManual) do if not log[q] then ch.watchManual[q] = nil end end
  for q in pairs(ch.watchOurs) do if not log[q] then ch.watchOurs[q] = nil end end
  local want = self:WatchWanted(log)
  if not want then return nil end
  self._watchAt = tnow
  local wantSet = {}
  for _, q in ipairs(want) do wantSet[q] = true end
  local n = 0
  self._watchBusy = true
  local ok, err = pcall(function()
    -- d'abord retirer (place libre dans le suivi), puis poser dans l'ordre du trajet
    for q in pairs(log) do
      local m = ch.watchManual[q]
      if m ~= true and not wantSet[q] and api.watched(q) then
        if T.ExpectQuestWatch then T:ExpectQuestWatch(q, false) end   -- (V5H) un seul réveil pour toute la passe
        api.remove(q); ch.watchOurs[q] = nil; n = n + 1
      end
    end
    for _, q in ipairs(want) do
      if ch.watchManual[q] ~= false and not api.watched(q) then
        if T.ExpectQuestWatch then T:ExpectQuestWatch(q, true) end
        api.add(q)
        if api.watched(q) then ch.watchOurs[q] = true; n = n + 1 end
      elseif ch.watchManual[q] == nil and api.watched(q) then ch.watchOurs[q] = true end
    end
  end)
  self._watchBusy = false
  if not ok and T.Debug then T:Debug("ZoneWatch: " .. tostring(err)) end
  self.watchLast = { at = tnow, n = n, want = want }
  return n
end
function Z:WatchSoon(delay)
  if not on() then return end
  if self._watchTimer then return end
  self._watchTimer = true
  T:After(math.max(0.2, delay or 0.5), function() Z._watchTimer = nil; Z:WatchPass() end)
end
--- texte de l'option (Hub, page Trajet)
function Z:AutoWatchOn() return on() and true or false end
function Z:SetAutoWatch(v)
  if not self.db then return end
  self.db.autoWatch = v and true or false
  self:WatchHub()
  if v then self._watchAt = nil; self:WatchSoon(0.2) end
end

-- ------------------------------------------------------------------ pastille (Hub) : une bascule
local hubEntry = { group = "reglages", id = "suiviauto", icon = "Interface\\Icons\\INV_Misc_Map_01",
  tip = "Clic : le suivi du jeu montre (ou non) les quêtes de ta zone, dans l'ordre du trajet ; tes choix à la main restent",
  fn = function() Z:SetAutoWatch(not Z:AutoWatchOn()) end }
function Z:WatchHub()
  hubEntry.title = fmt("Suivi auto par zone : %s", (self.db and self.db.autoWatch == true) and "oui" or "non")
  T.HUB_EXTRA = type(T.HUB_EXTRA) == "table" and T.HUB_EXTRA or {}
  for _, e in ipairs(T.HUB_EXTRA) do if e == hubEntry then return end end
  T.HUB_EXTRA[#T.HUB_EXTRA + 1] = hubEntry
end
Z.HubWatchEntry = hubEntry

-- ------------------------------------------------------------------ évènements (avec le module Trajet)
-- le jeu lui-même vient de toucher à cette quête (prise, objectif qui avance, rendue, abandonnée) ; un changement
-- du suivi à ce moment-là est le sien (suivi automatique du jeu), pas un choix du joueur
function Z.WatchGameDid(q)
  q = tonumber(ns(q))
  if q then autoAt[q] = now() end
  Z:WatchSoon(0.5)
end
function Z:WatchEnable()
  self:WatchHub()
  local function soon() Z:WatchSoon(0.5) end
  -- QUEST_ACCEPTED : (questID) sur Forever, (index du journal, questID) sur les clients classiques → le dernier nombre
  self:RegisterEvent("QUEST_WATCH_LIST_CHANGED", function(_, _, q, added) onListChanged(ns(q), ns(added)) end)
  self:RegisterEvent("QUEST_ACCEPTED", function(_, _, a, b) Z.WatchGameDid(tonumber(ns(b)) or ns(a)) end)
  self:RegisterEvent("QUEST_WATCH_UPDATE", function(_, _, q) Z.WatchGameDid(q) end)
  self:RegisterEvent("QUEST_REMOVED", function(_, _, q) Z.WatchGameDid(q) end)
  self:RegisterEvent("PLAYER_REGEN_ENABLED", function() if Z._watchQueued then soon() end end)
  self:On("PLAN_BUILT", soon)
  T:After(3, soon)                                          -- connexion : une fois le trajet prêt
end
local baseEnable, baseOption = Z.OnEnable, Z.OnOptionChanged
function Z:OnEnable()
  if baseEnable then baseEnable(self) end
  self:WatchEnable()
end
function Z:OnOptionChanged(key, value)
  if key == "autoWatch" or key == "watchMax" then
    self:WatchHub()
    if self:AutoWatchOn() then self._watchAt = nil; self:WatchSoon(0.2) end
    return
  end
  if baseOption then return baseOption(self, key, value) end
end
