-- TreeAI — module PvPMark (agent PVPMARK) : surligne sur les barres de nom les VRAIS joueurs ennemis que tu
-- peux attaquer tout de suite (JcJ sauvage en quêtant). Réglage propre, allumé par défaut, indépendant des
-- « aides PvP » (TreeAI.settings.pvp) ; PvP.lua lui laisse l'alerte « ennemi proche » quand il tourne.
--  * joueur (UnitIsPlayer), attaquable (UnitCanAttack + marqué JcJ), vivant, hors sanctuaire ; PNJ, familiers
--    et joueurs non attaquables ignorés
--  * contour couleur de classe + flèche couleur d'estimation, badge d'écart de niveau (« +2 », crâne si ?? ou
--    bien plus fort), « Peut te tuer » / « Combat serré » / « Facile » (niveau + classe)
--  * « Te cible ! » quand ce joueur te cible ; son « danger » une fois par joueur et par minute
-- TAINT : nos PROPRES cadres seulement (jamais de SetParent / déplacement d'une barre de nom Blizzard, aucune
-- fonction protégée). Un repère est créé et ancré sur la barre HORS combat ; en combat, s'il existe déjà, on
-- ne change que textes / couleurs / alpha (autorisé) ; sinon la barre (peut-être protégée) n'est pas touchée et
-- le joueur passe dans une petite liste en haut de l'écran (notre cadre sur UIParent), reconstruite après le
-- combat. Valeurs secrètes en combat → nil (_ns). Uniquement sur événements, aucun travail par image. Lua 5.1.
local tonumber = function(v) return _G.tonumber(v) end   -- (4.0.8) tonumber(f()) : un 2e retour de f n'est jamais une base
local function _ns(v) local f = rawget(_G, "issecretvalue") if f and f(v) then return nil end return v end   -- valeurs secrètes (combat) → nil
local T = _G.TreeAI
if not (T and T.RegisterModule) then return end
local G = _G

local PM = T:RegisterModule("PvPMark", {
  title = "Joueurs ennemis",
  description = "Surligne les joueurs ennemis que tu peux attaquer maintenant, avec leur niveau et le danger.",
  defaults = { enabled = true, mark = true, badge = true, estimate = true, targetMe = true, sound = true, list = true },
  options = {
    { key = "mark", type = "toggle", label = "Surligner les joueurs ennemis attaquables", section = "Barres de nom" },
    { key = "badge", type = "toggle", label = "Écart de niveau (« +2 », crâne si bien plus fort)" },
    { key = "estimate", type = "toggle", label = "Estimation : « Peut te tuer » ou « Facile »" },
    { key = "targetMe", type = "toggle", label = "Signaler un joueur ennemi qui te cible", section = "Alertes" },
    { key = "sound", type = "toggle", label = "Son « danger » (une fois par joueur et par minute)" },
    { key = "list", type = "toggle", label = "En combat : liste des ennemis en haut de l'écran",
      tip = "Pendant un combat, une barre de nom qui apparaît ne peut pas recevoir de repère : le joueur s'affiche dans cette liste." },
  },
})
T.PvPMark = PM

local floor = math.floor
local function pass(ok, ...) if ok then return ... end return nil end
local function try(fn, ...) if type(fn) ~= "function" then return nil end return pass(pcall(fn, ...)) end
local function api(name) return rawget(G, name) end
local function call(name, ...) local f = api(name) if type(f) == "function" then return try(f, ...) end end
local function now() return try(api("GetTime")) or 0 end
local function wipe(t) for k in pairs(t) do t[k] = nil end return t end
local function opt(k) return not (PM.db and PM.db[k] == false) end
local function inCombat() return (T.InCombat and T:InCombat()) and true or false end

PM.SOUND_CD = 60            -- s : un son par joueur au plus
PM.SKULL_DIFF = 5           -- niveaux au-dessus : crâne
PM.MAX_LIST = 5

