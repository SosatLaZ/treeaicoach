-- TreeAI — module « Rares » (fichier Boost.lua, nom interne "Boost" gardé pour les réglages des joueurs).
-- CLEAN 4.0 : l'ancien « Boost » faisait 9 choses sans lien ; chacune est chez son propriétaire ou retirée :
--   récompense de quête + infobulles d'objets → Équipement (Gear) · XP/h → Statistiques (Stats, « sur N min »)
--   · récolte (nœuds, dépeçage) → Métiers (Prof) · munitions → Chasseur · nourriture / pierre de foyer → Inventaire
--   · menace (secrète en combat sur Forever), aspect de voyage (Chasseur l'a), apprivoisement (Chercher l'a) : retirés.
-- Reste ici UNE fonction : un rare (ou un trésor) tout près = UNE ligne ; clic = la boussole y va, et un crâne sur le
-- rare s'il est à portée (hors combat, sur ton clic seulement : SetRaidTarget). Un son au plus par rare (10 min).
-- (4.0 QA) Seulement s'il est à la portée de TON groupe (M.CanHandle : écart de niveau ≤ 2 seul, ≤ 3 à deux, ≤ 4 à
-- trois et plus ; un rare élite veut un groupe) : sinon la ligne prévient « trop fort, évite-le », sans son ni crâne.
-- Sources : barres de nom / cible / survol (classification « rare ») et vignettes du jeu (TreeAI.Detect, position
-- exacte). Pour les autres modules, ce fichier garde l'ancien contrat Boost (XPStats, UpgradePct…) : il renvoie
-- vers Stats et Gear, sans calcul à lui. Lua 5.1, aucune globale, toutes les API testées.
local tonumber = function(v) return _G.tonumber(v) end   -- (4.0.8) tonumber(f()) : un 2e retour de f n'est jamais une base
local T = _G.TreeAI
if not T or not T.RegisterModule then return end
local API = T.API or {}

local M = T:RegisterModule("Boost", {
  title = "Rares",
  defaults = { enabled = true, rares = true, rareSound = true },
  options = {
    { key = "rares", type = "toggle", label = "Une ligne quand un rare ou un trésor est tout près (clic : y aller)" },
    { key = "rareSound", type = "toggle", label = "Petit son pour un rare (une fois par rare)" },
  },
})
T.Boost = M

local G = _G
local function pass(ok, ...) if ok then return ... end local sw = _G.TreeAI and _G.TreeAI.Swallow if sw then sw((...), "Boost") end return nil end
local function try(fn, ...)
  if type(fn) ~= "function" then return nil end
  return pass(pcall(fn, ...))
end
local function now() return (G.GetTime and G.GetTime()) or 0 end
local function opt(k) return M.db and M.db[k] end
local function inCombat() return T:InCombat() end
local function pub(v) if API.Pub then return API.Pub(v) end return v end   -- valeur secrète (combat) -> nil

local stats = { rares = 0, lastRare = nil }
local seen = {}          -- guid -> time (10 min)
local markGUID           -- dernier rare, pour le crâne

local function findUnitByGUID(guid)
  for _, u in ipairs({ "target", "mouseover", "focus" }) do
    if pub(try(G.UnitGUID, u)) == guid then return u end
  end
  for i = 1, 40 do
    local u = "nameplate" .. i
    if pub(try(G.UnitGUID, u)) == guid then return u end
  end
end

local function markRare()
  if inCombat() then return false end
  local unit = markGUID and findUnitByGUID(markGUID)
  if not unit then return false end
  if G.SetRaidTarget then try(G.SetRaidTarget, unit, 8) end -- 8 = crâne
  return true
end

--- un rare que ton groupe peut tuer ? (pur) cls = classification, lvl = son niveau (0 / -1 = « ?? »), my = ton
--- niveau, grp = taille du groupe (1 = seul). Niveau inconnu (vignette) : seul le rang décide.
function M.CanHandle(cls, lvl, my, grp)
  lvl, my, grp = tonumber(lvl), tonumber(my) or 1, math.max(1, tonumber(grp) or 1)
  if cls == "worldboss" or (lvl and lvl <= 0) then return false end      -- boss du monde, « niveau ?? »
  local gap = lvl and (lvl - my) or nil
  if cls == "rareelite" then                                           -- un élite est fait pour un groupe
    if not gap then return grp >= 3 end
    if grp >= 3 then return gap <= 2 end
    if grp == 2 then return gap <= 0 end
    return gap <= -3
  end
  if not gap then return true end
  return gap <= (grp >= 3 and 4 or (grp == 2 and 3 or 2))
