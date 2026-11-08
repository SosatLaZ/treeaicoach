-- qaui_lib.lua (QA-UI, scratch): instrumentation of the mock for text-fit checks + the realistic world of
-- questqa_scenario.lua (level-28 Horde hunter, Mille pointes / Orneval, fake ids). Read-only on the addon.
local L = {}

function L.widget(M)
  local mt = getmetatable(M.NewWidget("Frame"))
  local W
  for i = 1, 20 do
    local n, v = debug.getupvalue(mt.__index, i)
    if not n then break end
    if n == "Widget" then W = v end
  end
  return W
end

function L.instrument(M)
  local Wg = L.widget(M)
  if not Wg or Wg.__qaui then return Wg end
  Wg.__qaui = true
  local origCFS = Wg.CreateFontString
  Wg.CreateFontString = function(self, name, layer, tpl)
    local fs = origCFS(self, name, layer, tpl)
    fs.__tpl = tpl
    return fs
  end
  Wg.SetWordWrap = function(self, v) self.__wrap = v and true or false end
  Wg.SetFont = function(self, file, size) self.__fsize = size return true end
  Wg.SetFontObject = function(self, o) self.__fobj = o end
  local sw = Wg.SetWidth
  Wg.SetWidth = function(self, w) self.__wset = true return sw(self, w) end
  local ss = Wg.SetSize
  Wg.SetSize = function(self, w, h) if w then self.__wset = true end return ss(self, w, h) end
  Wg.SetJustifyH = function(self, j) self.__jh = j end
  Wg.SetTextColor = function(self, r, g, b) self.__color = { r, g, b } end
  return Wg
end

L.SIZE_OF_TPL = { GameFontHighlightSmall = 10, GameFontDisableSmall = 10, GameFontNormalSmall = 10,
  GameFontHighlight = 12, GameFontNormal = 12, GameFontNormalLarge = 16, GameFontNormalHuge = 20,
  NumberFontNormal = 14, NumberFontNormalSmallGray = 12, GameFontHighlightLarge = 16 }
-- the addon's own average glyph widths (Window.lua AVG / Options.lua AVG): S 5.4 · M 6.2 · L 8.2 · H 10.5 px
L.AVG = { [10] = 5.4, [12] = 6.2, [14] = 7.2, [16] = 8.2, [20] = 10.5 }

function L.strip(s)
  return (tostring(s or ""):gsub("|c%x%x%x%x%x%x%x%x", ""):gsub("|r", ""):gsub("|T.-|t", "~~"):gsub("|n", "\n"))
end
function L.ulen(s) local _, n = L.strip(s):gsub("[^\128-\191]", "") return n end
function L.fsize(fs)
  return fs.__fsize or L.SIZE_OF_TPL[fs.__tpl or ""] or 12
end
function L.est(fs, text)
  local sz = L.fsize(fs)
  local avg = L.AVG[sz] or (sz * 0.52)
  local longest = 0
  for line in (L.strip(text or fs.__text) .. "\n"):gmatch("([^\n]*)\n") do
    local _, n = line:gsub("[^\128-\191]", "")
    if n > longest then longest = n end
  end
  return longest * avg
end

