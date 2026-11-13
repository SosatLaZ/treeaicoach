-- TreeAI — module MinimapPins (agent MINIPINS) : NOS points sur la VRAIE minimap de Blizzard, comme Questie /
-- TomTom — plus de minimap à part. Seulement ce que Questie ne montre pas déjà :
--   cible de la boussole / prochaine étape du plan (sauf si Blizzard / TomTom / Shortest Path vise déjà ce point),
--   étapes du chemin sûr, rares vus, zones d'élites près de ton chemin (anneaux doux), lieux de butin de tes objets
--   de quête (seulement sans Questie), amis LOIN (au-delà des points de groupe de Blizzard : flèche au bord + nom),
--   maîtres de vol pas encore découverts près de toi, HV / boîte aux lettres / réparation les plus proches quand
--   il le faut (sacs pleins, réparation : module Prévisions).
-- Deux moteurs :
--   1. HereBeDragons-Pins-2.0 si un addon chargé l'embarque (Questie, TomTom, HandyNotes…) : AddMinimapIconMap /
--      RemoveMinimapIcon (la lib place, tourne, colle au bord) ;
--   2. sinon le nôtre : petits cadres enfants de Minimap, placés depuis la position monde du joueur +
--      Minimap:GetZoom() (table yards / zoom intérieur-extérieur de HereBeDragons), rotation (CVar rotateMinimap +
--      GetPlayerFacing), collés au bord avec flèche. ≤ 10 i/s, uniquement les points visibles.
-- Taint : on ne touche JAMAIS aux scripts / textures / taille / forme de Minimap, aucun hook ; nos cadres ne
-- prennent la souris que pour leur propre infobulle (anneaux : jamais). Survol = distance, clic = boussole.
-- Données : les sources du module Radar (lecture), nos relevés (butin de quête, services) en SavedVariables.
-- Lua 5.1, aucune globale.
local tonumber = function(v) return _G.tonumber(v) end   -- (4.0.8) tonumber(f()) : un 2e retour de f n'est jamais une base
local _, ns = ...
ns = ns or {}

local T = _G.TreeAI
if not (T and T.RegisterModule) then return end