---------------------------------------------------------------------------------------------------
-- moteur pur (testé)
---------------------------------------------------------------------------------------------------
-- classe ennemie → avantage contre ma classe (+ = plus dangereux pour moi) ; table de PvP.lua si présente
local MATCHUP_FALLBACK = {
  HUNTER = { ROGUE = 2, WARRIOR = 1, MAGE = 1, WARLOCK = 1, SHAMAN = 1, HUNTER = 0, PALADIN = 0, PRIEST = 0, DRUID = 0 },
}
local function matchup(myClass, cls)
  local P = T.PvP
  local mu = (type(P) == "table" and type(P.MATCHUP) == "table" and P.MATCHUP[myClass or ""]) or MATCHUP_FALLBACK[myClass or ""]
  return (mu and cls and tonumber(mu[cls])) or 0
end

--- Estimation contre un joueur ennemi.
-- @return { diff = n|nil, skull = bool, kind = "kill"|"even"|"easy", label = "Peut te tuer"|…, badge = "+2"|"=" }
function PM.Estimate(myLevel, myClass, level, class)
  myLevel = tonumber(myLevel) or 1
  level = tonumber(level)
  local r = {}
  if not level or level <= 0 then
    r.skull, r.kind = true, "kill"
  else
    local diff = level - myLevel
    r.diff = diff
    r.skull = diff >= PM.SKULL_DIFF
    r.badge = diff > 0 and ("+" .. diff) or diff < 0 and tostring(diff) or "="
    local score = diff + matchup(myClass, class)
    r.kind = (r.skull or score >= 3) and "kill" or (score <= -3) and "easy" or "even"
  end
  r.label = r.kind == "kill" and "Peut te tuer" or r.kind == "easy" and "Facile" or "Combat serré"
  return r
end

local KIND_COLOR = { kill = { 1, 0.25, 0.2 }, even = { 1, 0.82, 0.1 }, easy = { 0.3, 0.9, 0.35 } }
PM.KIND_COLOR = KIND_COLOR
function PM.DiffColor(diff)
  if not diff then return 1, 0.25, 0.2 end
  if diff >= 3 then return 1, 0.25, 0.2 elseif diff >= 1 then return 1, 0.55, 0.15
  elseif diff >= -2 then return 1, 0.82, 0.1 elseif diff >= -6 then return 0.3, 0.9, 0.35 end
  return 0.6, 0.6, 0.6
end

