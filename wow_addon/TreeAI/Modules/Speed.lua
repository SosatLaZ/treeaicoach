-- TreeAI — module Speed (agent SPEED) : techniques de vitesse en direct, calculées (jamais un guide copié).
--  * Mort : course au corps OU gardien des esprits, selon les distances réelles (fantôme → corps → étape
--    suivante contre fantôme → étape suivante) et le mal de résurrection (0 avant niv 10, 1 min par niveau
--    de 10 à 19, 10 min ensuite ; il ne gêne que si l'étape suivante demande de se battre).
--  * Pierre de foyer : « maintenant » quand marcher jusqu'à l'étape du trajet coûte ≥ 2 min de plus que
--    foyer (10 s) + marche depuis l'auberge. Position de l'auberge mémorisée (HEARTHSTONE_BOUND, ou à
--    l'arrivée d'une pierre de foyer) et passée à l'export pour le planificateur de l'appli.
--  * Les astuces calculées par l'appli (tue en chemin, point de vol en passant, grind de niveau, foyer)
--    arrivent dans le plan (champ `tip`) et sont affichées par Planner sous l'étape courante.
-- Une ligne en français simple par technique, seulement quand elle sert. Lua 5.1, aucune globale.
local tonumber = function(v) return _G.tonumber(v) end   -- (4.0.8) tonumber(f()) : un 2e retour de f n'est jamais une base
local function _ns(v) local f = rawget(_G, "issecretvalue") if f and f(v) then return nil end return v end   -- valeurs secrètes (combat) → nil
local T = _G.TreeAI
if not (T and T.RegisterModule) then return end

local S = T:RegisterModule("Speed", {
  title = "Vitesse",
  defaults = { enabled = true, death = true, hearth = true },
  charDefaults = { bind = nil },
  options = {
    { key = "death", type = "toggle", label = "Mort : dire s'il vaut mieux le gardien des esprits ou le corps" },
    { key = "hearth", type = "toggle", label = "Dire quand la pierre de foyer fait gagner du temps" },
  },
})
T.Speed = S

local floor, max = math.floor, math.max
local fmt = string.format
local function pass(ok, ...) if ok then return ... end return nil end
local function try(fn, ...) if type(fn) ~= "function" then return nil end return pass(pcall(fn, ...)) end
local function G(name) return rawget(_G, name) end
local function C(ns, fn) local t = G(ns) return type(t) == "table" and t[fn] or nil end
local function now() return try(G("GetTime")) or 0 end
local function cfg(k) return not (S.db and S.db[k] == false) end

-- ===================================================================== moteur (pur, testé)
local E = {}
S.Engine = E
E.HEARTH_CAST = 10
E.DEATH_MIN_GAIN = 60       -- s : en dessous, on ne dit rien (la course au corps reste le réflexe)
E.HEARTH_MIN_GAIN = 120     -- s
E.HEARTH_MIN_DIST = 300     -- yd

--- Durée du mal de résurrection (s) au gardien des esprits.
function E.Sickness(level)
  level = tonumber(level) or 1
  if level < 10 then return 0 end
  if level < 20 then return (level - 9) * 60 end
  return 600
end

function E.Saved(sec)
  sec = tonumber(sec) or 0
  if sec >= 90 then return fmt("%d min", floor(sec / 60 + 0.5)) end
  return fmt("%d s", floor(sec + 0.5))
end

--- Choix à la mort (fantôme au cimetière). Distances en yd, vitesse en yd/s.
-- @return "spirit"|"corpse", secondes gagnées par rapport à l'autre choix, durée du mal (s)
function E.DeathChoice(level, dMeCorpse, dCorpseNext, dMeNext, speed, fightNext)
  speed = (tonumber(speed) or 7) > 0 and speed or 7
  local corpse = (dMeCorpse + dCorpseNext) / speed
  local walk = dMeNext / speed
  local sick = E.Sickness(level)
  local spirit = walk + (fightNext and max(0, sick - walk) or 0)
  if spirit < corpse then return "spirit", corpse - spirit, sick end
  return "corpse", spirit - corpse, sick
end

--- Secondes gagnées en utilisant la pierre de foyer pour aller à l'étape (négatif = marcher est mieux).
function E.HearthGain(dMeStep, dBindStep, speed)
  speed = (tonumber(speed) or 7) > 0 and speed or 7
  return dMeStep / speed - (E.HEARTH_CAST + dBindStep / speed)
end

function E.DeathText(gain, sick)
  local parts = {}
  if sick > 0 then parts[#parts + 1] = fmt("mal de résurrection %d min", floor(sick / 60 + 0.5)) end
  parts[#parts + 1] = "−25 % durabilité"
  return fmt("Gardien des esprits : %s gagnées (%s)", E.Saved(gain), table.concat(parts, ", "))
end
function E.HearthText(gain) return fmt("Pierre de foyer maintenant : %s gagnées", E.Saved(gain)) end

-- course au corps (agent DATA) : « Cadavre : 230 m au NE (≈ 40 s) »
local DIRS = { "N", "NO", "O", "SO", "S", "SE", "E", "NE" }          -- cap 0 = nord, sens anti-horaire
local ART = { N = "au", NO = "au", O = "à l'", SO = "au", S = "au", SE = "au", E = "à l'", NE = "au" }
function E.Cardinal(bearing)
  if not bearing then return nil end
  return DIRS[floor(((bearing % (2 * math.pi)) + math.pi / 8) / (math.pi / 4)) % 8 + 1]
end
function E.Meters(yd)
  local m = floor(yd * 0.9144 / 10 + 0.5) * 10
  if m >= 1000 then return (fmt("%.1f km", m / 1000):gsub("%.", ",")) end
  return m .. " m"
end
function E.Eta(sec)
  sec = max(0, tonumber(sec) or 0)
  if sec < 60 then return fmt("%d s", max(5, floor(sec / 5 + 0.5) * 5)) end
  return fmt("%d min", floor(sec / 60 + 0.5))
end
function E.CorpseText(yd, bearing, speed)
  speed = (tonumber(speed) or 7) > 0 and speed or 7
  local d = E.Cardinal(bearing)
  local where = d and (" " .. ART[d] .. (ART[d] == "à l'" and "" or " ") .. d) or ""
  if yd < 15 then return "Cadavre : ici, relève-toi" end
  return fmt("Cadavre : %s%s (≈ %s)", E.Meters(yd), where, E.Eta(yd / speed))
end
E.RES_RANGE = 38       -- yd : on peut se relever jusqu'à ~39 yd du corps
E.SAFE_RANGE = 25      -- yd : ennemi plus près que ça du corps = relève-toi à l'écart

-- ===================================================================== jeu
local function Geo() return T.Geo end
local function playerPos() local g = Geo() if g and g.Player then return g.Player() end end
local function dist(m1, x1, y1, m2, x2, y2) local g = Geo() return g and g.Dist and g.Dist(m1, x1, y1, m2, x2, y2) end
local function runSpeed()
  local _, run = try(G("GetUnitSpeed"), "player")
  run = _ns(run)
  run = tonumber(run)
  return (run and run > 0) and run or 7
end
local function nextStep()
  local P = T.Planner
  if not (P and P.GetActiveSteps) then return nil end
  local list = try(P.GetActiveSteps, P, 1)
  local s = type(list) == "table" and list[1] or nil
  if s and s.map and s.x and s.y then return s end
  return nil
end
-- (QA-QUEST F25) le mal de résurrection (10 min) gêne si l'un des 3 prochains arrêts demande de se battre (pas
-- seulement le suivant : à 28, l'arrêt après une remise est un combat dans les 10 min)
function S.FightSoon(step)
  if step and (step.k == "obj" or step.k == "grind") then return true end
  local P = T.Planner
  local list = P and P.GetActiveSteps and try(P.GetActiveSteps, P, 3)
  for _, s in ipairs(type(list) == "table" and list or {}) do
    if s.k == "obj" or s.k == "grind" then return true end
  end
  return false
end
local function bindName() return try(G("GetBindLocation")) end

--- Position de l'auberge liée {map,x,y,name} (nil si inconnue ou si la pierre a été liée ailleurs depuis).
function S:Bind()
  local b = self.char and self.char.bind
  if type(b) ~= "table" or not b.map then return nil end
  local n = bindName()
  if n and b.name and n ~= b.name then return nil end
  return b
end
function S:RecordBind()
  local map, x, y = playerPos()
  if not (map and x and y and self.char) then return end
  self.char.bind = { map = map, x = x, y = y, name = bindName() }
end

-- (QA-QUEST F8) Forever n'a que C_Item.GetItemCount (le global GetItemCount n'existe plus : « pierre prête »
-- était toujours faux en jeu)
local function hearthCount()
  local n = try(C("C_Item", "GetItemCount"), 6948)
  if n == nil then n = try(G("GetItemCount"), 6948) end
  return tonumber(n) or 0
end
local function hearthReady()
  if hearthCount() < 1 then return false end
  local s, d = try(C("C_Container", "GetItemCooldown"), 6948)
  if not s then s, d = try(G("GetItemCooldown"), 6948) end
  s, d = tonumber(s), tonumber(d)
  if not s or s == 0 then return true end
  return (s + (d or 0) - now()) <= 0
end
S.HearthReady = hearthReady

-- ===================================================================== course au corps (agent DATA)
-- Fantôme : UNE ligne en tête de « Maintenant » (« Cadavre : 230 m au NE (≈ 40 s) »), la boussole vise le corps
-- (contrat unique Compass:SetTarget, source "corpse" : le corps passe avant l'itinéraire), le choix gardien des
-- esprits / corps (moteur ci-dessus) dans la même ligne, et l'endroit sûr pour se relever (barres de nom ennemies
-- vues près du corps). Tout s'efface au retour à la vie (PLAYER_ALIVE vivant / PLAYER_UNGHOST).
local function xyOf(v)
  if type(v) ~= "table" then return nil end
  if type(v.GetXY) == "function" then return try(v.GetXY, v) end
  return v.x, v.y
end
--- position du corps en % (map, x, y) : C_DeathInfo sur la carte courante, puis sur celle de la mort ; sinon
--- l'endroit noté à PLAYER_DEAD.
function S:CorpsePos(map)
  local f = C("C_DeathInfo", "GetCorpseMapPosition")
  local d = self.dead
  for _, m in ipairs({ map, d and d.map }) do
    if f and m then
      local cx, cy = xyOf(try(f, m))
      if tonumber(cx) and tonumber(cy) and (cx > 0 or cy > 0) then return m, cx * 100, cy * 100 end
    end
  end
  if d and d.map then return d.map, d.x, d.y end
  return nil
end
local function bearing(m1, x1, y1, m2, x2, y2)
  local g = Geo()
  if g and g.World then
    local a1, b1, c1 = g.World(m1, x1, y1)
    local a2, b2, c2 = g.World(m2, x2, y2)
    if a1 and a2 and c1 == c2 then return math.atan2(b2 - b1, a2 - a1) end
  end
  if m1 ~= m2 then return nil end
  return math.atan2(-(x2 - x1) * 3, -(y2 - y1) * 2)   -- zone ~3000 × 2000 yd : direction juste
end
-- ennemis vivants visibles à ≤ ~25 yd (barres de nom ; API.Distance : UnitPosition ou portée d'interaction)
function S:HostilesNear(range)
  local NP, API = G("C_NamePlate"), T.API
  local n, read = 0, 0
  if not (NP and NP.GetNamePlates and API and API.Distance) then return nil end
  for _, p in ipairs(try(NP.GetNamePlates) or {}) do
    local u = type(p) == "table" and (p.namePlateUnitToken or (type(p.UnitFrame) == "table" and p.UnitFrame.unit))
    if type(u) == "string" and try(G("UnitCanAttack"), "player", u) and not try(G("UnitIsDead"), u)
      and not try(G("UnitIsPlayer"), u) then
      local d = API.Distance(u)
      if not d then return nil end   -- (audit) distance non lue (PNJ : pas de position en vrai) → on ne dit rien
      read = read + 1
      if d <= (range or E.SAFE_RANGE) + 3 then n = n + 1 end
    end
  end
  return read > 0 and n or nil   -- aucune distance vraiment lue : ni « coin sûr » ni « ennemis tout près »
end
function S:OnDead()
  local map, x, y = playerPos()
  self.dead = map and { map = map, x = x, y = y, t = now(), foes = self:HostilesNear() } or nil
end
local function compass() local Cm = T.Compass return type(Cm) == "table" and Cm or nil end
function S:AimCorpse(map, x, y)
  local Cm = compass()
  if not (Cm and type(Cm.SetTarget) == "function") then return end
  local a = self.aim
  if a and a.map == map and math.abs(a.x - x) < 0.05 and math.abs(a.y - y) < 0.05 then return end
  local cur = Cm.char and Cm.char.custom
  if not a then   -- point perso du joueur gardé, rendu au retour à la vie
    self.prevCustom = (type(cur) == "table" and cur.source ~= "corpse") and cur or false
  end
  local ok = try(Cm.SetTarget, Cm, { mapID = map, x = x / 100, y = y / 100, label = "Ton cadavre", source = "corpse" })
  if ok then self.aim = { map = map, x = x, y = y } end
end
function S:ReleaseCorpse()
  self:Clear("corpse")
  self:Clear("death")
  self.dead = nil
  local Cm = compass()
  if self.aim and Cm and Cm.char then
    local cur = Cm.char.custom
    if type(cur) == "table" and cur.source == "corpse" then
      if self.prevCustom then
        Cm.char.custom = self.prevCustom
        if type(Cm.Rebuild) == "function" then try(Cm.Rebuild) end
        if type(Cm.Refresh) == "function" then try(Cm.Refresh) end
      elseif type(Cm.ClearWaypoint) == "function" then try(Cm.ClearWaypoint) end
    end
    if type(Cm.Clear) == "function" then try(Cm.Clear, Cm, "target") end
  end
  self.aim, self.prevCustom = nil, nil
end

function S:CheckDeath(st, step, map, x, y)
  if not st.ghost then
    if self.aim or self.dead and not st.dead then self:ReleaseCorpse() end
    self:Clear("corpse") self:Clear("death")
    return
  end
  local cm, cx, cy = self:CorpsePos(map)
  local dMC = cm and dist(map, x, y, cm, cx, cy)
  if not dMC then self:Clear("corpse") self:Clear("death") return end
  self:AimCorpse(cm, cx, cy)
  local speed = runSpeed()
  local text = E.CorpseText(dMC, bearing(map, x, y, cm, cx, cy), speed)
  local tips = { "Clic : la boussole vise ton corps." }
  -- gardien des esprits ou course au corps (selon l'étape suivante du trajet)
  local advice
  if cfg("death") and step then
    local dCN = dist(cm, cx, cy, step.map, step.x, step.y)
    local dMN = dist(map, x, y, step.map, step.x, step.y)
    if dCN and dMN then
      local lvl = try(G("UnitLevel"), "player") or 1
      local choice, gain, sick = E.DeathChoice(lvl, dMC, dCN, dMN, speed, S.FightSoon(step))
      if choice == "spirit" and gain >= E.DEATH_MIN_GAIN then
        advice = E.DeathText(gain, sick)
        tips[#tips + 1] = advice .. " — fantôme → étape suivante contre fantôme → corps → étape suivante, mal de résurrection compris."
      elseif sick > 0 then
        tips[#tips + 1] = fmt("Cours au corps : le gardien des esprits coûte %d min de mal de résurrection.", floor(sick / 60 + 0.5))
      end
    end
  end
  -- endroit sûr : ennemis vus près du corps (barres de nom quand tu y es, sinon au moment de la mort)
  if dMC <= E.RES_RANGE + 10 then
    local foes = self:HostilesNear()
    if foes and foes > 0 then
      text = text .. fmt(" · %d ennemi%s tout près : relève-toi à l'écart", foes, foes > 1 and "s" or "")
    elseif foes == 0 and dMC >= 15 then
      text = text .. " · coin sûr : relève-toi"
    end
  elseif self.dead and (self.dead.foes or 0) > 0 then
    tips[#tips + 1] = fmt("%d ennemi%s près du corps à ta mort : relève-toi en bordure (jusqu'à ~35 m du corps).",
      self.dead.foes, self.dead.foes > 1 and "s" or "")
  end
  if advice then text = text .. " — mieux : gardien des esprits" end
  local aim = self.aim
  -- (QA-QUEST F12) mort = obligation : ligne rouge « ! » en tête de la carte « Prochaine étape » (prio 3), avant les
  -- étapes du trajet (avant : la carte disait à un fantôme d'aller prendre une quête)
  self:Suggest("corpse", text, 3, { ttl = 8, death = true, sound = false, cooldown = 600, topic = "corpse",
    tooltip = table.concat(tips, "\n"),
    onClick = function() if aim then S.aim = nil S:AimCorpse(aim.map, aim.x, aim.y) end end })
  if advice then
    self:Suggest("death", advice, 2, { ttl = 30, death = true, sound = false, tab = "info",
      tooltip = "Ressuscite au gardien des esprits plutôt que courir au corps." })
  else
    self:Clear("death")
  end
end

function S:CheckHearth(st, step, map, x, y)
  local b = self:Bind()
  if not (cfg("hearth") and b and step and not st.ghost and not st.dead and not st.taxi) or step.k == "hs" then
    self:Clear("hearth") return
  end
  -- (QA-QUEST F8) l'itinéraire (Route) décide déjà du foyer pour le 1er trajet, vols et bateaux compris, et le
  -- montre (« Pierre de foyer vers … ») : pas de 2e avis, calculé en ligne droite à pied, qui pourrait le contredire
  local Rt = T.Route
  if Rt and Rt.IsEnabled and Rt:IsEnabled() then self:Clear("hearth") return end
  local dMS = dist(map, x, y, step.map, step.x, step.y)
  local dBS = dist(b.map, b.x, b.y, step.map, step.x, step.y)
  if not (dMS and dBS) or dMS < E.HEARTH_MIN_DIST or not hearthReady() then self:Clear("hearth") return end
  -- même vitesse que la boussole et l'itinéraire (NavTime : mesurée × détour appris), sinon la course du jeu
  local NT = T.NavTime
  local speed = runSpeed()
  if NT and NT.Speed and NT.Detour then
    local okV, v = pcall(NT.Speed, "w")
    local okD, det = pcall(NT.Detour, map)
    if okV and okD and tonumber(v) and v > 0 and tonumber(det) and det >= 1 then speed = v / det end
  end
  local gain = E.HearthGain(dMS, dBS, speed)
  if gain >= E.HEARTH_MIN_GAIN then
    self:Suggest("hearth", E.HearthText(gain), 2, { ttl = 30, sound = false,
      tooltip = "Pour : " .. tostring(step.label or "étape suivante") })
  else
    self:Clear("hearth")
  end
end

--- (4.0.1) l'astuce « pierre de foyer » a-t-elle quelque chose à surveiller ? (option, pas d'Itinéraire, foyer connu)
function S:HearthWatch()
  if not cfg("hearth") then return false end
  local Rt = T.Route
  if Rt and Rt.IsEnabled and Rt:IsEnabled() then return false end
  return self:Bind() ~= nil
end

function S:Tick()
  local st = (T.PlayerState and T:PlayerState()) or {}
  if T.InCombat and T:InCombat() and not st.ghost then return end
  local map, x, y = playerPos()
  local step = nextStep()
  if not (map and x and y) then
    self:Clear("hearth")
    if not st.ghost then self:CheckDeath(st, nil) end
    return
  end
  self:CheckDeath(st, step, map, x, y)
  self:CheckHearth(st, step, map, x, y)
end

function S:OnEnable()
  self:RegisterEvent("HEARTHSTONE_BOUND", function() S:RecordBind() end)
  self:RegisterUnitEvent("UNIT_SPELLCAST_SUCCEEDED", "player", function(_, _, _, _, spellID)
    if tonumber(spellID) == 8690 then S._hsAt = now() end
  end)
  local function arrived()
    if S._hsAt and now() - S._hsAt < 60 then
      S._hsAt = nil
      T:After(2, function() S:RecordBind() end)
    end
  end
  self:RegisterEvent("ZONE_CHANGED_NEW_AREA", arrived)
  self:RegisterEvent("PLAYER_ENTERING_WORLD", arrived)
  self:RegisterEvent("ZONE_CHANGED", arrived)
  -- course au corps : la ligne apparaît dès la libération, s'efface dès le retour à la vie
  self:RegisterEvent("PLAYER_DEAD", function()
    if T.InvalidatePlayerState then T:InvalidatePlayerState() end
    S:OnDead() S:Cadence()
  end)
  local function state()
    if T.InvalidatePlayerState then T:InvalidatePlayerState() end
    if not try(G("UnitIsDeadOrGhost"), "player") then S:ReleaseCorpse() else S:Tick() end
    S:Cadence()
  end
  self:RegisterEvent("PLAYER_ALIVE", state)
  self:RegisterEvent("PLAYER_UNGHOST", state)
  S:Cadence()
end
-- (4.0.1) 1 passage / s pendant une course au corps (fantôme, corps visé), sinon 1 toutes les 3 s ; vivant et rien en
-- cours (ni corps, ni mort notée), seule l'astuce « pierre de foyer » reste, et l'Itinéraire la donne déjà quand il est
-- allumé → rien à calculer (avant : un minuteur d'1 s, position + étape relues toutes les 3 s)
function S:Cadence()
  local ps = T.PlayerState and T:PlayerState()
  local sec = ((ps and (ps.ghost or ps.dead)) or self.aim or self.dead) and 1 or 3
  if self.ticker and self.tickerSec == sec then return end
  if self.ticker then self.ticker:Cancel() end
  self.tickerSec = sec
  self.ticker = T:NewTicker(sec, function()
    local st = T.PlayerState and T:PlayerState()
    if (st and st.ghost) or S.aim then S._tickAt = now() S:Tick() return S:Cadence() end
    if now() - (S._tickAt or -1e9) < 2.5 then return end
    S._tickAt = now()
    if (st and st.dead) or S.dead or S:HearthWatch() then S:Tick() end
    S:Cadence()
  end, self)
  if sec > 1 and self.ticker and self.ticker.IdleStretch then self.ticker:IdleStretch(5) end   -- immobile : 15 s
end
function S:OnDisable()
  if self.ticker then self.ticker:Cancel() end
  self.ticker, self.tickerSec = nil, nil
  self:ReleaseCorpse()
  self:Clear("hearth")
end
