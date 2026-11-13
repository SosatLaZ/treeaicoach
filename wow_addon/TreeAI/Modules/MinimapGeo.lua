local tonumber = function(v) return _G.tonumber(v) end   -- (4.0.8) tonumber(f()) : un 2e retour de f n'est jamais une base
-- TreeAI — MinimapGeo (5.1 CLEAN) : la géométrie de la vraie minimap, reprise de MinimapPins.lua (module retiré : ses
-- propres points étaient coupés pour tout le monde depuis la 4.1). Ses fonctions servaient pourtant toujours aux
-- icônes de quêtes (QuestPins), au chemin pointillé (PathDraw) et aux services en ville (CityRun) sur la minimap :
-- elles restent ici, sous le même nom (T.MinimapPins) pour ces modules. Pas de module, pas d'événement, aucun cadre :
-- des fonctions pures + la lecture de la minimap à la demande. Lua 5.1, aucune globale.
local T = _G.TreeAI
if not T then return end
local floor, sqrt, cos, sin, abs, max, atan2 = math.floor, math.sqrt, math.cos, math.sin, math.abs, math.max, math.atan2
local MP = {}
local function tryPass(ok, a, ...) if ok then return a, ... end local sw = _G.TreeAI and _G.TreeAI.Swallow if sw then sw(a, "MinimapGeo") end return nil end
local function try(fn, ...)
  if type(fn) ~= "function" then return nil end
  return tryPass(pcall(fn, ...))
end
local function secret(v)
  local f = rawget(_G, "issecretvalue")
  return type(f) == "function" and try(f, v) and true or false
end
local function num(v) if secret(v) then return nil end return tonumber(v) end
local function minimap() return rawget(_G, "Minimap") end

-- diamètre (yards) de la minimap par niveau de zoom 0..5 (valeurs de HereBeDragons-Pins)
-- diamètre (yards) de la minimap par niveau de zoom 0..5 (valeurs de HereBeDragons-Pins)
MP.YARDS = {
  indoor = { 300, 240, 180, 120, 80, 50 },
  outdoor = { 466 + 2 / 3, 400, 333 + 1 / 3, 266 + 2 / 3, 200, 133 + 1 / 3 },
}

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

T.MinimapPins = MP   -- (nom gardé : QuestPins, PathDraw, CityRun le lisent)
T.MinimapGeo = MP