---------------------------------------------------------------------------------------------------
-- lecture d'une unité (toutes les valeurs passent par _ns : secrètes en combat → nil)
---------------------------------------------------------------------------------------------------
local function zoneBlocked()
  -- (FIDELITY F5) Forever : C_PvP.GetZonePVPInfo() (le global GetZonePVPInfo n'existe plus) ; repli Era
  local CP = rawget(G, "C_PvP")
  local kind = (type(CP) == "table" and type(CP.GetZonePVPInfo) == "function") and _ns(try(CP.GetZonePVPInfo))
    or _ns(call("GetZonePVPInfo"))
  return kind == "sanctuary"
end
local function inPvPInstance()
  local inst, kind = call("IsInInstance")
  kind = _ns(kind)
  return _ns(inst) and (kind == "pvp" or kind == "arena") and true or false
end

--- Joueur ennemi attaquable maintenant ? (nil si non). Renvoie une fiche { level, class, kind, … }.
function PM.Read(unit)
  if not _ns(call("UnitExists", unit)) then return nil end
  if not _ns(call("UnitIsPlayer", unit)) then return nil end            -- PNJ, familiers : non
  if not _ns(call("UnitCanAttack", "player", unit)) then return nil end  -- amis, non attaquables : non
  -- marqué JcJ (ou JcJ libre) ; valeur secrète / API absente = inconnue → on se fie à UnitCanAttack
  -- (FIDELITY F6) en JcJ sauvage EN COMBAT, UnitIsPVP d'un ennemi est secret : le comparer est une erreur Lua ;
  -- _ns() le rend nil (inconnu) et seul un « non » lisible écarte le joueur
  if api("UnitIsPVP") and _ns(call("UnitIsPVP", unit)) == false and not _ns(call("UnitIsPVPFreeForAll", unit)) then
    return nil
  end
  if _ns(call("UnitIsDeadOrGhost", unit)) then return nil end
  local _, cls = call("UnitClass", unit)
  local e = { unit = unit, class = _ns(cls), level = _ns(call("UnitLevel", unit)) }
  local my = _ns(call("UnitLevel", "player")) or 1
  local est = PM.Estimate(my, T.PlayerClass and T:PlayerClass(), e.level, e.class)
  for k, v in pairs(est) do e[k] = v end
  e.targetsMe = _ns(call("UnitIsUnit", unit .. "target", "player")) and true or false
  e.guid = _ns(call("UnitGUID", unit))
  e.name = _ns(call("UnitName", unit))
  return e
end

local RANGE_SPELL = 75   -- Tir automatique (chasseur)
local function rangeBand(unit)
  local ok
  local CS = api("C_Spell")
  local name = CS and CS.GetSpellName and _ns(try(CS.GetSpellName, RANGE_SPELL))
  if not name then local n = call("GetSpellInfo", RANGE_SPELL) name = _ns(n) end
  if T.PlayerClass and T:PlayerClass() == "HUNTER" and name then
    if CS and CS.IsSpellInRange then ok = _ns(try(CS.IsSpellInRange, name, unit)) end
    if ok == nil then ok = _ns(call("IsSpellInRange", name, unit)) end
    if ok == true or ok == 1 then return "à portée de tir" end
    if ok == false or ok == 0 then return "hors de portée" end
  end
  return "proche"
end
PM.RangeBand = rangeBand

---------------------------------------------------------------------------------------------------
-- repères (nos cadres, un par barre de nom ; jamais SetParent / déplacement de la barre)
---------------------------------------------------------------------------------------------------
local marks = {}      -- plate frame -> our marker frame (ancré une fois, hors combat)
local units = {}      -- unit token -> fiche (joueur ennemi attaquable visible)
local plateOf = {}    -- unit token -> plate frame
local noMark = {}     -- unit token -> true : barre apparue en combat sans repère → liste
local lastSound = {}  -- clé joueur -> GetTime()
PM._marks, PM._units, PM._noMark = marks, units, noMark

local TEX_WHITE = "Interface\\Buttons\\WHITE8X8"
local TEX_ARROW = "Interface\\BUTTONS\\UI-MicroStream-Red"
local TEX_SKULL = "Interface\\TargetingFrame\\UI-TargetingFrame-Skull"

local function locked(...) return (T.FrameLocked and T:FrameLocked(...)) or false end

local function newMark(plate)
  local f = CreateFrame("Frame", nil, G.UIParent)
  -- (4.1.3) le cadre de la plaque est la zone de clic (énorme) : on entoure la barre de vie seulement, au plus juste
  local uf = rawget(plate, "UnitFrame")
  local hb = type(uf) == "table" and (rawget(uf, "healthBar") or rawget(uf, "HealthBarsContainer")) or nil
  local anchor = (type(hb) == "table" and hb.GetWidth) and hb or plate
  if anchor == plate then
    f:SetPoint("CENTER", plate, "CENTER", 0, 0)
    f:SetSize(110, 14)
  else
    f:SetPoint("TOPLEFT", anchor, "TOPLEFT", -1, 1)
    f:SetPoint("BOTTOMRIGHT", anchor, "BOTTOMRIGHT", 1, -1)
  end
  local strata = try(plate.GetFrameStrata, plate)
  if strata then f:SetFrameStrata(strata) end
  f:SetFrameLevel((tonumber(try(plate.GetFrameLevel, plate)) or 1) + 12)
  f.edges = {}
  local function edge(p1, p2, horiz)
    local t = f:CreateTexture(nil, "OVERLAY")
    t:SetTexture(TEX_WHITE)
    t:SetPoint(p1, f, p1, 0, 0)
    t:SetPoint(p2, f, p2, 0, 0)
    if horiz then t:SetHeight(1) else t:SetWidth(1) end
    f.edges[#f.edges + 1] = t
  end
  edge("TOPLEFT", "TOPRIGHT", true)
  edge("BOTTOMLEFT", "BOTTOMRIGHT", true)
  edge("TOPLEFT", "BOTTOMLEFT", false)
  edge("TOPRIGHT", "BOTTOMRIGHT", false)
  f.arrow = f:CreateTexture(nil, "OVERLAY")
  f.arrow:SetTexture(TEX_ARROW)
  f.arrow:SetSize(14, 14)
  f.arrow:SetPoint("BOTTOM", f, "TOP", 0, 10)
  f.skull = f:CreateTexture(nil, "OVERLAY")
  f.skull:SetTexture(TEX_SKULL)
  f.skull:SetSize(12, 12)
  f.skull:SetPoint("LEFT", f, "RIGHT", 2, 0)
  f.badge = f:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
  f.badge:SetPoint("LEFT", f, "RIGHT", 2, 0)
  f.est = f:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
  f.est:SetPoint("BOTTOM", f, "TOP", 0, 1)
  f.tme = f:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
  f.tme:SetPoint("TOP", f, "BOTTOM", 0, -1)
  f.tme:SetTextColor(1, 0.5, 0.1)
  return f
end

local function setAlpha(r, a) if r and r.SetAlpha then r:SetAlpha(a) end end

--- Contenu d'un repère : textes / couleurs / alpha seulement (autorisé en combat).
local function paint(f, e)
  local cr, cg, cb = 1, 0.25, 0.2
  local Th = T.Theme
  if e.class and Th and Th.ClassColor then cr, cg, cb = Th:ClassColor(e.class) end
  for _, t in ipairs(f.edges) do t:SetVertexColor(cr, cg, cb, 0.95) end
  local kc = KIND_COLOR[e.kind] or KIND_COLOR.even
  f.arrow:SetVertexColor(kc[1], kc[2], kc[3], 1)
  local skull = opt("badge") and e.skull
  setAlpha(f.skull, skull and 1 or 0)
  f.badge:SetText((opt("badge") and not skull and e.badge) or "")
  f.badge:SetTextColor(PM.DiffColor(e.diff))
  f.est:SetText(opt("estimate") and e.label or "")
  f.est:SetTextColor(kc[1], kc[2], kc[3])
  f.tme:SetText((opt("targetMe") and e.targetsMe) and "Te cible !" or "")
  f:SetAlpha(1)
end

local function unmark(unit)
  local plate = plateOf[unit]
  local f = plate and marks[plate]
  if f then
    f:SetAlpha(0)
    if not inCombat() and not locked(f) then f:Hide() end
  end
end

---------------------------------------------------------------------------------------------------
-- liste de secours en combat (notre cadre sur UIParent : jamais protégé)
---------------------------------------------------------------------------------------------------
local list
local function listFrame()
  if list then return list end
  list = CreateFrame("Frame", "TreeAIPvPMarkList", G.UIParent)
  list:SetSize(260, 16)
  list:SetPoint("TOP", G.UIParent, "TOP", 0, -150)
  list:SetFrameStrata("MEDIUM")
  list.title = list:CreateFontString(nil, "OVERLAY", "GameFontNormal")
  list.title:SetPoint("TOP", list, "TOP", 0, 0)
  list.title:SetTextColor(1, 0.3, 0.25)
  list.title:SetText("Joueurs ennemis attaquables")
  list.lines = {}
  for i = 1, PM.MAX_LIST do
    local fs = list:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
    fs:SetPoint("TOP", list, "TOP", 0, -4 - 14 * i)
    list.lines[i] = fs
  end
  list:Hide()
  return list
end

local function className(cls)
  local loc = api("LOCALIZED_CLASS_NAMES_MALE")
  return (type(loc) == "table" and cls and loc[cls]) or cls or "Joueur"
end

function PM.ListLine(e, band)
  local lvl = e.skull and not e.diff and "??" or tostring(e.level or "??")
  local diff = e.badge and e.badge ~= "=" and (" (" .. e.badge .. ")") or ""
  local s = ("%s niv %s%s · %s"):format(className(e.class), lvl, diff, e.label or "")
  if e.targetsMe then s = s .. " · te cible" end
  if band then s = s .. " · " .. band end
  return s
end

function PM:UpdateList()
  local rows = {}
  if inCombat() and opt("list") and opt("mark") then
    for unit in pairs(noMark) do
      local e = units[unit]
      if e then rows[#rows + 1] = e end
    end
  end
  if #rows == 0 then
    if list and list:IsShown() then list:Hide() end
    self.listLines = nil
    return
  end
  table.sort(rows, function(a, b)
    if a.targetsMe ~= b.targetsMe then return a.targetsMe end
    local ka, kb = a.kind == "kill" and 1 or 0, b.kind == "kill" and 1 or 0
    if ka ~= kb then return ka > kb end
    return (a.unit or "") < (b.unit or "")
  end)
  local f = listFrame()
  self.listLines = {}
  for i = 1, PM.MAX_LIST do
    local e = rows[i]
    local txt = e and PM.ListLine(e, rangeBand(e.unit)) or ""
    f.lines[i]:SetText(txt)
    if e then
      local kc = KIND_COLOR[e.kind] or KIND_COLOR.even
      f.lines[i]:SetTextColor(kc[1], kc[2], kc[3])
      self.listLines[#self.listLines + 1] = txt
    end
  end
  f:SetHeight(18 + 14 * math.min(#rows, PM.MAX_LIST))
  if not f:IsShown() then f:Show() end
end

---------------------------------------------------------------------------------------------------
-- cœur : une barre de nom
---------------------------------------------------------------------------------------------------
local function soundKey(e)
  return e.guid or e.name or ((e.class or "?") .. ":" .. tostring(e.level) .. ":" .. tostring(e.unit))
end

local function maybeSound(e)
  if not (opt("sound") and (e.kind == "kill" or e.targetsMe)) then return end
  if inPvPInstance() then return end   -- champ de bataille / arène : tout le monde est ennemi, pas de son
  local k, t = soundKey(e), now()
  if lastSound[k] and t - lastSound[k] < PM.SOUND_CD then return end
  lastSound[k] = t
  PM.sounds = (PM.sounds or 0) + 1
  if T.Sound then try(T.Sound, T, "danger") end
end

function PM:Active() return self._active and opt("mark") and true or false end

function PM:UpdateUnit(unit)
  if type(unit) ~= "string" or not unit:match("^nameplate%d+$") then return end
  local e = (self:Active() and not zoneBlocked()) and PM.Read(unit) or nil
  if not e then
    if units[unit] then unmark(unit) end
    units[unit], noMark[unit] = nil, nil
    return
  end
  local was = units[unit]
  units[unit] = e
  local NP = api("C_NamePlate")
  local plate = NP and NP.GetNamePlateForUnit and try(NP.GetNamePlateForUnit, unit)
  if plateOf[unit] and plateOf[unit] ~= plate then unmark(unit) end
  plateOf[unit] = plate
  local f = plate and marks[plate]
  if plate and not f and not locked(plate) then
    f = newMark(plate)   -- hors combat (ou barre non protégée) : on crée et on ancre
    marks[plate] = f
  end
  if f then
    if not f:IsShown() then
      if locked(f) then f = nil else f:Show() end
    end
  end
  if f then
    paint(f, e)
    noMark[unit] = nil
  else
    noMark[unit] = true   -- barre protégée en combat : liste de secours, repère après le combat
  end
  if not was or (e.targetsMe and not was.targetsMe) then maybeSound(e) end
end

function PM:RemoveUnit(unit)
  if units[unit] then unmark(unit) end
  units[unit], noMark[unit], plateOf[unit] = nil, nil, nil
end

function PM:RefreshAll()
  for unit in pairs(units) do self:UpdateUnit(unit) end
  local NP = api("C_NamePlate")
  if NP and NP.GetNamePlates then
    for _, p in ipairs(try(NP.GetNamePlates) or {}) do
      local u = type(p) == "table" and (p.namePlateUnitToken or (type(p.UnitFrame) == "table" and p.UnitFrame.unit))
      if type(u) == "string" and not units[u] then self:UpdateUnit(u) end
    end
  end
  self:UpdateList()
end

function PM:ClearAll()
  for unit in pairs(units) do unmark(unit) end
  wipe(units) wipe(noMark) wipe(plateOf)
  self:UpdateList()
end

--- Nombre de joueurs ennemis attaquables suivis (tests, autres modules).
function PM:Count()
  local n = 0
  for _ in pairs(units) do n = n + 1 end
  return n
end
function PM:Info(unit) return units[unit] end

---------------------------------------------------------------------------------------------------
-- événements
---------------------------------------------------------------------------------------------------
function PM:OnEnable()
  if T.OnPlates then   -- (5.0 STABLE) barres de nom regroupées par le cœur ; une barre qui part : repère retiré tout de suite
    T:OnPlates(self, function(s, us)
      for unit in pairs(us) do s:UpdateUnit(unit) end
      s:UpdateList()
    end, function(s, unit)
      if units[unit] or plateOf[unit] then s:RemoveUnit(unit) s:UpdateList() end
    end)
  else
    self:RegisterEvent("NAME_PLATE_UNIT_ADDED", function(s, _, unit) s:UpdateUnit(unit) s:UpdateList() end)
    self:RegisterEvent("NAME_PLATE_UNIT_REMOVED", function(s, _, unit)
      if units[unit] or plateOf[unit] then s:RemoveUnit(unit) s:UpdateList() end
    end)
  end
  local function one(s, _, unit)
    if unit == "player" then s:RefreshAll() return end
    if type(unit) == "string" and (units[unit] or unit:match("^nameplate%d+$")) then s:UpdateUnit(unit) s:UpdateList() end
  end
  self:RegisterEvent("UNIT_FACTION", one)
  self:RegisterEvent("PLAYER_FLAGS_CHANGED", one)
  -- qui cible qui : seulement les joueurs ennemis déjà suivis
  self:RegisterEvent("UNIT_TARGET", function(s, _, unit)
    if units[unit] then s:UpdateUnit(unit) s:UpdateList() end
  end)
  -- mort d'un joueur suivi (UNIT_HEALTH filtré : une recherche de table pour les autres unités)
  self:RegisterEvent("UNIT_HEALTH", function(s, _, unit)
    if units[unit] and _ns(call("UnitIsDeadOrGhost", unit)) then s:RemoveUnit(unit) s:UpdateList() end
  end)
  self:RegisterEvent("PLAYER_REGEN_ENABLED", function(s) s:RefreshAll() end)   -- repères manqués en combat
  self:RegisterEvent("PLAYER_REGEN_DISABLED", function(s) s:UpdateList() end)
  self:RegisterEvent("ZONE_CHANGED_NEW_AREA", function(s) s:RefreshAll() end)
  self:RegisterEvent("PLAYER_ENTERING_WORLD", function(s) s:RefreshAll() end)
  self:RegisterEvent("PLAYER_LEVEL_UP", function(s) s:RefreshAll() end)
  self:RefreshAll()
end

function PM:OnDisable()
  self:UnregisterAllEvents()
  self:ClearAll()
end

function PM:OnOptionChanged()
  if not self._active then return end
  if opt("mark") then self:RefreshAll() else self:ClearAll() end
end