local LAYERS = { "target", "rares", "danger", "drops", "squad", "taxi", "services" }   -- (4.0) « chemin sûr » retiré avec SafePath
local LABELS = {
  target = "Cible de la boussole / prochaine étape",
  rares = "Rares vus",
  danger = "Élites près de ton chemin (anneaux)",
  drops = "Butin de tes objets de quête (sans Questie)",
  squad = "Amis loin (flèche au bord)",
  taxi = "Maîtres de vol à découvrir",
  services = "HV / courrier / réparation quand il faut",
}
local opts = {}
for _, l in ipairs(LAYERS) do opts[#opts + 1] = { key = l, type = "toggle", label = LABELS[l], section = "Points sur la minimap" } end

local MP = T:RegisterModule("MinimapPins", {
  title = "Points sur la minimap",
  description = "Nos repères sur la minimap de Blizzard, seulement ce que Questie ne montre pas.",
  -- (4.1 BUGHUNT) « target » coupé d'office : UN pointeur à l'écran, la boussole (plus de flèche « 76 m » au bord
  -- de la minimap qui double la boussole) ; une option à cocher pour la remettre
  defaults = { enabled = true, target = false, rares = true, danger = true, drops = true,
               squad = true, taxi = true, services = true },
  charDefaults = { drops = {}, services = {} },
  options = opts,
})
ns.MinimapPins = MP
T.MinimapPins = MP
MP.LAYERS, MP.LABELS = LAYERS, LABELS

local floor, sqrt, atan2, cos, sin, pi, abs, max, min =
  math.floor, math.sqrt, math.atan2, math.cos, math.sin, math.pi, math.abs, math.max, math.min
local STEP = 0.2                 -- ≤ 5 placements / s (LITE)
MP.STILL_SLOW = 15               -- (4.4 SMART) liste refaite au plus toutes les 15 s quand le joueur ne bouge pas
local CIRCLE = "Interface\\CharacterFrame\\TempPortraitAlphaMask"
local ARROW = "Interface\\Minimap\\MinimapArrow"

-- diamètre (yards) de la minimap par niveau de zoom 0..5 (valeurs de HereBeDragons-Pins)
MP.YARDS = {
  indoor = { 300, 240, 180, 120, 80, 50 },
  outdoor = { 466 + 2 / 3, 400, 333 + 1 / 3, 266 + 2 / 3, 200, 133 + 1 / 3 },
}

local function tryPass(ok, a, ...) if ok then return a, ... end return nil end   -- (LITE) sans table
local function try(fn, ...)
  if type(fn) ~= "function" then return nil end
  return tryPass(pcall(fn, ...))
end
local function now() return try(_G.GetTime) or 0 end
local function epoch() return try(_G.time) or floor(now()) end
local cut = T.Cut   -- (4.0) Core : n caractères UTF-8 (« … » compris), jamais une lettre accentuée coupée
local function secret(v)
  local f = rawget(_G, "issecretvalue")
  return type(f) == "function" and try(f, v) and true or false
end
local function num(v) if secret(v) then return nil end return tonumber(v) end
local function opt(k) return MP.db and MP.db[k] ~= false end
local function mod(name) return T.GetModule and T:GetModule(name) or nil end
local function radar() return T.Radar or mod("Radar") end
local function minimap() return rawget(_G, "Minimap") end

-- ------------------------------------------------------------------ détection (lecture seule)
local function addonLoaded(name)
  local CA = rawget(_G, "C_AddOns")
  if CA and CA.IsAddOnLoaded and try(CA.IsAddOnLoaded, name) then return true end
  return try(rawget(_G, "IsAddOnLoaded"), name) and true or false
end
function MP.QuestieLoaded()
  return (rawget(_G, "QuestieLoader") ~= nil or rawget(_G, "Questie") ~= nil or addonLoaded("Questie")) and true or false
end
-- couches que Questie montre déjà sur la minimap → jamais en double
MP.QUESTIE_COVERS = { drops = true }
function MP.LayerOn(layer)
  if not opt(layer) then return false end
  -- (5.0 STABLE) guide de quêtes coupé : plus de point de butin de quête (la cible de la boussole n'a plus de quête)
  if layer == "drops" and T.QuestGuideOn and not T:QuestGuideOn() then return false end
  if MP.QUESTIE_COVERS[layer] and MP.QuestieLoaded() then return false end
  return true
end

-- HereBeDragons-Pins-2.0 (embarquée par un autre addon) ; nil sinon
function MP.HBD()
  local LS = rawget(_G, "LibStub")
  if not LS then return nil end
  local pins
  if type(LS) == "table" and type(LS.GetLibrary) == "function" then pins = try(LS.GetLibrary, LS, "HereBeDragons-Pins-2.0", true) end
  if not pins then
    local ok, lib = pcall(LS, "HereBeDragons-Pins-2.0", true)   -- LibStub est une table appelable
    if ok then pins = lib end
  end
  if type(pins) == "table" and type(pins.AddMinimapIconMap) == "function" and type(pins.RemoveMinimapIcon) == "function" then
    return pins
  end
  return nil
end

-- ------------------------------------------------------------------ géométrie (pure, testable)
-- rayon de la minimap en yards (zoom 0..5, intérieur / extérieur)
function MP.YardsRadius(zoom, indoor)
  local t = indoor and MP.YARDS.indoor or MP.YARDS.outdoor
  zoom = floor(tonumber(zoom) or 0)
  if zoom < 0 then zoom = 0 elseif zoom > 5 then zoom = 5 end
  return t[zoom + 1] / 2
end

-- Décalage écran (px, depuis le centre de la minimap) d'un point à dn yards au nord / dw yards à l'ouest.
-- rotate = minimap qui tourne (le haut = devant moi) ; yr = rayon en yards ; pr = rayon en pixels ;
-- shape "SQUARE" = minimap carrée ; inset = marge au bord (px). Retourne px, py, dist (yd), edge, angle.
function MP.Offset(dn, dw, facing, rotate, yr, pr, shape, inset)
  local sx, sy = -dw, dn
  if rotate and facing then
    local c, s = cos(-facing), sin(-facing)
    sx, sy = sx * c - sy * s, sx * s + sy * c
  end
  local dist = sqrt(dn * dn + dw * dw)
  local k = pr / yr
  local px, py = sx * k, sy * k
  local lim = pr - (inset or 0)
  local edge = false
  if shape == "SQUARE" then
    local m = max(abs(px), abs(py))
    if m > lim and m > 0 then edge = true px, py = px * lim / m, py * lim / m end
  else
    local d = sqrt(px * px + py * py)
    if d > lim and d > 0 then edge = true px, py = px * lim / d, py * lim / d end
  end
  return px, py, dist, edge, atan2(py, px)
end

-- état de la minimap : rayon yards / pixels, rotation, cap, forme
function MP.View()
  local mm = minimap()
  if not mm then return nil end
  local zoom = num(try(mm.GetZoom, mm)) or 0
  local indoor = try(rawget(_G, "IsIndoors")) and true or false
  local w = num(try(mm.GetWidth, mm)) or 140
  local facing = num(try(_G.GetPlayerFacing))
  local rot = try(_G.GetCVar, "rotateMinimap")
  local shape = try(rawget(_G, "GetMinimapShape"))
  return { yr = MP.YardsRadius(zoom, indoor), pr = w / 2, zoom = zoom, indoor = indoor, facing = facing,
           rotate = (rot == "1" or rot == 1 or rot == true) and facing ~= nil, shape = shape == "SQUARE" and "SQUARE" or "ROUND" }
end

local function d2(a, b) return sqrt((a.n - b.n) ^ 2 + (a.w - b.w) ^ 2) end

-- ------------------------------------------------------------------ relevés : butin de quête, services
local DROP_TTL, SVC_TTL = 3 * 86400, 60 * 86400
local function record(list, me, name, extra, maxn, ttl)
  local R = radar()
  if R and R.Record then return R.Record(list, me, name, extra, maxn, ttl) end
end

function MP.OnLoot()
  if not (MP.char and MP.LayerOn("drops")) then return end
  local R, Q = radar(), mod("Quests")
  local me = R and R.Me and R.Me()
  if not (me and Q and Q.ObjectiveFor) then return end
  local n = num(try(_G.GetNumLootItems)) or 0
  for i = 1, min(n, 20) do
    local _, name, _, _, _, _, isQuest = try(_G.GetLootSlotInfo, i)
    if type(name) == "string" and not secret(name) and (isQuest or try(Q.ObjectiveFor, name, false)) then
      record(MP.char.drops, me, name, nil, 40, DROP_TTL)
      MP.slowAt = 0
    end
  end
end

-- kind = "mail" | "ah" | "vendor" | "repair" : position du joueur pendant l'interaction (± 5 yd du PNJ)
function MP.LearnService(kind, label)
  local R = radar()
  local me = MP.char and R and R.Me and R.Me()
  if not me then return end
  MP.char.services[kind] = MP.char.services[kind] or {}
  record(MP.char.services[kind], me, label or kind, { sub = try(_G.GetSubZoneText) }, 30, SVC_TTL)
  MP.slowAt = 0
end

-- besoin d'un service ? (Prévisions : sacs / réparation ; sinon lecture directe)
function MP.Needs()
  local need = {}
  local P = T.Predict   -- (CLEAN 4.0) façade de Stats : les lignes réelles d'Inventaire
  for _, f in ipairs(P and type(P.list) == "table" and P.list or {}) do
    if f.id == "bags" and (f.prio or 0) >= 2 then need.bags = true end
    if f.id == "repair" and (f.prio or 0) >= 2 then need.repair = true end
  end
  local API = T.API
  local free = API and API.FreeSlots and num(try(API.FreeSlots))
  if free and free <= 2 then need.bags = true end
  if P and P.Durability then
    local _, worst = try(P.Durability)
    worst = num(worst)
    if worst and worst <= 25 then need.repair = true end
  end
  return need
end

-- ------------------------------------------------------------------ couches
local SVC = { mail = "Boîte aux lettres", ah = "Hôtel des ventes", vendor = "Marchand", repair = "Réparation" }
local CLASS_FR = { HUNTER = "Chasseur", WARRIOR = "Guerrier", MAGE = "Mage", PRIEST = "Prêtre", ROGUE = "Voleur",
  DRUID = "Druide", PALADIN = "Paladin", SHAMAN = "Chaman", WARLOCK = "Démoniste" }
MP.TARGET_NEAR = 30              -- yd : deux cibles plus proches = la même
MP.DANGER_NEAR = 120             -- yd d'une étape / de la cible (ou de moi) pour afficher une zone d'élites
MP.TAXI_NEAR = 800               -- yd : maîtres de vol à découvrir « près de toi »

local function otherGuide(t)
  local C = T.Compass or mod("Compass")
  if C and C.OtherGuide then return try(C.OtherGuide, t) end
end

-- pins = { key, kind, layer, c, n, w, label, lines, color, float, ring, mapID?, x?, y?, questID? }
function MP.Collect(me, view)
  local R = radar()
  local pins = {}
  if not (R and me) then return pins end
  local function list(fn) local ok, l = pcall(fn, me) return ok and type(l) == "table" and l or {} end
  local anchors = { me }

  -- 1. cible de la boussole + prochaine étape du plan (une seule fois, jamais si un autre guide l'affiche)
  if MP.LayerOn("target") and R.TargetPins then
    local kept = {}
    for _, p in ipairs(list(R.TargetPins)) do
      local step = p.step
      if not step or step == 1 then
        local dup = otherGuide({ key = p.key, questID = p.questID, mapID = p.mapID, x = p.x, y = p.y })
        for _, q in ipairs(kept) do if d2(p, q) < MP.TARGET_NEAR then dup = true end end
        if not dup then
          p.float, p.layer = true, "target"
          kept[#kept + 1] = p
          pins[#pins + 1] = p
          anchors[#anchors + 1] = p
        end
      end
    end
  end
  -- 3./4. rares + élites (anneaux doux, seulement près de moi / de mon chemin)
  if (MP.LayerOn("rares") or MP.LayerOn("danger")) and R.SeenPins then
    for _, p in ipairs(list(R.SeenPins)) do
      if p.kind == "rare" and MP.LayerOn("rares") then
        pins[#pins + 1] = p
      elseif p.kind == "danger" and MP.LayerOn("danger") then
        local near = false
        for _, a in ipairs(anchors) do if d2(p, a) <= MP.DANGER_NEAR + (a == me and 80 or 0) then near = true break end end
        if near then p.ring = 40 p.float = nil pins[#pins + 1] = p end
      end
    end
  end
  -- 5. butin de quête (sans Questie) : objectifs encore à faire
  if MP.LayerOn("drops") and MP.char then
    local Q = mod("Quests")
    local t = epoch()
    for i, e in ipairs(MP.char.drops or {}) do
      if e.c == me.c and t - (e.t or 0) <= DROP_TTL and Q and Q.ObjectiveFor then
        local o = try(Q.ObjectiveFor, e.name, false)
        if o and not (type(o) == "table" and o.finished) then
          pins[#pins + 1] = { key = "drop:" .. i, kind = "drop", layer = "drops", c = e.c, n = e.n, w = e.w,
            label = "Butin de quête : " .. cut(e.name, 40),
            lines = { { "Ramassé ici (" .. (e.seen or 1) .. " fois)", 1, 0.82, 0 } } }
        end
      end
    end
  end
  -- 6. amis LOIN : au-delà du rayon de la minimap (Blizzard montre déjà les proches)
  if MP.LayerOn("squad") and R.SquadPins then
    local yr = view and view.yr or MP.YardsRadius(0, false)
    for _, p in ipairs(list(R.SquadPins)) do
      if d2(p, me) > yr then p.float, p.named = true, true pins[#pins + 1] = p end
    end
  end
  -- 7. maîtres de vol pas encore découverts, près de moi
  if MP.LayerOn("taxi") and R.TaxiPins then
    for _, p in ipairs(list(R.TaxiPins)) do
      if p.known == false and d2(p, me) <= MP.TAXI_NEAR then
        p.lines = { { "Pas encore découvert — va le voir", 0.6, 1, 0.6 } }
        pins[#pins + 1] = p
      end
    end
  end
  -- 8. services quand il faut : le plus proche de chaque sorte (relevés)
  if MP.LayerOn("services") and MP.char then
    local need = MP.Needs()
    local kinds = {}
    if need.bags then kinds[#kinds + 1] = "vendor" kinds[#kinds + 1] = "mail" kinds[#kinds + 1] = "ah" end
    if need.repair then kinds[#kinds + 1] = "repair" end
    for _, k in ipairs(kinds) do
      local best, bd
      for _, e in ipairs(MP.char.services[k] or {}) do
        if e.c == me.c then
          local d = d2(e, me)
          if not bd or d < bd then best, bd = e, d end
        end
      end
      if best then
        pins[#pins + 1] = { key = "svc:" .. k, kind = "svc_" .. k, layer = "services", c = best.c, n = best.n, w = best.w,
          float = true, label = SVC[k] .. (best.sub and best.sub ~= "" and (" — " .. cut(best.sub, 30)) or ""),
          lines = { { (k == "repair" and "Tes objets s'abîment : répare ici")
            or (k == "vendor" and "Sacs presque pleins : vends tes objets gris ici")
            or (k == "mail" and "Sacs presque pleins : envoie le surplus à un reroll")
            or "Sacs presque pleins : vends à l'HV ce qui a de la valeur", 1, 0.6, 0.3 } } }
      end
    end
  end
  return pins
end

-- ------------------------------------------------------------------ pins (nos cadres)
local ICON = {
  target = { "Interface\\TargetingFrame\\UI-RaidTargetingIcon_1", nil, 14 },
  rare = { "Interface\\TargetingFrame\\UI-RaidTargetingIcon_3", nil, 13 },
  danger = { CIRCLE, { 1, 0.25, 0.2 }, 10 },
  drop = { CIRCLE, { 1, 0.82, 0.1 }, 8 },
  squad = { CIRCLE, nil, 9 },
  taxi = { "Interface\\TaxiFrame\\UI-Taxi-Icon-Gray", nil, 13 },
  svc_mail = { "Interface\\Minimap\\Tracking\\Mailbox", nil, 14 },
  svc_ah = { "Interface\\Minimap\\Tracking\\Auctioneer", nil, 14 },
  svc_vendor = { "Interface\\Minimap\\Tracking\\Banker", nil, 14 },
  svc_repair = { "Interface\\Minimap\\Tracking\\Repair", nil, 14 },
}
MP.pool = {}

-- (UIREDO) chaque point dit : ce que c'est, à quelle distance et en combien de temps, pourquoi il compte
-- maintenant, et ce que fait le clic. Un point sans raison n'est pas montré (Collect ne garde que ceux-là).
local WHY = {
  target = "Ta prochaine étape",
  rare = "Rare vu ici : bon butin, souvent seul", danger = "Élites ici : passe autour",
  squad = "Ton ami est loin : rejoins-le pour avancer ensemble", drop = "Ton objet de quête tombe ici",
}
MP.WHY = WHY
function MP.TipLines(pin, me)
  local out = {}
  local R = radar()
  if me and pin.n and me.c == pin.c then
    local d = d2(pin, me)
    local dist = R and R.Dist and R.Dist(d) or (floor(d * 0.9144 + 0.5) .. " m")
    local NT = T.NavTime
    local sec = (NT and NT.Model and try(NT.Model, d, pin.mapID)) or d * 1.3 / 7
    local Pl = T.Plain
    -- (REPLACE 4.0.1) Plain.Minutes dit déjà « ≈ 3 min » : plus de « ≈ ≈ »
    local eta = (Pl and Pl.Minutes and Pl.Minutes(sec)) or ("≈ " .. floor(sec / 60 + 0.5) .. " min")
    out[#out + 1] = { dist .. " · " .. eta .. " à pied", 0.6, 0.8, 1 }
  end
  local any = false
  for _, l in ipairs(pin.lines or {}) do
    if type(l) == "table" and l[1] then out[#out + 1] = l any = true end
  end
  if not any then
    local why = WHY[pin.layer == "target" and "target" or pin.kind] or WHY[pin.layer or ""]
    if why then out[#out + 1] = { why, 1, 0.82, 0.2 } end
  end
  out[#out + 1] = { "Clic : la boussole vise ce point", 0.4, 0.8, 1 }
  return out
end
local function showTip(owner, pin)
  local tt = _G.GameTooltip
  if not (tt and pin) then return end
  try(tt.SetOwner, tt, owner, "ANCHOR_LEFT")
  try(tt.AddLine, tt, pin.label or "?", 1, 1, 1, true)
  local R = radar()
  local me = R and R.Me and R.Me()
  for _, l in ipairs(MP.TipLines(pin, me)) do try(tt.AddLine, tt, l[1], l[2] or 0.8, l[3] or 0.8, l[4] or 0.8, true) end
  try(tt.Show, tt)
end
local function hideTip() local tt = _G.GameTooltip if tt then try(tt.Hide, tt) end end

function MP.Click(pin)
  local R = radar()
  if not (pin and R and R.SetTarget) then return nil end
  -- (4.0 QA) la boussole qui tourne vers le point est la réponse au clic : rien dans le chat
  return R.SetTarget(pin)
end

local function newPin(i)
  local mm = minimap()
  if not (mm and _G.CreateFrame) then return nil end
  local b = _G.CreateFrame("Button", nil, mm)           -- enfant de Minimap (comme HereBeDragons) ; Minimap intacte
  b:SetSize(12, 12)
  local lvl = num(try(mm.GetFrameLevel, mm)) or 1
  try(b.SetFrameLevel, b, lvl + 5)
  b.icon = b:CreateTexture(nil, "OVERLAY")
  b.icon:SetAllPoints(b)
  b.arrow = b:CreateTexture(nil, "OVERLAY")
  b.arrow:SetTexture(ARROW)
  b.arrow:SetSize(12, 12)
  b.arrow:Hide()
  b.name = b:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
  b.name:SetPoint("TOP", b, "BOTTOM", 0, -1)
  b.name:Hide()
  try(b.RegisterForClicks, b, "LeftButtonUp")
  b:SetScript("OnEnter", function(self) showTip(self, self.pin) end)
  b:SetScript("OnLeave", hideTip)
  b:SetScript("OnClick", function(self)
    if T.UserAction then T:UserAction(MP.Click, self.pin) else MP.Click(self.pin) end
  end)
  b:Hide()
  MP.pool[i] = b
  return b
end

local function stylePin(b, p, k)
  local ic = ICON[p.kind] or ICON.drop
  local col = p.color or ic[2]
  b.icon:SetTexture(ic[1])
  if col then b.icon:SetVertexColor(col[1], col[2], col[3], 1) else b.icon:SetVertexColor(1, 1, 1, 1) end
  if p.ring then
    -- zone d'élites : disque doux à l'échelle (± 40 yd), jamais sous la souris
    local s = max(10, floor(2 * p.ring * (k or 0.3) + 0.5))
    b:SetSize(s, s)
    b.icon:SetVertexColor(1, 0.25, 0.2, 0.22)
    b:EnableMouse(false)
  else
    b:SetSize(ic[3], ic[3])
    b:EnableMouse(true)
  end
  b.size = p.ring and 0 or ic[3]
  if p.named then b.name:SetText(cut(p.label, 16)) b.name:Show() else b.name:Hide() end
  b.arrow:SetVertexColor((col or { 1, 0.82, 0 })[1], (col or { 1, 0.82, 0 })[2], (col or { 1, 0.82, 0 })[3], 1)
end

-- ------------------------------------------------------------------ moteur HereBeDragons
MP.hbdShown = {}
function MP.SyncHBD(pins, me, view)
  local H = MP.HBD()
  if not H then return false end
  local R = radar()
  local want = {}
  local used = 0
  for _, p in ipairs(pins) do
    local mapID, x, y = p.mapID, p.x, p.y
    if not (mapID and x) and R and R.WorldToMap and me.mapID then mapID = me.mapID x, y = R.WorldToMap(me.mapID, p.c, p.n, p.w) end
    if mapID and x and y then
      used = used + 1
      local b = MP.pool[used] or newPin(used)
      if not b then break end
      local k = view and view.pr / view.yr
      stylePin(b, p, k)
      b.pin, b.edgeShown = p, nil
      local ok = pcall(H.AddMinimapIconMap, H, MP, b, mapID, x, y, false, p.float and true or false)
      if ok then want[b] = true end
    end
  end
  for b in pairs(MP.hbdShown) do
    if not want[b] then pcall(H.RemoveMinimapIcon, H, MP, b) b.pin = nil end
  end
  MP.hbdShown = want
  MP.shown = used
  MP.engine = "hbd"
  return true
end

local function clearHBD()
  local H = MP.HBD()
  for b in pairs(MP.hbdShown) do if H then pcall(H.RemoveMinimapIcon, H, MP, b) end b:Hide() b.pin = nil end
  MP.hbdShown = {}
end

-- ------------------------------------------------------------------ moteur maison (placement 10 i/s)
function MP.Place(me, view)
  local used = 0
  local k = view.pr / view.yr
  for _, p in ipairs(MP.pins or {}) do
    if p.c == me.c then
      local ic = ICON[p.kind] or ICON.drop
      local inset = p.ring and 0 or (ic[3] / 2)
      local px, py, dist, edge, ang = MP.Offset(p.n - me.n, p.w - me.w, view.facing, view.rotate, view.yr, view.pr, view.shape, inset)
      if not edge or p.float then
        used = used + 1
        local b = MP.pool[used] or newPin(used)
        if not b then break end
        if b.pin ~= p or b.k ~= k then stylePin(b, p, k) b.k = k end
        b.pin = p
        p.dist, p.px, p.py, p.edge = dist, px, py, edge
        b:ClearAllPoints()
        b:SetPoint("CENTER", minimap(), "CENTER", px, py)
        if edge then
          b.arrow:ClearAllPoints()
          local ax, ay = cos(ang) * (inset + 6), sin(ang) * (inset + 6)
          b.arrow:SetPoint("CENTER", b, "CENTER", ax, ay)
          if b.arrow.SetRotation then pcall(b.arrow.SetRotation, b.arrow, ang - pi / 2) end
          b.arrow:Show()
          b:SetAlpha(0.9)
        else
          b.arrow:Hide()
          b:SetAlpha(1)
        end
        b:Show()
      end
    end
  end
  for i = used + 1, #MP.pool do local b = MP.pool[i] if b:IsShown() then b:Hide() end b.pin = nil end
  MP.shown = used
  MP.engine = "own"
end

function MP.HideAll()
  if next(MP.hbdShown) then clearHBD() end
  for _, b in ipairs(MP.pool) do if b:IsShown() then b:Hide() end b.pin = nil end
  MP.shown = 0
  hideTip()
end

-- une passe : liste (1 Hz) + placement (≤ 10 Hz, si quelque chose a bougé)
MP.cost, MP.updates = 0, 0
function MP.Update(force)
  local t = now()
  if not force and t - (MP.lastAt or -1) < STEP then return end
  MP.lastAt = t
  local mm = minimap()
  -- (4.4 SMART) immobile (ni position ni orientation changées : Core, relevé 1 / s + PLAYER_STARTED_MOVING) et rien de
  -- neuf (journal, zone, zoom → MP.sig effacé) : les épingles sont déjà à leur place, aucune relecture de position ;
  -- la liste se refait toutes les 15 s à l'arrêt au lieu de 3 s (avant : 5 relevés de position / s pour rien)
  local qg = T.QuestGen and T:QuestGen() or 0
  if not force and MP.sig and MP.pins and MP.qg == qg and T.Moving and not T:Moving(1.5)
      and t - (MP.slowAt or 0) < MP.STILL_SLOW and MP.engine ~= "hbd" then return end
  MP.qg = qg
  local R = radar()
  local me = R and R.Me and R.Me()
  local visible = mm and (not mm.IsVisible or try(mm.IsVisible, mm))
  if not (MP:IsEnabled() and me and visible) then MP.HideAll() MP.pins = nil return end
  local view = MP.View()
  if not view then return end
  MP.updates = MP.updates + 1
  if force or not MP.pins or t - (MP.slowAt or 0) >= 3 or MP.pinsMap ~= me.mapID then   -- (LITE) liste : 3 s
    MP.pins = MP.Collect(me, view)
    MP.slowAt, MP.pinsMap, MP.sig = t, me.mapID, nil
    if MP.SyncHBD(MP.pins, me, view) then
      for i = MP.shown + 1, #MP.pool do MP.pool[i]:Hide() MP.pool[i].pin = nil end
      return
    end
    if next(MP.hbdShown) then clearHBD() end
  end
  if MP.engine == "hbd" and MP.HBD() then return end
  -- (SPIKE) signature sans chaîne : 5 fois / s, une concaténation = des déchets pour le ramasse-miettes
  local sg = MP.sigT
  if not sg then sg = {}; MP.sigT = sg end
  local a, b, c = floor(me.n * 2), floor(me.w * 2), floor((view.facing or 0) * 50)
  if MP.sig and sg[1] == a and sg[2] == b and sg[3] == c and sg[4] == view.rotate and sg[5] == view.yr
      and sg[6] == view.pr and sg[7] == MP.pins then return end
  sg[1], sg[2], sg[3], sg[4], sg[5], sg[6], sg[7] = a, b, c, view.rotate, view.yr, view.pr, MP.pins
  MP.sig = true
  MP.Place(me, view)
end

-- ------------------------------------------------------------------ évènements
local driver
function MP:OnEnable()
  if type(self.db) == "table" and not self.db.v41 then self.db.v41, self.db.target = 1, false end   -- (4.1) une fois
  self.char.drops = self.char.drops or {}
  self.char.services = self.char.services or {}
  local mm = minimap()
  if mm and _G.CreateFrame and not driver then
    -- notre propre cadre invisible, enfant de Minimap : son OnUpdate ne tourne que si la minimap est visible
    driver = _G.CreateFrame("Frame", nil, mm)
    driver:SetSize(1, 1)
    try(driver.EnableMouse, driver, false)
    MP.driver = driver
  end
  if driver then
    driver:SetScript("OnUpdate", function() MP.Update() end)
    driver:Show()
  end
  for _, ev in ipairs({ "PLAYER_ENTERING_WORLD", "ZONE_CHANGED_NEW_AREA", "ZONE_CHANGED_INDOORS", "MINIMAP_UPDATE_ZOOM",
    "CVAR_UPDATE" }) do
    self:RegisterEvent(ev, function() MP.slowAt, MP.sig = 0, nil end)
  end
  self:RegisterEvent("LOOT_OPENED", function() MP.OnLoot() end)
  self:RegisterEvent("MAIL_SHOW", function() MP.LearnService("mail") end)
  self:RegisterEvent("AUCTION_HOUSE_SHOW", function() MP.LearnService("ah") end)
  self:RegisterEvent("MERCHANT_SHOW", function()
    MP.LearnService("vendor")
    if try(rawget(_G, "CanMerchantRepair")) then MP.LearnService("repair") end
  end)
  -- client moderne : un seul évènement d'interaction (types : Enum.PlayerInteractionType)
  self:RegisterEvent("PLAYER_INTERACTION_MANAGER_FRAME_SHOW", function(_, _, kind)
    local E = rawget(_G, "Enum") and _G.Enum.PlayerInteractionType
    if type(E) ~= "table" or kind == nil then return end
    if kind == E.MailInfo then MP.LearnService("mail")
    elseif kind == E.Auctioneer then MP.LearnService("ah")
    elseif kind == E.Merchant then
      MP.LearnService("vendor")
      if try(rawget(_G, "CanMerchantRepair")) then MP.LearnService("repair") end
    end
  end)
  MP.slowAt = 0
end

function MP:OnDisable()
  if driver then driver:SetScript("OnUpdate", nil) end
  MP.HideAll()
  MP.pins = nil
end

function MP:OnOptionChanged()
  MP.slowAt, MP.sig = 0, nil
  MP.Update(true)
end

function MP.Status()
  local counts = {}
  for _, p in ipairs(MP.pins or {}) do counts[p.layer or p.kind] = (counts[p.layer or p.kind] or 0) + 1 end
  local parts = {}
  for _, l in ipairs(LAYERS) do if counts[l] then parts[#parts + 1] = l .. " " .. counts[l] end end
  return ("Minimap : %d points (%s), moteur %s%s"):format(MP.shown or 0, #parts > 0 and table.concat(parts, ", ") or "aucun",
    MP.HBD() and "HereBeDragons" or "TreeAI", MP.QuestieLoaded() and ", Questie détecté (butin de quête laissé à Questie)" or "")
end

if T.RegisterSlash then
  T:RegisterSlash("minimap", function() MP:Print(MP.Status()) end, "minimap : nos points sur la minimap (statut)")
end