end
local function myLevel() return tonumber(pub(try(G.UnitLevel, "player"))) or 1 end
local function groupSize() return math.max(1, tonumber(pub(try(G.GetNumGroupMembers))) or 0) end
--- ligne « trop fort » (pure) : « Rare élite X (niv 33) tout près : trop fort pour toi seul, évite-le »
function M.TooHardText(cls, name, lvl, grp)
  local lv = (tonumber(lvl) or 0) > 0 and (" (niv " .. lvl .. ")") or (lvl and " (niveau ??)" or "")
  local what = cls == "rare" and "Rare" or (cls == "worldboss" and "Boss" or "Rare élite")
  return ("%s %s%s tout près : trop fort pour %s, évite-le"):format(what, name, lv,
    (tonumber(grp) or 1) <= 1 and "toi seul" or "ton groupe")
end

local function scanUnit(unit)
  if not opt("rares") or not unit or not pub(try(G.UnitExists, unit)) then return end
  if pub(try(G.UnitIsPlayer, unit)) or pub(try(G.UnitIsDead, unit)) then return end
  if G.UnitCanAttack and not pub(try(G.UnitCanAttack, "player", unit)) then return end
  local cls = pub(try(G.UnitClassification, unit))
  if cls ~= "rare" and cls ~= "rareelite" and cls ~= "worldboss" then return end
  local guid = pub(try(G.UnitGUID, unit))
  if not guid then return end
  local t = now()
  if seen[guid] and t - seen[guid] < 600 then return end
  seen[guid] = t
  local name = pub(try(G.UnitName, unit))
  if type(name) ~= "string" or name == "" then return end
  local lvlRaw = tonumber(pub(try(G.UnitLevel, unit)))   -- -1 = « ?? » ; nil = illisible
  local lvl = lvlRaw or 0
  local lv = lvl > 0 and (" (niv " .. lvl .. ")") or " (niveau ??)"
  stats.rares, stats.lastRare = stats.rares + 1, name
  local grp = groupSize()
  if not M.CanHandle(cls, lvlRaw, myLevel(), grp) then
    -- trop fort pour ton groupe : on prévient, sans son ni crâne (jamais « vise-le »)
    M:Suggest("rare", M.TooHardText(cls, name, lvlRaw, grp), 1, { ttl = 60, cooldown = 5, sound = false,
      tooltip = { "Un rare trop haut ou élite tue vite : reviens plus tard ou à plusieurs." } })
    return
  end
  markGUID = guid
  local label = (cls == "rareelite") and "le rare élite" or (cls == "worldboss" and "le boss" or "le rare")
  M:Suggest("rare", "Vise " .. label .. " " .. name .. lv .. " : il est tout près (clic : crâne)", 2,
    { ttl = 90, cooldown = 5, sound = opt("rareSound") and true or false, soundKind = "rare",
      tooltip = { "Clic hors combat : crâne sur lui (s'il est à portée).", "Un rare = bon butin et grosse XP." },
      onClick = function() markRare() end })
end
M.ScanUnit = scanUnit

