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

local function hearthReady()
  local s, d = try(C("C_Container", "GetItemCooldown"), 6948)
  if not s then s, d = try(G("GetItemCooldown"), 6948) end
  s, d = tonumber(s), tonumber(d)
  if not s or s == 0 then return (try(G("GetItemCount"), 6948) or 0) > 0 end
  return (s + (d or 0) - now()) <= 0
end

function S:CheckDeath(st, step, map, x, y)
  if not (cfg("death") and st.ghost and step) then self:Clear("death") return end
  local f = C("C_DeathInfo", "GetCorpseMapPosition")
  local v = f and try(f, map)
  local cx, cy
  if type(v) == "table" then
    if v.GetXY then cx, cy = try(v.GetXY, v) else cx, cy = v.x, v.y end
  end
  if not (tonumber(cx) and tonumber(cy)) then self:Clear("death") return end
  cx, cy = cx * 100, cy * 100
  local dMC = dist(map, x, y, map, cx, cy)
  local dCN = dist(map, cx, cy, step.map, step.x, step.y)
  local dMN = dist(map, x, y, step.map, step.x, step.y)
  if not (dMC and dCN and dMN) then self:Clear("death") return end
  local lvl = try(G("UnitLevel"), "player") or 1
  local choice, gain, sick = E.DeathChoice(lvl, dMC, dCN, dMN, runSpeed(), step.k == "obj" or step.k == "grind")
  if choice == "spirit" and gain >= E.DEATH_MIN_GAIN then
    self:Suggest("death", E.DeathText(gain, sick), 2, { ttl = 30, death = true, sound = false,
      tooltip = "Ressuscite ici plutôt que courir au corps. Calcul : fantôme → étape suivante contre fantôme → corps → étape suivante, mal de résurrection compris." })
  else
    self:Clear("death")
  end
end

function S:CheckHearth(st, step, map, x, y)
  local b = self:Bind()
  if not (cfg("hearth") and b and step and not st.ghost and not st.dead and not st.taxi) or step.k == "hs" then
    self:Clear("hearth") return
  end
  local dMS = dist(map, x, y, step.map, step.x, step.y)
  local dBS = dist(b.map, b.x, b.y, step.map, step.x, step.y)
  if not (dMS and dBS) or dMS < E.HEARTH_MIN_DIST or not hearthReady() then self:Clear("hearth") return end
  local gain = E.HearthGain(dMS, dBS, runSpeed())
  if gain >= E.HEARTH_MIN_GAIN then
    self:Suggest("hearth", E.HearthText(gain), 2, { ttl = 30, sound = false,
      tooltip = "Pour : " .. tostring(step.label or "étape suivante") })
  else
    self:Clear("hearth")
  end
end

function S:Tick()
  if T.InCombat and T:InCombat() then return end
  local st = (T.PlayerState and T:PlayerState()) or {}
  local map, x, y = playerPos()
  local step = nextStep()
  if not (map and x and y) then self:Clear("death"); self:Clear("hearth") return end
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
  self.ticker = T:NewTicker(3, function() S:Tick() end)
end
function S:OnDisable()
  if self.ticker then self.ticker:Cancel() end
  self:Clear("death"); self:Clear("hearth")
end