-- walk every visible font string under `root`; report the ones with no word wrap whose text is wider than
-- their explicit width (truncated with « … » in game)
function L.overflow(root, out, path)
  out = out or {}
  local function walk(f, depth)
    if type(f) ~= "table" or depth > 12 then return end
    if f.__kind == "FontString" then
      if f.__shown ~= false and f.__text and f.__text ~= "" and f.__wset and f.__wrap == false then
        local need = L.est(f)
        if need > (f.__w or 0) + 4 then
          out[#out + 1] = { text = L.strip(f.__text), need = math.floor(need), have = math.floor(f.__w or 0), size = L.fsize(f) }
        end
      end
      return
    end
    if f.__shown == false then return end
    for _, c in ipairs(f.__children or {}) do walk(c, depth + 1) end
  end
  walk(root, 0)
  return out
end

function L.texts(root)
  local out = {}
  local function walk(f, depth)
    if type(f) ~= "table" or depth > 12 then return end
    if f.__shown == false then return end
    if f.__kind == "FontString" then
      if f.__text and f.__text ~= "" then out[#out + 1] = L.strip(f.__text) end
      return
    end
    for _, c in ipairs(f.__children or {}) do walk(c, depth + 1) end
  end
  walk(root, 0)
  return out
end

-- ------------------------------------------------------------------ realistic world (copied from questqa)
function L.world(M, T, opts)
  opts = opts or {}
  local P, Z, X, R = T.Planner, T.ZonePlan, T.Export, T.Route
  local MAPS = {
    [1440] = { "Orneval", 1, 4672.9, 1700.0, 829.2, -4066.7 },
    [1441] = { "Mille pointes", 1, -3966.7, -433.3, -6900.0, -4833.3 },
    [1413] = { "Les Tarides", 1, 1612.5, 2622.9, -5143.8, -7510.4 },
    [1456] = { "Thunder Bluff", 1, -850.0, 516.7, -1545.8, -527.1 },
    [1412] = { "Mulgore", 1, 266.7, 2479.2, -3835.4, -3675.0 },
    [1442] = { "Les Serres-Rocheuses", 1, 2916.7, 3245.8, -339.6, -1637.5 },
    [1454] = { "Orgrimmar", 1, 2273.9, -3680.6, 1338.5, -5083.2 },
    [1458] = { "Fossoyeuse", 0, 1877.9, 873.2, 1237.8, -86.2 },
    [1420] = { "Clairières de Tirisfal", 0, 3837.5, 3033.3, 825.0, -1485.4 },
  }
  for id, m in pairs(MAPS) do
    M.mapRects[id] = { top = m[3], left = m[4], h = m[3] - m[5], w = m[4] - m[6], continent = m[2] }
    M.mapNames[id] = m[1]
  end
  if T.Geo then T.Geo.rects = {} end
  if Z and Z.ResetMaps then Z.ResetMaps() end
  rawset(_G, "UnitFactionGroup", function() return "Horde", "Horde" end)
  M.player.level = 28
  M.group = {}
  M.FireEvent("GROUP_ROSTER_UPDATE")
  local function goTo(map, x, y)
    M.pos = { mapID = map, x = x / 100, y = y / 100 }
    M.player.zone = MAPS[map][1]
    if Z then Z._lastAllowed = nil end
    M.FireEvent("ZONE_CHANGED_NEW_AREA")
  end
  L.goTo = goTo
  local DUNGEON, PVP, ELITE = 81, 41, 1
  local function obj(text, have, need, typ) return { text = text, have = have, need = need, done = have >= need, type = typ or "monster" } end
  local LOG = {
    { header = "Mille pointes" },
    { id = 5101, title = "Le cavalier céleste", level = 29, map = 1441, objectives = { obj("Œuf de wyverne de Haut-perchoir : 3/10", 3, 10, "item") } },
    { id = 5102, title = "Hypercapaciteur bidulotron", level = 30, tag = ELITE, elite = true, map = 1441,
      objectives = { obj("Hypercapaciteur bidulotron : 0/1", 0, 1, "item") } },
    { id = 5104, title = "L'œuf étrange", level = 26, complete = true, map = 1441, objectives = {} },
    { id = 5105, title = "ON RECHERCHE : Arnak Sinistre", level = 29, complete = true, map = 1441, objectives = {} },
    { id = 5106, title = "Pacifier les centaures", level = 25, complete = true, map = 1441, objectives = {} },
    { id = 5108, title = "Interruption des hurlements", level = 30, map = 1441, objectives = { obj("Hurleur tué : 2/8", 2, 8) } },
    { id = 5109, title = "Les fournitures de Librevent", level = 30, complete = true, map = 1441, objectives = {} },
    { header = "Thunder Bluff" },
    { id = 5103, title = "Broie-l'acier", level = 30, complete = true, map = 1456, objectives = {} },
    { id = 5110, title = "La flamme sacrée", level = 25, map = 1440, objectives = { obj("Fiole d'eau forte remplie : 0/1", 0, 1, "item") } },
    { header = "Les Tarides" },
    { id = 5107, title = "Brouet de rat", level = 23, map = 1413, objectives = { obj("Rat fouisseur : 0/8", 0, 8, "item") } },
    { id = 5120, title = "Des armes de choix", level = 24, map = 1413, objectives = { obj("Poignard de Tranchecrin : 0/1", 0, 1, "item") } },
    { id = 5122, title = "La réclamation de Gann", level = 18, map = 1413, objectives = { obj("Excavateur de Bael'dun tué : 0/15", 0, 15) } },
  }
  M.quests = {}
  for _, q in ipairs(LOG) do M.quests[#M.quests + 1] = q end
  if M.SetCompleted then M.SetCompleted({}) end
  if P and P.char then P.char.turned = {} end
  if M.SetPlan then M.SetPlan(nil) end
  if opts.noQuestie then return LOG end
  local AREAS = { [331] = 1440, [400] = 1441, [17] = 1413, [1638] = 1456, [1497] = 1458, [85] = 1420, [215] = 1412 }
  local function npc(name, lv, area, x, y, rank) return { name = name, minLevel = lv, maxLevel = lv, rank = rank or 0, spawns = { [area] = { { x, y } } } } end
  local function quest(name, ql, rl, zone, giver, ender, kills, items)
    return { name = name, questLevel = ql, requiredLevel = rl, zoneOrSort = zone, startedBy = { { giver } }, finishedBy = { { ender } },
      objectives = { kills and { kills } or nil, nil, items and { items } or nil } }
  end
  local lib = M.InstallQuestieDB({
    areaToMap = AREAS,
    xp = { [5110] = 1850, [5201] = 1750, [5202] = 1750, [5203] = 1800, [5204] = 1750, [5205] = 1650, [5101] = 2650,
      [5104] = 1300, [5105] = 2650, [5106] = 1450, [5108] = 2800, [5109] = 2800, [5103] = 2800, [5111] = 3950,
      [5401] = 2700, [5402] = 2700, [5403] = 2900 },
    quests = {
      [5101] = quest("Le cavalier céleste", 29, 24, 400, 610, 610, nil, { 8101 }),
      [5102] = quest("Hypercapaciteur bidulotron", 30, 25, 400, 611, 611, nil, { 8102 }),
      [5103] = quest("Broie-l'acier", 30, 25, 1638, 620, 620),
      [5104] = quest("L'œuf étrange", 26, 22, 400, 612, 612),
      [5105] = quest("ON RECHERCHE : Arnak Sinistre", 29, 25, 400, 612, 612),
      [5106] = quest("Pacifier les centaures", 25, 21, 400, 612, 612),
      [5108] = quest("Interruption des hurlements", 30, 25, 400, 613, 613, { 7108 }),
      [5109] = quest("Les fournitures de Librevent", 30, 25, 400, 613, 613),
      [5110] = quest("La flamme sacrée", 25, 20, 1638, 620, 620, nil, { 8110 }),
      [5107] = quest("Brouet de rat", 23, 18, 17, 622, 622, nil, { 8107 }),
      [5120] = quest("Des armes de choix", 24, 20, 17, 622, 622, nil, { 8120 }),
      [5122] = quest("La réclamation de Gann", 18, 15, 17, 622, 622, { 7122 }),
      [5401] = quest("Les salines : le venin acide des salines", 30, 25, 400, 613, 613, { 7401 }),
      [5402] = quest("Les carapaces durcies", 30, 25, 400, 613, 613, { 7402 }),
      [5403] = quest("Une affaire de gobelins dans les Salines miroitantes", 30, 26, 400, 611, 611, { 7403 }),
      [5201] = quest("La patte d'Ursangous", 24, 20, 331, 700, 700, { 7201 }),
      [5202] = quest("Entre un roc et un Crin-de-Chardon", 24, 20, 331, 700, 700, { 7202 }),
    },
    npcs = {
      [610] = npc("Cavalier céleste", 30, 400, 46, 50), [611] = npc("Pozzik", 30, 400, 80, 76),
      [612] = npc("Cerf-de-vent", 30, 400, 46, 51), [613] = npc("Nyrill", 30, 400, 45, 50),
      [620] = npc("Sage de Thunder Bluff", 50, 1638, 30, 30), [622] = npc("Rocheyeux", 30, 17, 45, 58),
      [700] = npc("Kuray'bin", 30, 331, 73, 61), [7108] = npc("Hurleur des falaises", 29, 400, 30, 45),
      [7122] = npc("Excavateur de Bael'dun", 24, 17, 48, 85),
      [7401] = npc("Scorpide des salines", 30, 400, 75, 80), [7402] = npc("Tortue des salines", 30, 400, 78, 72),
      [7403] = npc("Contremaître gobelin", 31, 400, 80, 85),
      [7201] = npc("Ursangous", 25, 331, 42, 66), [7202] = npc("Crin-de-Chardon", 24, 331, 79, 54),
      [7101] = npc("Wyverne", 29, 400, 47, 61), [7110] = npc("Puits de lune", 1, 331, 49, 69),
      [7107] = npc("Rat fouisseur", 22, 17, 46, 40), [7120] = npc("Tranchecrin", 23, 17, 43, 82),
    },
    items = {
      [8101] = { name = "Œuf de wyverne", npcDrops = { 7101 } }, [8110] = { name = "Fiole d'eau forte remplie", npcDrops = { 7110 } },
      [8107] = { name = "Rat fouisseur", npcDrops = { 7107 } }, [8120] = { name = "Poignard", npcDrops = { 7120 } },
    },
  })
  return LOG, lib
end

return L