-- rares / trésors vus par TreeAI.Detect (vignettes du jeu : position exacte) : une ligne, clic = la boussole y
-- va (+ crâne si le rare est à portée, hors combat). Même id "rare" que les barres de nom : jamais de doublon.
function M.OnDetectRares(list)
  if not opt("rares") or type(list) ~= "table" then return end
  local Dt = T.Detect
  local t = now()
  for _, e in ipairs(list) do
    -- un rare vu sur une barre de nom / la cible : la ligne de scanUnit (même id) s'en charge
    if type(e) == "table" and e.kind ~= "event" and e.kind ~= "vignette" and e.src ~= "plate" then
      if e.guid then
        if seen[e.guid] and t - seen[e.guid] < 600 then e = nil else seen[e.guid] = t end
      end
      if e then
        local treasure = e.kind == "treasure"
        local txt = (Dt and Dt.Describe and Dt.Describe(e)) or ((treasure and "Trésor : " or "Rare : ") .. tostring(e.name))
        local click = function()
          if e.guid then markGUID = e.guid end
          if Dt and Dt.Target then Dt.Target(e) end
          if not treasure and not inCombat() and e.guid and findUnitByGUID(e.guid) then markRare() end
        end
        if treasure then
          M:Suggest("treasure", txt .. " (clic : y aller)", 1, { ttl = 90, cooldown = 30, sound = false, onClick = click })
        else
          stats.rares, stats.lastRare = stats.rares + 1, e.name
          local grp = groupSize()
          if not M.CanHandle(e.kind, e.level, myLevel(), grp) then
            local nm = type(e.name) == "string" and e.name ~= "" and e.name or nil
            if nm then   -- sans nom lu : pas de ligne (jamais « ? »)
              M:Suggest("rare", M.TooHardText(e.kind, nm, e.level, grp), 1, { ttl = 60, cooldown = 5,
                sound = false, tooltip = { "Un rare trop haut ou élite tue vite : reviens plus tard ou à plusieurs." } })
            end
          else
            if e.guid then markGUID = e.guid end
            M:Suggest("rare", txt .. " (clic : y aller)", 2,
              { ttl = 90, cooldown = 5, sound = opt("rareSound") and true or false, soundKind = "rare", onClick = click })
          end
        end
      end
    end
  end
end

---------------------------------------------------------------------------------------------------
-- Ancien contrat « Boost » (lu par Quick, Dungeon, Abandon, Todo, Hub, Squad, Export) : simples renvois.
---------------------------------------------------------------------------------------------------
local function gear() return T.Gear end
function M.UpgradePct(link) local Gm = gear() return Gm and Gm.UpgradePct and Gm.UpgradePct(link) end
function M.CanUse(link) local Gm = gear() if Gm and Gm.CanUse then return Gm.CanUse(link) end return true end
function M.ScoreStats(st, slot, w) local Gm = gear() return Gm and Gm.ScoreStats and Gm.ScoreStats(st, slot, w) end
--- XP/h MESURÉE (Stats, au moins 10 min de jeu dans la dernière heure) et temps avant le niveau ; nil sinon
function M:XPStats()
  local St = T.Stats
  local xph, sec
  if St and St.RecentXPPerHour then xph, sec = St:RecentXPPerHour() end
  local cur, mx = tonumber(pub(try(G.UnitXP, "player"))) or 0, tonumber(pub(try(G.UnitXPMax, "player"))) or 0
  if not (xph and xph > 0) or mx <= 0 then return nil end
  local left = mx - cur
  return { perHour = math.floor(xph), eta = left / xph * 3600, remaining = left, minutes = sec and math.floor(sec / 60) or nil,
    rested = tonumber(pub(try(G.GetXPExhaustion))) or 0, nextLevel = (tonumber(pub(try(G.UnitLevel, "player"))) or 1) + 1 }
end
function M:XPPerHour() local r = M:XPStats() return r and r.perHour or nil end

---------------------------------------------------------------------------------------------------
-- Cycle de vie
---------------------------------------------------------------------------------------------------
function M:OnEnable()
  self:RegisterEvent("NAME_PLATE_UNIT_ADDED", function(_, _, unit) scanUnit(unit) end)
  self:RegisterEvent("PLAYER_TARGET_CHANGED", function() scanUnit("target") end)
  self:RegisterEvent("UPDATE_MOUSEOVER_UNIT", function() scanUnit("mouseover") end)
  self:On("SUMMARY", function()
    local s = T:Summary()
    if type(s) == "table" then s.boost = { raresSeen = stats.rares, lastRare = stats.lastRare } end
  end)
  local Dt = T.Detect
  if not M.detectSub and type(Dt) == "table" and type(Dt.OnChange) == "function" then
    M.detectSub = Dt:OnChange(function(kind, data) if kind == "rares" and M:IsEnabled() then M.OnDetectRares(data) end end)
  end
end

function M:OnDisable()
  self:Clear("rare")
  self:Clear("treasure")
end
