-- TreeAI — module PvP (agent PVP) : aides JcJ, INFORMATIVES uniquement, et seulement si les aides PvP sont
-- activées (/tai pvp, ou Options > Aides PvP : TreeAI.settings.pvp, OFF par défaut). Onglet « JcJ » visible
-- seulement dans ce cas.
--  1 champs de bataille : minuteurs d'objectifs (bases / tours en cours de prise : POI de carte
--    C_AreaPoiInfo comme Capping, + messages CHAT_MSG_BG_SYSTEM_* en secours), réapparition du drapeau,
--    porteurs de drapeau (nom, vie % quand l'unité est visible, position), clic = macro « TAI Cible »
--    (/targetexact Nom) que TU appuies — jamais de ciblage automatique
--  2 score : prédiction (fin de partie, temps restant, score final adverse) et probabilité de victoire
--    depuis les widgets de score (C_UIWidgetManager) — taux mesurés, sinon table classique d'Arathi
--  3 équipe ennemie depuis le tableau des scores (classes, soigneurs estimés, meilleur tueur)
--  4 JcJ sauvage : ennemis vus (barres de nom / cible / survol) : classe, niveau, guilde, danger vs toi,
--    liste KOS manuelle (/tai kos), alerte « Danger : 2 ennemis niv 60 près de toi » dans l'encadré
--  5 honneur de la session : honneur/heure, victoires honorables, historique par champ de bataille →
--    meilleur BG en honneur/heure ; file d'attente : compte à rebours pour entrer
--  6 duel / cible ennemie : conseils par classe (ce qu'il faut surveiller, contres du chasseur), auras
--    défensives visibles sur la cible (durées affichées par le jeu)
-- Jamais : COMBAT_LOG_EVENT_UNFILTERED (interdit aux addons sur Forever), suivi des temps de recharge
-- ennemis, ciblage / action à la place du joueur. Valeurs « secrètes » en combat ignorées. Lua 5.1.
local T = _G.TreeAI
if not T or not T.RegisterModule then return end
local G = _G

local M = T:RegisterModule("PvP", {
  title = "JcJ",
  defaults = { enabled = true, bg = true, predict = true, enemies = true, world = true, dangerAlert = true,
    honor = true, duel = true, kos = {} },
  charDefaults = { history = {}, lifetime = { hk = 0, honor = 0, bgs = 0, wins = 0 } },
  options = {
    { key = "bg", type = "toggle", label = "Champs de bataille : minuteurs, drapeaux", section = "JcJ (si les aides PvP sont activées)" },
    { key = "predict", type = "toggle", label = "Prédiction du score et chance de victoire" },
    { key = "enemies", type = "toggle", label = "Équipe ennemie (tableau des scores)" },
    { key = "world", type = "toggle", label = "JcJ sauvage : ennemis proches, liste KOS" },
    { key = "dangerAlert", type = "toggle", label = "Alerte danger dans l'encadré" },
    { key = "honor", type = "toggle", label = "Honneur de la session, meilleur champ de bataille" },
    { key = "duel", type = "toggle", label = "Conseils contre la classe ciblée / en duel" },
  },
})
T.PvP = M

local TAB = "pvp"
local MACRO = "TAI Cible"

local function pass(ok, ...) if ok then return ... end return nil end
local function try(fn, ...) if type(fn) ~= "function" then return nil end return pass(pcall(fn, ...)) end
local function api(name) return rawget(G, name) end
local function secret(v) local f = api("issecretvalue") return type(f) == "function" and try(f, v) and true or false end
local function sv(v) if v == nil or secret(v) then return nil end return v end
local function call(name, ...) local f = api(name) if type(f) == "function" then return try(f, ...) end end
local function now() return try(api("GetTime")) or 0 end
local function opt(k) return M.db and M.db[k] ~= false end
local function inCombat() return try(api("InCombatLockdown")) and true or false end
local function clamp(v, a, b) if v < a then return a elseif v > b then return b end return v end
local function mmss(sec)
  sec = math.max(0, math.floor((tonumber(sec) or 0) + 0.5))
  return ("%d:%02d"):format(math.floor(sec / 60), sec % 60)
end
local function lower(s) return (tostring(s or "")):lower() end

local CLASS_FR = { WARRIOR = "Guerrier", PALADIN = "Paladin", HUNTER = "Chasseur", ROGUE = "Voleur",
  PRIEST = "Prêtre", SHAMAN = "Chaman", MAGE = "Mage", WARLOCK = "Démoniste", DRUID = "Druide" }
local HEALERS = { PRIEST = true, DRUID = true, SHAMAN = true, PALADIN = true }
local function className(tok)
  local loc = api("LOCALIZED_CLASS_NAMES_MALE")
  return (type(loc) == "table" and loc[tok]) or CLASS_FR[tok] or tostring(tok or "?")
end
local CLASS_BY_LOCAL = {}
for tok, fr in pairs(CLASS_FR) do CLASS_BY_LOCAL[lower(fr)] = tok; CLASS_BY_LOCAL[lower(tok)] = tok end
CLASS_BY_LOCAL["voleuse"], CLASS_BY_LOCAL["prêtresse"], CLASS_BY_LOCAL["chamane"] = "ROGUE", "PRIEST", "SHAMAN"
CLASS_BY_LOCAL["druidesse"], CLASS_BY_LOCAL["chasseresse"], CLASS_BY_LOCAL["guerrière"] = "DRUID", "HUNTER", "WARRIOR"
CLASS_BY_LOCAL["démoniste"], CLASS_BY_LOCAL["rogue"], CLASS_BY_LOCAL["priest"] = "WARLOCK", "ROGUE", "PRIEST"

function M.Active()
  return T.settings and T.settings.pvp and M:IsEnabled() and true or false
end

local function playerFaction() return sv(call("UnitFactionGroup", "player")) end
local function playerLevel() return sv(call("UnitLevel", "player")) or 1 end
local function playerClass() local _, c = call("UnitClass", "player") return sv(c) end
local function shortName(n)
  n = sv(n)
  if type(n) ~= "string" then return nil end
  return (n:match("^([^%-]+)")) or n
end

local function inBG()
  local inInst, kind = call("IsInInstance")
  kind = sv(kind)
  return (sv(inInst) and kind == "pvp") and true or false, kind
end

---------------------------------------------------------------------------------------------------
-- état
---------------------------------------------------------------------------------------------------
M.timers = {}      -- name -> { ends=, label=, faction= }
M.carriers = {}    -- flag faction ("Alliance"/"Horde") -> { name=, since=, hp= }
M.score = {}       -- "Alliance"/"Horde" -> { score=, max=, bases=, hist = { {t, score} } }
M.enemies = {}     -- name -> { class=, level=, guild=, hp=, seen=, src=, guid= }
M.team = nil       -- scoreboard summary
M.session = { start = nil, honor = 0, hk = 0, deaths = 0 }
M.bg = nil         -- current battleground { name=, start=, honor0= }

---------------------------------------------------------------------------------------------------
-- 1. messages système de champ de bataille (FR + EN)
---------------------------------------------------------------------------------------------------
local function flagOf(msg)
  local m = lower(msg)
  if m:find("alliance", 1, true) then return "Alliance" end
  if m:find("horde", 1, true) then return "Horde" end
end

--- Parse a CHAT_MSG_BG_SYSTEM_* text. Returns { kind=, name=, flag=, node= } or nil.
--- kinds: pick, drop, cap, ret (flags) ; assault, take, defend (nodes).
function M.ParseBGSystem(msg)
  if type(msg) ~= "string" or secret(msg) then return nil end
  local m = msg
  local name
  -- drapeaux
  name = m:match("ramassé par (.-) ?!") or m:match("[Pp]icked up by (.-) ?!")
  if name then return { kind = "pick", name = name, flag = flagOf(m:match("^(.-)ramass") or m:match("^(.-)[Pp]icked") or m) } end
  name = m:match("lâché par (.-) ?!") or m:match("[Dd]ropped by (.-) ?!")
  if name then return { kind = "drop", name = name, flag = flagOf(m:match("^(.-)lâch") or m:match("^(.-)[Dd]ropped") or m) } end
  name = m:match("^(.-) a pris le drapeau de (.-) ?!") or m:match("^(.-) captured the (.-) ?!")
  if name then
    local f = m:match("drapeau de (.-) ?!") or m:match("captured the (.-) ?!")
    return { kind = "cap", name = name, flag = flagOf(f) }
  end
  if m:find("renvoyé", 1, true) or m:find("returned", 1, true) then
    return { kind = "ret", name = m:match("par (.-) ?!") or m:match("by (.-) ?!"), flag = flagOf(m) }
  end
  -- bases / tours (Arathi, Alterac)
  local who, node = m:match("^(.-) a attaqué (.-) ?!")
  if not who then who, node = m:match("^(.-) has assaulted (.-) ?!") end
  if not who then who, node = m:match("^(.-) revendique (.-) ?!") end
  if not who then who, node = m:match("^(.-) claims (.-) ?!") end
  if who then return { kind = "assault", name = who, node = node } end
  local fac, n2 = m:match("^L[a']%s?(.-) a pris (.-) ?!")
  if not fac then fac, n2 = m:match("^The (%a+) has taken (.-) ?!") end
  if fac then return { kind = "take", flag = flagOf(fac), node = n2 } end
  who, node = m:match("^(.-) a défendu (.-) ?!")
  if not who then who, node = m:match("^(.-) has defended (.-) ?!") end
  if who then return { kind = "defend", name = who, node = node } end
  return nil
end

-- capture time by battleground map (Capping, classic values) ; default 60 s
local CAP_TIME = { ["bassin arathi"] = 60, ["arathi basin"] = 60, ["vallée d'alterac"] = 300, ["alterac valley"] = 300 }
local function capTime()
  local zone = lower(call("GetRealZoneText") or call("GetZoneText"))
  for k, v in pairs(CAP_TIME) do if zone:find(k, 1, true) then return v end end
  return 60
end

function M:StartTimer(name, sec, faction, label)
  if not name then return end
  self.timers[name] = { ends = now() + (tonumber(sec) or 60), faction = faction, label = label or name }
end

function M:OnBGMessage(msg)
  local e = M.ParseBGSystem(msg)
  if not e then return end
  if e.kind == "pick" and e.flag then
    self.carriers[e.flag] = { name = shortName(e.name) or e.name, since = now() }
  elseif (e.kind == "drop" or e.kind == "ret") and e.flag then
    self.carriers[e.flag] = nil
  elseif e.kind == "cap" then
    self.carriers = {}   -- a capture needs your own flag home: nobody carries one any more
    self:StartTimer("flagrespawn", 12, nil, "Réapparition des drapeaux")
  elseif e.kind == "assault" and e.node and not self._poiDriven then
    self:StartTimer(e.node, capTime(), nil, e.node)
  elseif (e.kind == "take" or e.kind == "defend") and e.node and not self._poiDriven then
    self.timers[e.node] = nil
  end
  self:Refresh()
end

---------------------------------------------------------------------------------------------------
-- 1b. POI de carte : icônes « contesté » (textureIndex) comme Capping
---------------------------------------------------------------------------------------------------
local CONTESTED = { [3] = "Alliance", [13] = "Horde", [8] = "Alliance", [11] = "Horde", [17] = "Alliance",
  [19] = "Horde", [22] = "Alliance", [24] = "Horde", [27] = "Alliance", [29] = "Horde", [32] = "Alliance",
  [34] = "Horde", [37] = "Alliance", [39] = "Horde" }
M.CONTESTED = CONTESTED
local poiCache = {}

function M:UpdatePOI()
  local poi, map = api("C_AreaPoiInfo"), api("C_Map")
  if type(poi) ~= "table" or type(map) ~= "table" then return end
  local mapID = try(map.GetBestMapForUnit, "player")
  if not mapID then return end
  local ids = try(poi.GetAreaPOIForMap, mapID)
  if type(ids) ~= "table" then return end
  for _, id in ipairs(ids) do
    local info = try(poi.GetAreaPOIInfo, mapID, id)
    local name, icon = info and sv(info.name), info and sv(info.textureIndex)
    if name and icon and poiCache[name] ~= icon then
      local first = poiCache[name] == nil
      poiCache[name] = icon
      self._poiDriven = true
      if CONTESTED[icon] then
        -- a POI already contested when we arrive: unknown start, assume a fresh one (Capping does the same)
        local t = sv(info.secondsLeft) or (try(poi.GetAreaPOISecondsLeft, id))
        self:StartTimer(name, sv(t) or capTime(), CONTESTED[icon], name)
      elseif not first or self.timers[name] then
        self.timers[name] = nil
      end
    end
  end
  self:Refresh()
end

---------------------------------------------------------------------------------------------------
-- 2. score : widgets (texte « Bases : 3  Ressources : 1200/2000 », « 2/3 ») et prédiction
---------------------------------------------------------------------------------------------------
-- classic Arathi rates (Capping): points / second by number of bases held
local AB_RATE = { [0] = 0, 0.8333, 1.1111, 1.6667, 3.3333, 30 }
M.AB_RATE = AB_RATE
-- known widget IDs (Capping, classic clients): Arathi 1893 Alliance / 1894 Horde
local WIDGET_SIDE = { [1893] = "Alliance", [1894] = "Horde" }

--- "Bases : 3  Ressources : 1200/2000" -> bases, score, max ; "2/3" -> nil, 2, 3.
function M.ScoreFromText(text)
  if type(text) ~= "string" or secret(text) then return nil end
  local t = text:gsub("|c%x%x%x%x%x%x%x%x", ""):gsub("|r", "")
  local b, s, mx = t:match("(%d)[^%d/]+(%d+)%s*/%s*(%d+)")
  if b then return tonumber(b), tonumber(s), tonumber(mx) end
  s, mx = t:match("(%d+)%s*/%s*(%d+)")
  if s then return nil, tonumber(s), tonumber(mx) end
  return nil
end

function M:SetScore(side, bases, score, max)
  if side ~= "Alliance" and side ~= "Horde" or not score then return end
  local s = self.score[side] or { hist = {} }
  local t = now()
  if s.score ~= score then
    s.hist[#s.hist + 1] = { t, score }
    while #s.hist > 12 do table.remove(s.hist, 1) end
  end
  s.score, s.max, s.bases, s.at = score, max or s.max, bases, t
  self.score[side] = s
end

--- points / second for a side: Arathi table when bases are known, else the measured slope.
function M.Rate(s)
  if not s then return 0 end
  if s.bases and (s.max or 0) >= 1000 then return AB_RATE[s.bases] or 0 end
  local h = s.hist or {}
  if #h >= 2 then
    local a, b = h[math.max(1, #h - 5)], h[#h]
    local dt = b[1] - a[1]
    if dt > 0 and b[2] >= a[2] then return (b[2] - a[2]) / dt end
  end
  return 0
end

local function logistic(x) return 1 / (1 + math.exp(-x)) end

--- Prediction from both sides. Returns { winner=, eta=, final=, pWin= (for `mine`), kind= } or nil.
function M.Predict(a, h, mine)
  if not (a and h and a.score and h.score) then return nil end
  local max = a.max or h.max or 0
  if max <= 0 then return nil end
  if max <= 5 then   -- capture-the-flag (Goulet des Chanteguerres : 3 captures)
    local diff = (mine == "Horde") and (h.score - a.score) or (a.score - h.score)
    local p = logistic(diff * 1.2 + (mine and M._flagEdge and M._flagEdge(mine) or 0))
    local lead = (a.score > h.score) and "Alliance" or (h.score > a.score) and "Horde" or nil
    return { kind = "flags", winner = lead, pWin = p }
  end
  local ra, rh = M.Rate(a), M.Rate(h)
  local ta = ra > 0 and (max - a.score) / ra or math.huge
  local th = rh > 0 and (max - h.score) / rh or math.huge
  local res = { kind = "res", ra = ra, rh = rh }
  if ta == math.huge and th == math.huge then
    res.pWin = logistic(((mine == "Horde") and (h.score - a.score) or (a.score - h.score)) / 150)
    return res
  end
  if ta <= th then
    res.winner, res.eta = "Alliance", ta
    res.final = math.min(max - 10, math.floor((h.score + rh * ta) / 10 + 0.5) * 10)
  else
    res.winner, res.eta = "Horde", th
    res.final = math.min(max - 10, math.floor((a.score + ra * th) / 10 + 0.5) * 10)
  end
  local mineT = (mine == "Horde") and th or ta
  local theirT = (mine == "Horde") and ta or th
  local margin = (theirT == math.huge and 600) or (mineT == math.huge and -600) or (theirT - mineT)
  res.pWin = logistic(clamp(margin, -600, 600) / 90)
  return res
end

M._flagEdge = function(mine)
  local enemyFlag = (mine == "Horde") and "Alliance" or "Horde"
  local e = 0
  if M.carriers[enemyFlag] then e = e + 0.4 end   -- we carry their flag
  if M.carriers[mine] then e = e - 0.4 end        -- they carry ours
  return e
end

function M:UpdateWidgets(widgetID)
  local W = api("C_UIWidgetManager")
  if type(W) ~= "table" or type(W.GetIconAndTextWidgetVisualizationInfo) ~= "function" then return end
  local function read(id)
    local info = try(W.GetIconAndTextWidgetVisualizationInfo, id)
    return info and sv(info.text)
  end
  local changed = false
  -- known Arathi widgets
  for id, side in pairs(WIDGET_SIDE) do
    if widgetID == nil or WIDGET_SIDE[widgetID] then
      local b, s, mx = M.ScoreFromText(read(id))
      if s then self:SetScore(side, b, s, mx); changed = true end
    end
  end
  -- generic: the top-centre widget set, first two score widgets = Alliance then Horde
  if not changed and type(W.GetTopCenterWidgetSetID) == "function" and type(W.GetAllWidgetsBySetID) == "function" then
    local set = try(W.GetTopCenterWidgetSetID)
    local list = set and try(W.GetAllWidgetsBySetID, set)
    if type(list) == "table" then
      local found = {}
      for _, w in ipairs(list) do
        local id = type(w) == "table" and sv(w.widgetID)
        local b, s, mx = M.ScoreFromText(id and read(id))
        if s and mx then found[#found + 1] = { b, s, mx } end
        if #found >= 2 then break end
      end
      if #found == 2 then
        self:SetScore("Alliance", found[1][1], found[1][2], found[1][3])
        self:SetScore("Horde", found[2][1], found[2][2], found[2][3])
      end
    end
  end
  self:Refresh()
end

---------------------------------------------------------------------------------------------------
-- 1c. porteurs de drapeau : vie % (unité visible), position (carte)
---------------------------------------------------------------------------------------------------
local function unitsToScan()
  local list = { "target", "focus", "mouseover" }
  for i = 1, 40 do list[#list + 1] = "nameplate" .. i end
  local n = sv(call("GetNumGroupMembers")) or 0
  for i = 1, math.min(n, 40) do list[#list + 1] = "raid" .. i end
  return list
end

local function hpOf(unit)
  local hp, mx = sv(call("UnitHealth", unit)), sv(call("UnitHealthMax", unit))
  if hp and mx and mx > 0 then return math.floor(hp / mx * 100 + 0.5) end
end

function M:ScanCarriers()
  if not next(self.carriers) then return end
  local want = {}
  for flag, c in pairs(self.carriers) do want[lower(c.name)] = c; c.hpFresh = false end
  for _, u in ipairs(unitsToScan()) do
    if sv(call("UnitExists", u)) then
      local n = shortName(call("UnitName", u))
      local c = n and want[lower(n)]
      if c and not c.hpFresh then
        c.hp, c.hpAt, c.hpFresh = hpOf(u), now(), true
        local _, cls = call("UnitClass", u)
        c.class = sv(cls) or c.class
      end
    end
  end
end

--- zone text for a position on the Warsong Gulch map (Alliance base north, Horde base south).
function M.FlagZone(y, faction)
  y = tonumber(y)
  if not y then return nil end
  if y < 0.3 then return "base Alliance" elseif y > 0.7 then return "base Horde" end
  if y < 0.45 then return "milieu, côté Alliance" elseif y > 0.55 then return "milieu, côté Horde" end
  return "milieu"
end

local function flagPosition(i)
  local P = api("C_PvP")
  local map = api("C_Map")
  local mapID = type(map) == "table" and try(map.GetBestMapForUnit, "player")
  if type(P) == "table" and type(P.GetBattlefieldFlagPosition) == "function" and mapID then
    local pos = try(P.GetBattlefieldFlagPosition, i, mapID)
    if type(pos) == "table" then
      local x, y = pos.x, pos.y
      if type(pos.GetXY) == "function" then x, y = try(pos.GetXY, pos) end
      return sv(x), sv(y)
    end
  end
  local x, y = call("GetBattlefieldFlagPosition", i)
  return sv(x), sv(y)
end

function M:CarrierWhere(flag)
  -- flag index: 1 = Alliance flag, 2 = Horde flag (classic clients)
  local i = (flag == "Alliance") and 1 or 2
  local x, y = flagPosition(i)
  if x and y and (x > 0 or y > 0) then return M.FlagZone(y), x, y end
end

---------------------------------------------------------------------------------------------------
-- 3. équipe ennemie (tableau des scores)
---------------------------------------------------------------------------------------------------
local function scoreRow(i)
  local P = api("C_PvP")
  if type(P) == "table" and type(P.GetScoreInfo) == "function" then
    local s = try(P.GetScoreInfo, i)
    if type(s) == "table" then
      return { name = sv(s.name), faction = sv(s.faction), class = sv(s.classToken), hk = sv(s.honorableKills),
        kb = sv(s.killingBlows), deaths = sv(s.deaths), dmg = sv(s.damageDone), heal = sv(s.healingDone),
        race = sv(s.raceName) }
    end
  end
  local name, kb, hk, deaths, honor, faction, rank, race, cls, tok, dmg, heal = call("GetBattlefieldScore", i)
  if not sv(name) then return nil end
  return { name = sv(name), kb = sv(kb), hk = sv(hk), deaths = sv(deaths), faction = sv(faction),
    class = sv(tok) or CLASS_BY_LOCAL[lower(sv(cls))], race = sv(race), dmg = sv(dmg), heal = sv(heal),
    honor = sv(honor) }
end

function M:ReadScoreboard()
  local n = sv(call("GetNumBattlefieldScores")) or 0
  if n <= 0 then return nil end
  local myFac = playerFaction()
  local myIdx = (myFac == "Horde") and 0 or 1   -- scoreboard faction: 0 = Horde, 1 = Alliance
  local team = { count = 0, classes = {}, healers = 0, top = nil, allies = 0, healerNames = {} }
  for i = 1, math.min(n, 80) do
    local r = scoreRow(i)
    if r and r.name then
      if r.faction ~= nil and r.faction ~= myIdx then
        team.count = team.count + 1
        if r.class then team.classes[r.class] = (team.classes[r.class] or 0) + 1 end
        local heal, dmg = tonumber(r.heal) or 0, tonumber(r.dmg) or 0
        if r.class and HEALERS[r.class] and heal > 0 and heal > dmg * 1.2 then
          team.healers = team.healers + 1
          team.healerNames[#team.healerNames + 1] = shortName(r.name)
        end
        if (r.hk or 0) > ((team.top and team.top.hk) or -1) then team.top = { name = shortName(r.name), hk = r.hk or 0, class = r.class } end
        local e = self.enemies[shortName(r.name)]
        if e and not e.class then e.class = r.class end
      else
        team.allies = team.allies + 1
      end
    end
  end
  self.team = team
  return team
end

function M.TeamText(team)
  if not team or team.count == 0 then return nil end
  local parts = {}
  local order = {}
  for cls, n in pairs(team.classes) do order[#order + 1] = { cls, n } end
  table.sort(order, function(a, b) if a[2] ~= b[2] then return a[2] > b[2] end return a[1] < b[1] end)
  for _, p in ipairs(order) do parts[#parts + 1] = p[2] .. " " .. className(p[1]) end
  return ("Ennemis : %d — %s"):format(team.count, table.concat(parts, ", "))
end

---------------------------------------------------------------------------------------------------
-- 4. JcJ sauvage : ennemis vus, danger, KOS
---------------------------------------------------------------------------------------------------
-- danger of an enemy class for YOUR class (+ = harder), roughly level-60 classic matchups
local MATCHUP = {
  HUNTER = { ROGUE = 2, WARRIOR = 1, MAGE = 1, WARLOCK = 1, SHAMAN = 1, HUNTER = 0, PALADIN = 0, PRIEST = 0, DRUID = 0 },
  WARRIOR = { MAGE = 2, HUNTER = 2, WARLOCK = 1, PRIEST = 1, DRUID = 1, PALADIN = 1, ROGUE = 0, SHAMAN = 0, WARRIOR = 0 },
  MAGE = { ROGUE = 1, WARLOCK = 1, PRIEST = 1, HUNTER = 1, WARRIOR = 0, MAGE = 0 },
  ROGUE = { WARRIOR = 1, DRUID = 1, PALADIN = 1, ROGUE = 0, MAGE = 0, PRIEST = 0, WARLOCK = 0 },
  PRIEST = { WARLOCK = 1, ROGUE = 1, PRIEST = 0, WARRIOR = 0 },
  WARLOCK = { PRIEST = 1, ROGUE = 1, WARLOCK = 0, WARRIOR = -1 },
  DRUID = { HUNTER = 1, MAGE = 1, WARLOCK = 1, DRUID = 0 },
  SHAMAN = { MAGE = 1, PRIEST = 1, WARLOCK = 1, SHAMAN = 0 },
  PALADIN = { MAGE = 1, PRIEST = 1, WARLOCK = 1, PALADIN = 0 },
}
M.MATCHUP = MATCHUP

--- Danger 0..10 of an enemy for the player. lvl -1 / nil skull = 10.
function M.Danger(e, myLevel, myClass, kos)
  if not e then return 0 end
  myLevel = myLevel or 1
  local lvl = tonumber(e.level)
  if not lvl or lvl <= 0 then return 10 end
  local d = 5 + (lvl - myLevel)
  local mu = MATCHUP[myClass or ""]
  d = d + ((mu and e.class and mu[e.class]) or 0)
  if e.hp and e.hp < 30 then d = d - 2 elseif e.hp and e.hp < 60 then d = d - 1 end
  if kos then d = d + 2 end
  return clamp(math.floor(d + 0.5), 0, 10)
end

function M.DangerLabel(d)
  if d >= 9 then return "extrême" elseif d >= 7 then return "élevé" elseif d >= 4 then return "moyen" end
  return "faible"
end

local function isEnemyPlayer(unit)
  if not sv(call("UnitExists", unit)) then return false end
  if not sv(call("UnitIsPlayer", unit)) then return false end
  return sv(call("UnitCanAttack", "player", unit)) and true or false
end

function M:SeeUnit(unit, src)
  if not (opt("world") or opt("enemies")) then return end
  if not isEnemyPlayer(unit) then return end
  local name = shortName(call("UnitName", unit))
  if not name then return end
  local e = self.enemies[name] or { first = now() }
  local _, cls = call("UnitClass", unit)
  e.class = sv(cls) or e.class
  local lvl = sv(call("UnitLevel", unit))
  if lvl then e.level = lvl end
  local guild = sv(call("GetGuildInfo", unit))
  if guild then e.guild = guild end
  e.hp = hpOf(unit) or e.hp
  e.guid = sv(call("UnitGUID", unit)) or e.guid
  e.seen, e.src, e.name = now(), src or unit, name
  self.enemies[name] = e
  -- a carrier seen on a nameplate: refresh its health
  for _, c in pairs(self.carriers) do
    if lower(c.name) == lower(name) then c.hp, c.hpAt, c.class = e.hp, now(), e.class end
  end
  self:CheckDanger()
  self:Refresh()
end

local NEAR = 25   -- seconds an enemy stays « près de toi » after last sight
function M:Nearby(window)
  window = window or NEAR
  local t, list = now(), {}
  for name, e in pairs(self.enemies) do
    if t - (e.seen or 0) <= window then list[#list + 1] = e
    elseif t - (e.seen or 0) > 600 then self.enemies[name] = nil end
  end
  local lvl, cls, kos = playerLevel(), playerClass(), (M.db and M.db.kos) or {}
  for _, e in ipairs(list) do e.danger = M.Danger(e, lvl, cls, kos[lower(e.name)] ~= nil) end
  table.sort(list, function(a, b)
    if a.danger ~= b.danger then return a.danger > b.danger end
    return (a.seen or 0) > (b.seen or 0)
  end)
  return list
end

function M.DangerText(list, myLevel)
  if not list or #list == 0 then return nil, 0 end
  local strong, maxLvl, skull, kos = 0, 0, false, nil
  local kosList = (M.db and M.db.kos) or {}
  for _, e in ipairs(list) do
    local l = tonumber(e.level) or -1
    if l <= 0 then skull = true end
    if l <= 0 or l >= (myLevel or 1) then strong = strong + 1 end
    if l > maxLvl then maxLvl = l end
    if kosList[lower(e.name)] then kos = e.name end
  end
  if kos then return ("Danger : %s (KOS) près de toi !"):format(kos), 3 end
  if skull then return ("Danger : %d ennemi%s dont un niv ?? près de toi"):format(#list, #list > 1 and "s" or ""), 3 end
  if strong >= 2 then return ("Danger : %d ennemis niv %d près de toi"):format(strong, maxLvl), 3 end
  if #list >= 1 and (list[1].danger or 0) >= 4 then
    local e = list[1]
    return ("Ennemi proche : %s niv %s"):format(className(e.class), tostring(e.level or "?")), (e.danger or 0) >= 7 and 3 or 2
  end
  return nil, 0
end

function M:CheckDanger()
  if not (M.Active() and opt("world") and opt("dangerAlert")) then return end
  if inBG() then self:Clear("danger") return end   -- in battlegrounds every nameplate is an enemy: no spam
  local text, prio = M.DangerText(self:Nearby(), playerLevel())
  -- PVPMARK : « ennemi proche » appartient aux repères des barres de nom quand ils tournent (seule la KOS reste ici)
  local PMk = T.PvPMark
  if text and PMk and PMk.Active and PMk:Active() and not text:find("KOS", 1, true) then text = nil end
  if text then
    self:Suggest("danger", text, prio, { pvp = true, tab = TAB, ttl = NEAR, cooldown = 45 })
  else
    self:Clear("danger")
  end
end

function M:AddKOS(name, note)
  name = shortName(name)
  if not name or name == "" then
    if isEnemyPlayer("target") then name = shortName(call("UnitName", "target")) end
  end
  if not name or name == "" then T:Print("KOS : cible un joueur ennemi ou tape /tai kos Nom") return false end
  self.db.kos = self.db.kos or {}
  local k = lower(name)
  if self.db.kos[k] then
    self.db.kos[k] = nil
    T:Print("KOS : " .. name .. " retiré de la liste")
  else
    local e = self.enemies[name] or {}
    self.db.kos[k] = { name = name, class = e.class, guild = e.guild, note = note ~= "" and note or nil, added = try(api("time")) }
    T:Print("KOS : " .. name .. " ajouté (alerte quand il est près de toi)")
  end
  self:Refresh()
  return true
end

---------------------------------------------------------------------------------------------------
-- clic = macro « TAI Cible » (/targetexact Nom) : TU appuies dessus, hors combat seulement
---------------------------------------------------------------------------------------------------
function M.TargetMacroText(name) return "/targetexact " .. tostring(name) end

function M:PrepareTarget(name)
  if not name then return false end
  local body = M.TargetMacroText(name)
  if inCombat() or not (T.InUserAction and T:InUserAction()) then
    T:Print("Pour cibler " .. name .. " : " .. body .. " (macro modifiable hors combat seulement)")
    return false
  end
  -- (FIDELITY) GetMacroIndexByName n'existe pas sur Forever : l'index du module Macros (sinon : une macro de plus à chaque fois)
  local Mc = T.modules and T.modules.Macros
  local idx = (Mc and Mc.MacroIndex and try(Mc.MacroIndex, MACRO)) or try(api("GetMacroIndexByName"), MACRO) or 0
  local ok
  if idx and idx > 0 then
    ok = try(api("EditMacro"), idx, MACRO, nil, body) ~= nil
  else
    ok = try(api("CreateMacro"), MACRO, "INV_MISC_QUESTIONMARK", body, true) ~= nil
  end
  T:Print(ok and ("Macro « " .. MACRO .. " » prête : " .. body .. " — appuie dessus pour cibler")
    or ("Pour cibler " .. name .. " : " .. body))
  return ok
end

---------------------------------------------------------------------------------------------------
-- 5. honneur / session / historique / file d'attente
---------------------------------------------------------------------------------------------------
--- "(Points d'honneur estimés : 87)" / "(Estimated Honor Points: 87)" / "... 12 points d'honneur" -> 87
function M.ParseHonor(msg)
  if type(msg) ~= "string" or secret(msg) then return nil end
  local n = msg:match("[Hh]onor[^%d]*(%d+)") or msg:match("honneur[^%d]*(%d+)") or msg:match("(%d+)%s+points? d'honneur")
    or msg:match("(%d+)%s+[Hh]onor")
  return tonumber(n)
end

function M:OnHonor(msg)
  local n = M.ParseHonor(msg)
  if not n then return end
  local s = self.session
  s.start = s.start or now()
  s.honor = s.honor + n
  if msg:find("meurt", 1, true) or msg:find("dies", 1, true) or msg:find("honorable", 1, true) then s.hk = s.hk + 1 end
  if self.char and self.char.lifetime then self.char.lifetime.honor = (self.char.lifetime.honor or 0) + n end
  self:Refresh()
end

function M:SessionStats()
  local s = self.session
  local hk, honor = call("GetPVPSessionStats")
  hk, honor = sv(hk), sv(honor)
  local h = math.max(s.honor or 0, tonumber(honor) or 0)
  local k = math.max(s.hk or 0, tonumber(hk) or 0)
  local start = s.start
  local per = 0
  if start and h > 0 then per = h / math.max(now() - start, 600) * 3600 end   -- 10 min floor: no silly peaks
  return { honor = h, hk = k, perHour = math.floor(per + 0.5), deaths = s.deaths or 0 }
end

function M:BGStart(name)
  if self.bg then return end
  self.bg = { name = name or "?", start = now(), honor0 = self:SessionStats().honor }
  self.session.start = self.session.start or now()
  self.carriers, self.timers, self.score, self.team, poiCache, self._poiDriven = {}, {}, {}, nil, {}, nil
end

function M:BGEnd(won)
  local bg = self.bg
  if not bg then return end
  self.bg = nil
  local dur = now() - bg.start
  if dur < 60 then return end
  local honor = self:SessionStats().honor - (bg.honor0 or 0)
  local h = self.char and self.char.history
  if type(h) == "table" then
    h[#h + 1] = { bg = bg.name, dur = math.floor(dur), honor = honor, won = won, at = try(api("time")) }
    while #h > 50 do table.remove(h, 1) end
  end
  local lt = self.char and self.char.lifetime
  if lt then lt.bgs = (lt.bgs or 0) + 1; if won then lt.wins = (lt.wins or 0) + 1 end end
end

--- best battleground by honor / hour from the history (>= 2 games, else best single game)
function M.BestBG(history)
  local agg = {}
  for _, g in ipairs(history or {}) do
    if g.bg and (g.dur or 0) > 0 then
      local a = agg[g.bg] or { bg = g.bg, honor = 0, dur = 0, n = 0, wins = 0 }
      a.honor, a.dur, a.n = a.honor + (g.honor or 0), a.dur + g.dur, a.n + 1
      if g.won then a.wins = a.wins + 1 end
      agg[g.bg] = a
    end
  end
  local list = {}
  for _, a in pairs(agg) do a.perHour = a.honor / a.dur * 3600; list[#list + 1] = a end
  table.sort(list, function(x, y)
    if (x.n >= 2) ~= (y.n >= 2) then return x.n >= 2 end
    if x.perHour ~= y.perHour then return x.perHour > y.perHour end
    return x.bg < y.bg
  end)
  return list[1], list
end

function M:UpdateQueue()
  local maxQ = sv(call("GetMaxBattlefieldID")) or 3
  local lines = {}
  local inside, current = false, nil
  for i = 1, math.min(maxQ, 5) do
    local status, mapName = call("GetBattlefieldStatus", i)
    status, mapName = sv(status), sv(mapName)
    if status == "confirm" then
      local left = sv(call("GetBattlefieldPortExpiration", i))
      left = left and (left > 1000 and left / 1000 or left)
      lines[#lines + 1] = { text = ("Entrée prête : %s — %s pour accepter"):format(mapName or "?", left and mmss(left) or "?"), prio = 3 }
    elseif status == "queued" then
      local waited = sv(call("GetBattlefieldTimeWaited", i))
      local est = sv(call("GetBattlefieldEstimatedWaitTime", i))
      lines[#lines + 1] = { text = ("File : %s — attendu %s%s"):format(mapName or "?", mmss((waited or 0) / 1000),
        (est and est > 0) and (", estimé " .. mmss(est / 1000)) or ""), prio = 1 }
    elseif status == "active" then
      inside, current = true, mapName
    end
  end
  self.queueLines = lines
  local isIn = inBG() or inside
  if isIn and not self.bg and not self._ended then
    self:BGStart(current or sv(call("GetRealZoneText")) or sv(call("GetZoneText")))
  end
  local winner = isIn and sv(call("GetBattlefieldWinner"))
  if self.bg and winner ~= nil and winner ~= false then
    local fac = playerFaction()
    self:BGEnd((winner == 0 and fac == "Horde") or (winner == 1 and fac == "Alliance"))
    self._ended = true   -- scoreboard stays ~2 min: no new game until we leave
  elseif self.bg and not isIn then
    self:BGEnd(nil)
  end
  if not isIn then self._ended = nil end
  self:Refresh()
end

---------------------------------------------------------------------------------------------------
-- 6. conseils par classe ennemie (cible / duel) + auras défensives visibles
---------------------------------------------------------------------------------------------------
M.CLASS_TIPS = {
  WARRIOR = { "Garde tes distances : Charge / Interception, puis Brise-genou (ralentit).",
    "Représailles (15 s) : ne le frappe pas en mêlée ; Mur protecteur réduit tes dégâts 10 s." },
  ROGUE = { "Furtif : il ouvre par Coup bas / Assommer ; Fusée éclairante si tu le sais près.",
    "Évasion = esquive tout en mêlée : recule, les sorts et le pet passent." },
  MAGE = { "Nova de givre puis Transfert : reste mobile, coupe ses sorts (Contresort = tes stuns/silences).",
    "Bloc de glace : immunité 10 s, attends (soins, bandage)." },
  PRIEST = { "Peur psychique (rayon 8 m) : garde de la distance.", "Coupe ses soins ; Brûlure de mana vide ta mana." },
  WARLOCK = { "Peur / Hurlement de terreur, DoTs : tue vite ou fuis hors de portée.",
    "Pierre de sort / Dissiper : son familier (succube = Séduction) compte aussi." },
  PALADIN = { "Bouclier divin (12 s) : ne gaspille rien, il soigne dessous.", "Marteau de la justice = étourdissement 6 s." },
  HUNTER = { "Zone morte 5–8 m : collé à lui il ne tire plus.", "Piège givrant, Dissuasion (pare 10 s) : contourne." },
  DRUID = { "Change de forme pour se libérer des ralentissements.", "Racines sarmenteuses : brise-les ; coupe ses soins." },
  SHAMAN = { "Horion de terre coupe tes sorts ; Totem de lien terrestre ralentit.",
    "Totem de séisme : annule peurs et charmes, brise-le d'abord." },
}
-- the player's own hunter counters (FR)
M.HUNTER_COUNTERS = {
  WARRIOR = "Chasseur : Coupure d'ailes puis distance, Trait de choc, Piège givrant ; pas de Morsure de vipère (rage).",
  ROGUE = "Chasseur : Fusée éclairante sur sa zone, Piège givrant à tes pieds, Aspect du singe en mêlée.",
  MAGE = "Chasseur : Morsure de vipère (mana), Flèche de dispersion si talent, Tir des arcanes après sa Nova.",
  PRIEST = "Chasseur : Morsure de vipère, tir sur la Peur après Berserk ; pet sur lui pour couper ses soins.",
  WARLOCK = "Chasseur : Morsure de vipère, pet sur le démoniste (pas le familier), Tir des arcanes sur la Pierre.",
  PALADIN = "Chasseur : Morsure de vipère, garde-le ralenti (Trait de choc, Piège givrant), attends la fin du Bouclier.",
  HUNTER = "Chasseur : colle-le dans sa zone morte, ou pet + Morsure de vipère ; Piège givrant au contact.",
  DRUID = "Chasseur : Morsure de vipère, Fusée éclairante contre le félin furtif, Trait de choc après ses changements de forme.",
  SHAMAN = "Chasseur : distance > 20 m (Horion), Morsure de vipère, tue ses totems avec le pet.",
}
-- defensive auras worth a callout when visible on the enemy target (spell IDs, classic ranks + names)
local DEF_AURAS = {
  [642] = "Bouclier divin", [1020] = "Bouclier divin", [498] = "Protection divine", [5573] = "Protection divine",
  [1022] = "Bénédiction de protection", [5599] = "Bénédiction de protection", [10278] = "Bénédiction de protection",
  [11958] = "Bloc de glace", [5277] = "Évasion", [26669] = "Évasion", [871] = "Mur protecteur",
  [19263] = "Dissuasion", [18499] = "Rage berserker", [1044] = "Bénédiction de liberté", [6346] = "Gardien de peur",
  [20230] = "Représailles", [1719] = "Témérité", [12042] = "Pouvoir des arcanes", [12043] = "Présence spirituelle",
  [10060] = "Infusion de puissance", [29166] = "Innervation", [22812] = "Peau d'écorce",
}
M.DEF_AURAS = DEF_AURAS

function M.TargetAuras(unit)
  local out = {}
  local CU = api("C_UnitAuras")
  for i = 1, 40 do
    local name, exp, id
    if type(CU) == "table" and type(CU.GetAuraDataByIndex) == "function" then
      local a = try(CU.GetAuraDataByIndex, unit, i, "HELPFUL")
      if not a then break end
      name, exp, id = sv(a.name), sv(a.expirationTime), sv(a.spellId)
    else
      local n, _, _, _, _, e, _, _, _, sid = call("UnitBuff", unit, i)
      if not n then break end
      name, exp, id = sv(n), sv(e), sv(sid)
    end
    local label = id and DEF_AURAS[id]
    if label then
      local left = (exp and exp > 0) and (exp - now()) or nil
      out[#out + 1] = { label = label, left = left }
    end
  end
  return out
end

function M:TargetInfo()
  if not (M.Active() and opt("duel")) then return nil end
  local unit = "target"
  local name = shortName(call("UnitName", unit))
  if not isEnemyPlayer(unit) and not (name and self.duelWith and lower(name) == lower(self.duelWith)) then return nil end
  local _, cls = call("UnitClass", unit)
  cls = sv(cls)
  if not cls then return nil end
  local lvl = sv(call("UnitLevel", unit))
  local lines = {}
  local e = { level = lvl, class = cls, hp = hpOf(unit) }
  local kos = M.db and M.db.kos and M.db.kos[lower(name or "")]
  local d = M.Danger(e, playerLevel(), playerClass(), kos ~= nil)
  lines[#lines + 1] = { text = ("Cible : %s %s niv %s — danger %s (%d/10)%s"):format(className(cls), name or "?",
    (lvl and lvl > 0) and tostring(lvl) or "??", M.DangerLabel(d), d, kos and " — KOS" or ""), prio = d >= 7 and 3 or 2 }
  for _, a in ipairs(M.TargetAuras(unit)) do
    lines[#lines + 1] = { text = ("  %s actif%s"):format(a.label, a.left and (" — " .. math.ceil(a.left) .. " s") or ""), prio = 3 }
  end
  for _, tip in ipairs(M.CLASS_TIPS[cls] or {}) do lines[#lines + 1] = { text = "  " .. tip, prio = 1 } end
  if playerClass() == "HUNTER" and M.HUNTER_COUNTERS[cls] then
    lines[#lines + 1] = { text = "  " .. M.HUNTER_COUNTERS[cls], prio = 2 }
  end
  return lines
end

---------------------------------------------------------------------------------------------------
-- onglet « JcJ »
---------------------------------------------------------------------------------------------------
function M:SyncTab()
  local on = M.Active()
  if on and not (T.tabs and T.tabs[TAB]) then
    if T.RegisterTab then T:RegisterTab(TAB, "JcJ", 6) end
  elseif not on and T.tabs and T.tabs[TAB] then
    if T.SetTabLines then try(T.SetTabLines, T, TAB, {}, "PvP") end
    T.tabs[TAB] = nil
    self:Clear("danger")
    if T.RequestRefresh then T:RequestRefresh() end
  end
  return on
end

function M:BuildLines()
  local L = {}
  local function add(text, prio, extra)
    local l = { text = text, prio = prio or 1 }
    if extra then for k, v in pairs(extra) do l[k] = v end end
    L[#L + 1] = l
  end
  local myFac = playerFaction()
  for _, q in ipairs(self.queueLines or {}) do add(q.text, q.prio, { pin = q.prio >= 3 }) end
  local isBG = inBG()
  if isBG and opt("bg") then
    -- carriers
    for _, flag in ipairs({ "Alliance", "Horde" }) do
      local c = self.carriers[flag]
      if c then
        local carrierFac = (flag == "Alliance") and "Horde" or "Alliance"
        local enemy = myFac and carrierFac ~= myFac
        local where = self:CarrierWhere(flag)
        local hp = (c.hp and c.hpAt and now() - c.hpAt < 15) and (" — " .. c.hp .. " %") or ""
        local txt = ("%s %s a le drapeau %s%s%s"):format(enemy and "|cffff5555Ennemi|r" or "|cff55ff55Allié|r",
          c.name, flag == "Alliance" and "de l'Alliance" or "de la Horde", hp, where and (" (" .. where .. ")") or "")
        local name = c.name
        add(txt, enemy and 3 or 2, { pin = enemy, tooltip = "Clic : macro « TAI Cible » = " .. M.TargetMacroText(name),
          onClick = function() M:PrepareTarget(name) end })
      end
    end
    -- objective timers
    local t, list = now(), {}
    for name, tm in pairs(self.timers) do
      local left = tm.ends - t
      if left <= 0 then self.timers[name] = nil else list[#list + 1] = { name = name, left = left, tm = tm } end
    end
    table.sort(list, function(a, b) return a.left < b.left end)
    for _, x in ipairs(list) do
      local who = x.tm.faction and (x.tm.faction == myFac and " (nous)" or " (eux)") or ""
      add(("%s : %s%s"):format(x.tm.label, mmss(x.left), who), x.left <= 15 and 3 or 2)
    end
  end
  if isBG and opt("predict") then
    local p = M.Predict(self.score.Alliance, self.score.Horde, myFac)
    if p then
      local pct = p.pWin and math.floor(p.pWin * 100 + 0.5)
      if p.kind == "res" and p.winner then
        local us = myFac and p.winner == myFac
        add(("Prévision : %s gagne dans %s (%s %d)%s"):format(us and "on" or p.winner, mmss(p.eta),
          us and "eux" or "nous", p.final or 0, pct and (" — victoire " .. pct .. " %") or ""), us and 1 or 2)
      elseif pct then
        add(("Chance de victoire : %d %%"):format(pct), pct < 35 and 2 or 1)
      end
    end
  end
  if isBG and opt("enemies") then
    local txt = M.TeamText(self.team)
    if txt then
      add(txt, 1)
      local tm = self.team
      if tm.healers > 0 then
        add(("Soigneurs ennemis (estimés) : %d — %s"):format(tm.healers, table.concat(tm.healerNames, ", ")), 2)
      end
      if tm.top then add(("Meilleur tueur ennemi : %s (%d victoires)"):format(tm.top.name, tm.top.hk), 1) end
    end
  end
  -- target / duel
  for _, l in ipairs(self:TargetInfo() or {}) do add(l.text, l.prio) end
  -- world pvp
  if not isBG and opt("world") then
    local near = self:Nearby()
    local kos = (M.db and M.db.kos) or {}
    for i = 1, math.min(#near, 6) do
      local e = near[i]
      local name = e.name
      add(("%s%s %s niv %s%s — danger %s"):format(kos[lower(name)] and "|cffff0000KOS|r " or "", className(e.class), name,
        (tonumber(e.level) or 0) > 0 and tostring(e.level) or "??", e.guild and (" <" .. e.guild .. ">") or "",
        M.DangerLabel(e.danger or 0)), (e.danger or 0) >= 7 and 3 or 2,
        { tooltip = "Clic : macro « TAI Cible » ; /tai kos " .. name .. " pour la liste KOS",
          onClick = function() M:PrepareTarget(name) end })
    end
  end
  -- honor
  if opt("honor") then
    local s = self:SessionStats()
    if s.honor > 0 or s.hk > 0 then
      add(("Session : %d honneur (%d/h), %d victoires honorables"):format(s.honor, s.perHour, s.hk), 1)
    end
    local best = M.BestBG(self.char and self.char.history)
    if best and not isBG then
      add(("Meilleur BG pour l'honneur : %s (%d/h sur %d partie%s, %d gagnée%s)"):format(best.bg,
        math.floor(best.perHour + 0.5), best.n, best.n > 1 and "s" or "", best.wins, best.wins > 1 and "s" or ""), 1)
    end
  end
  if #L == 0 then add("JcJ : rien à signaler (cible un joueur ennemi, ou entre dans un champ de bataille)", 1) end
  return L
end

function M:Refresh(force)
  if not self:SyncTab() then return end
  local t = now()
  if not force and self._lastRefresh and t - self._lastRefresh < 0.5 then
    if not self._pending then
      self._pending = true
      T:After(0.6, function() M._pending = false; M:Refresh(true) end)
    end
    return
  end
  self._lastRefresh = t
  self.lines = self:BuildLines()
  if T.SetTabLines then try(T.SetTabLines, T, TAB, self.lines, "PvP") end
end

---------------------------------------------------------------------------------------------------
-- événements
---------------------------------------------------------------------------------------------------
local function guard(fn)
  return function(self, ...) if M.Active() then return fn(self, ...) end end
end

function M:OnEnable()
  self:RegisterEvent("CHAT_MSG_BG_SYSTEM_ALLIANCE", guard(function(s, _, msg) s:OnBGMessage(msg) end))
  self:RegisterEvent("CHAT_MSG_BG_SYSTEM_HORDE", guard(function(s, _, msg) s:OnBGMessage(msg) end))
  self:RegisterEvent("CHAT_MSG_BG_SYSTEM_NEUTRAL", guard(function(s, _, msg) s:OnBGMessage(msg) end))
  self:RegisterEvent("AREA_POIS_UPDATED", guard(function(s) if inBG() then s:UpdatePOI() end end))
  self:RegisterEvent("UPDATE_UI_WIDGET", guard(function(s, _, info)
    if inBG() then s:UpdateWidgets(type(info) == "table" and sv(info.widgetID) or nil) end
  end))
  self:RegisterEvent("UPDATE_BATTLEFIELD_SCORE", guard(function(s) s:ReadScoreboard(); s:Refresh() end))
  self:RegisterEvent("UPDATE_BATTLEFIELD_STATUS", guard(function(s) s:UpdateQueue() end))
  self:RegisterEvent("PLAYER_ENTERING_WORLD", guard(function(s) s:UpdateQueue() end))
  self:RegisterEvent("ZONE_CHANGED_NEW_AREA", guard(function(s) s:UpdateQueue() end))
  self:RegisterEvent("NAME_PLATE_UNIT_ADDED", guard(function(s, _, unit) s:SeeUnit(unit, "barre de nom") end))
  self:RegisterEvent("UPDATE_MOUSEOVER_UNIT", guard(function(s) s:SeeUnit("mouseover", "survol") end))
  self:RegisterEvent("PLAYER_TARGET_CHANGED", guard(function(s) s:SeeUnit("target", "cible"); s:Refresh() end))
  self:RegisterEvent("UNIT_HEALTH", guard(function(s, _, unit)
    if unit == "target" and isEnemyPlayer("target") then s:SeeUnit("target", "cible") end
  end))
  self:RegisterEvent("UNIT_AURA", guard(function(s, _, unit) if unit == "target" then s:Refresh() end end))
  self:RegisterEvent("CHAT_MSG_COMBAT_HONOR_GAIN", guard(function(s, _, msg) s:OnHonor(msg) end))
  self:RegisterEvent("PLAYER_PVP_KILLS_CHANGED", guard(function(s) s:Refresh() end))
  self:RegisterEvent("PLAYER_DEAD", guard(function(s) if inBG() then s.session.deaths = (s.session.deaths or 0) + 1 end end))
  self:RegisterEvent("DUEL_REQUESTED", guard(function(s, _, who) s.duelWith = shortName(who); s:Refresh() end))
  self:RegisterEvent("DUEL_FINISHED", function(s) s.duelWith = nil end)
  self:SyncTab()
end

function M:OnDisable()
  self:UnregisterAllEvents()
  if T.tabs and T.tabs[TAB] then
    if T.SetTabLines then try(T.SetTabLines, T, TAB, {}, "PvP") end
    T.tabs[TAB] = nil
  end
end

local lastScoreReq = 0
function M:AutoRefresh()
  if not self:SyncTab() then return end
  if inBG() then
    if opt("bg") then self:ScanCarriers() end
    if opt("enemies") and now() - lastScoreReq >= 15 then
      lastScoreReq = now()
      call("RequestBattlefieldScoreData")   -- standard scoreboard refresh (the score frame does the same)
    end
  end
  self:CheckDanger()
  self:Refresh()
end

T:RegisterSlash("kos", function(rest)
  if not M.Active() then T:Print("Aides PvP désactivées : /tai pvp pour les activer") return end
  rest = tostring(rest or "")
  if rest == "liste" or rest == "list" then
    local n = 0
    for _, e in pairs(M.db.kos or {}) do
      n = n + 1
      T:Print(("KOS : %s %s%s"):format(e.name, e.class and className(e.class) or "", e.note and (" — " .. e.note) or ""))
    end
    if n == 0 then T:Print("KOS : liste vide (/tai kos Nom, ou cible un joueur ennemi puis /tai kos)") end
    return
  end
  local name, note = rest:match("^(%S*)%s*(.-)$")
  M:AddKOS(name ~= "" and name or nil, note)
end, "kos [Nom|liste] : liste KOS JcJ (ajoute / retire, alerte quand il est près)")

T:RegisterSlash("jcj", function()
  if not M.Active() then T:Print("Aides PvP désactivées : /tai pvp pour les activer") return end
  local s = M:SessionStats()
  T:Print(("JcJ : %d honneur cette session (%d/h), %d victoires honorables, %d morts en BG"):format(s.honor, s.perHour, s.hk, s.deaths))
  local _, list = M.BestBG(M.char and M.char.history)
  for i = 1, math.min(#(list or {}), 4) do
    local a = list[i]
    T:Print(("  %s : %d honneur/h, %d partie(s), %d gagnée(s)"):format(a.bg, math.floor(a.perHour + 0.5), a.n, a.wins))
  end
  if T.SelectTab and T.tabs and T.tabs[TAB] then T:SelectTab(TAB) end
end, "jcj : honneur de la session, meilleur champ de bataille")
