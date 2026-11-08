-- Scenario for Modules/MinimapPins.lua (agent MINIPINS), run on both API flavours by tests/wow/test_minipins.py.
-- Mock world: map 1424 = 3000 x 2000 yd, player at map (0.5, 0.4) = world north 200, west 500, continent 0.
-- Mock Minimap: 140 px wide (radius 70), Minimap:GetZoom() = M.minimapZoom, IsIndoors() = M.indoors.
-- Fake names only.
return function(M, ctx)
  local T = TreeAI
  local MP, R, C = T:GetModule("MinimapPins"), T:GetModule("Radar"), T:GetModule("Compass")
  local check = ctx.check
  check(MP and R and C and MP.db and MP.db.enabled, "MinimapPins + Radar + Compass registered, MinimapPins enabled")
  if not (MP and R and C) then return end
  local pi = math.pi
  local function near(a, b, eps) return a and b and math.abs(a - b) <= (eps or 1e-6) end
  local function pt(px, py, x, y, msg) check(near(px, x, 1e-4) and near(py, y, 1e-4), msg .. (": %s, %s"):format(tostring(px), tostring(py))) end
  M.LeaveCombat()

  -- radar à part : caché par défaut (migration une fois), réactivable
  check(R.db.shown == false and R.db.offByDefault == true, "round radar OFF by default (migration flag set)")
  R.db.shown = true
  R:OnEnable()
  check(R.db.shown == true, "migration runs only once: re-enabling the radar sticks")
  R.db.shown = false
  R.Visibility()

  -- ------------------------------------------------------------- pure geometry, exact numbers
  check(near(MP.YardsRadius(0, false), 700 / 3) and near(MP.YardsRadius(5, false), 200 / 3), "outdoor yards radius (zoom 0 / 5)")
  check(near(MP.YardsRadius(0, true), 150) and near(MP.YardsRadius(5, true), 25) and near(MP.YardsRadius(9, true), 25), "indoor yards radius + clamp")
  local px, py, d, edge = MP.Offset(200, 0, 0, false, 700 / 3, 70, "ROUND", 7)
  pt(px, py, 0, 60, "200 yd north, zoom 0 outdoor, 70 px → (0, 60)")
  check(near(d, 200) and edge == false, "inside the minimap")
  px, py = MP.Offset(200, 0, pi / 2, true, 700 / 3, 70, "ROUND", 0)
  pt(px, py, 60, 0, "rotating minimap, facing west: north on the right → (60, 0)")
  px, py = MP.Offset(0, 100, pi / 2, true, 700 / 3, 70, "ROUND", 0)
  pt(px, py, 0, 30, "rotating minimap, facing west: a point west straight ahead → (0, 30)")
  px, py, d, edge = MP.Offset(300, -400, 0, false, 700 / 3, 70, "ROUND", 5)
  pt(px, py, 52, 39, "500 yd: 300 north, 400 east (3-4-5) → rim minus inset 5 → (52, 39)")
  check(edge and near(d, 500), "edge flag + real distance kept")
  px, py, d, edge = MP.Offset(300, -400, 0, false, 700 / 3, 70, "SQUARE", 5)
  pt(px, py, 65, 48.75, "square minimap: clamp on the box side → (65, 48.75)")
  px, py = MP.Offset(0, -100, 0, false, 150, 70, "ROUND", 0)
  pt(px, py, 140 / 3, 0, "indoor zoom 0: 100 yd east → (46.67, 0)")

  -- ------------------------------------------------------------- live pins (own engine)
  local mm = Minimap
  local mmScripts = 0
  for _ in pairs(mm.__scripts) do mmScripts = mmScripts + 1 end
  local mmPoints = #mm.__points
  for _, l in ipairs(MP.LAYERS) do MP.db[l] = true end
  M.minimapZoom, M.indoors, M.facing = 0, false, 0
  M.cvars.rotateMinimap = "0"
  rawset(_G, "LibStub", nil)
  rawset(_G, "QuestieLoader", nil)
  C.SetWaypoint(0.5, 0.3, "Point test")              -- 200 yd north
  M.group = { { name = "Amie", class = "MAGE", level = 26, online = true }, { name = "Proche", class = "PRIEST", level = 26, online = true } }
  local oldPos = C_Map.GetPlayerMapPosition
  C_Map.GetPlayerMapPosition = function(id, u)
    if u == "party1" then return { x = 0.6, y = 0.4, GetXY = function(p) return p.x, p.y end } end      -- 300 yd east
    if u == "party2" then return { x = 0.5 - 30 / 3000, y = 0.4, GetXY = function(p) return p.x, p.y end } end  -- 30 yd west
    return oldPos(id, u)
  end
  rawset(_G, "C_TaxiMap", { GetTaxiNodesForMap = function(m)
    if m == 1424 then return { { nodeID = 7, name = "Moulin-connu", position = { x = 0.5, y = 0.5 }, isUndiscovered = false, faction = 0 },
      { nodeID = 8, name = "Poste-a-voir", position = { x = 0.45, y = 0.4 }, isUndiscovered = true, faction = 0 } } end
    return {}
  end })
  local t = time()
  R.char.rares = { { name = "Croc-test", c = 0, n = 200, w = 550, t = t, seen = 1, lvl = 28 } }         -- 50 yd west
  R.char.dangers = { { name = "Ogre-test", c = 0, n = 250, w = 500, t = t, seen = 1, lvl = 30 },           -- 50 yd north
                     { name = "Ogre-loin", c = 0, n = 1500, w = 2000, t = t, seen = 1, lvl = 30 } }        -- far from everything
  local Q = T:GetModule("Quests")
  local oldList = Q and Q.lastList
  if Q then Q.lastList = { { id = 901, title = "Quête-test", obj = { { name = "Patte de test", have = 1, need = 6 } } } } end
  MP.char.drops = { { name = "Patte de test", c = 0, n = 140, w = 500, t = t, seen = 3 } }                -- 60 yd south
  MP.char.services = { repair = { { name = "repair", c = 0, n = 200, w = 300, t = t, seen = 1, sub = "Forge-test" } } }  -- 200 yd east
  -- (4.0) Predict retiré : « il faut réparer » = la ligne réelle de l'Inventaire, lue par la façade T.Predict.list
  local P = T.Predict
  T:Suggest("Inventory:inv_repair", "Répare au plus vite : équipement cassé (rouge)", 3, { module = "Inventory", sound = false })

  MP.Update(true)
  check(MP.engine == "own", "no HereBeDragons → own engine (" .. tostring(MP.engine) .. ")")
  local function pinOf(kind, key)
    for _, b in ipairs(MP.pool) do
      if b.pin and b:IsShown() and b.pin.kind == kind and (not key or b.pin.key == key) then return b end
    end
  end
  local function at(b) local p = b and b.__points[#b.__points] return p and p[4], p and p[5], p and p[2] end
  local tb = pinOf("target")
  check(tb ~= nil, "compass target pin on the minimap")
  if tb then
    local x, y, rel = at(tb)
    pt(x, y, 0, 60, "target 200 yd north → (0, 60)")
    check(tb:GetParent() == Minimap and rel == Minimap, "pin is a child of Minimap, anchored to its centre")
  end
  local fb = pinOf("squad")
  check(fb and fb.pin.label == "Amie", "far friend shown")
  check(not pinOf("squad", "sq:Proche"), "close friend left to Blizzard's party dot")
  if fb then
    local x, y = at(fb)
    pt(x, y, 65.5, 0, "far friend 300 yd east → rim minus inset 4.5 → (65.5, 0)")
    check(fb.arrow:IsShown() and fb.name:IsShown() and fb.name:GetText() == "Amie", "edge arrow + name")
  end
  local rb = pinOf("rare")
  if rb then local x, y = at(rb) pt(x, y, -15, 0, "rare 50 yd west → (-15, 0)") else check(false, "rare pin") end
  local db_ = pinOf("danger")
  check(db_ and db_.pin.label:find("Ogre%-test") and not pinOf("danger", "dg:2"), "only the elite zone near me / my path")
  if db_ then
    local x, y = at(db_)
    pt(x, y, 0, 15, "elite zone 50 yd north → (0, 15)")
    check(near(db_:GetWidth(), 24) and db_:IsMouseEnabled() == false, "soft ring ±40 yd = 24 px, never mouse-blocking")
  end
  local dr = pinOf("drop")
  if dr then local x, y = at(dr) pt(x, y, 0, -18, "quest loot 60 yd south → (0, -18)") else check(false, "quest loot pin (no Questie)") end
  local tx = pinOf("taxi")
  check(tx and tx.pin.key == "taxi:8" and not pinOf("taxi", "taxi:7"), "only the undiscovered flight master")
  if tx then local x, y = at(tx) pt(x, y, -45, 0, "flight master 150 yd west → (-45, 0)") end
  local sv = pinOf("svc_repair")
  check(sv and sv.pin.label:find("Forge%-test"), "repair vendor shown when repair is needed")
  if sv then local x, y = at(sv) pt(x, y, 60, 0, "repair 200 yd east → (60, 0)") end

  -- tooltip + one click = compass target
  if rb then
    GameTooltip.__lines = {}
    M.Script(rb, "OnEnter")
    local joined = table.concat(GameTooltip.__lines, "|")
    check(joined:find("Croc%-test") and joined:find("46 m"), "tooltip: name + distance: " .. joined)
    rb:Click()
    local cur = C.Current()
    check(cur and tostring(cur.label or cur.title):find("Croc%-test"), "click → compass target: " .. tostring(cur and (cur.label or cur.title)))
    C.SetWaypoint(0.5, 0.3, "Point test")
  end

  -- zoom in: target goes to the rim (it floats), the rare stays inside, elites outside disappear
  M.minimapZoom = 5
  MP.Update(true)
  tb = pinOf("target")
  if tb then local x, y = at(tb) pt(x, y, 0, 63, "zoom 5: target 200 yd → rim minus inset 7 → (0, 63)") check(tb.arrow:IsShown(), "target edge arrow") end
  check(not pinOf("taxi"), "non-floating pins out of range are hidden (flight master 150 yd at zoom 5)")
  M.minimapZoom = 0
  -- rotating minimap, facing west
  M.cvars.rotateMinimap, M.facing = "1", pi / 2
  MP.Update(true)
  tb = pinOf("target")
  if tb then local x, y = at(tb) pt(x, y, 60, 0, "rotating minimap facing west: target on the right (60, 0)") end
  M.cvars.rotateMinimap, M.facing = "0", 0
  -- indoor
  M.indoors = true
  MP.Update(true)
  rb = pinOf("rare")
  if rb then local x, y = at(rb) pt(x, y, -70 / 3, 0, "indoor zoom 0: rare 50 yd west → (-23.33, 0)") end
  M.indoors = false

  -- duplicate guide: Blizzard / TomTom / SPF already shows the same point → no target pin
  local oldOG = C.OtherGuide
  C.OtherGuide = function() return "blizzard" end
  MP.Update(true)
  check(not pinOf("target"), "target hidden when another guide shows the same point")
  C.OtherGuide = oldOG

  -- Questie loaded: drop sources are Questie's job
  rawset(_G, "QuestieLoader", {})
  MP.Update(true)
  check(MP.QuestieLoaded() and not pinOf("drop"), "Questie detected → no quest-loot layer")
  check(pinOf("target") ~= nil, "other layers stay with Questie")
  rawset(_G, "QuestieLoader", nil)

  -- layer toggles
  MP.db.rares = false
  MP:OnOptionChanged("rares", false)
  check(not pinOf("rare"), "layer toggle off hides the layer")
  MP.db.rares = true

  -- perf: ≤ 5 placements / s through the Minimap-child driver (LITE)
  MP.Update(true)
  local u0 = MP.updates
  M.Advance(2, 0.02)
  check(MP.updates - u0 <= 11 and MP.updates - u0 >= 5, "driver updates ≤ 5 Hz (" .. (MP.updates - u0) .. " in 2 s)")

  -- ------------------------------------------------------------- HereBeDragons-Pins path (stub)
  local added, removed = {}, {}
  local HBDPins = {
    AddMinimapIconMap = function(self, ref, icon, mapID, x, y, showParent, float)
      added[icon] = { ref = ref, map = mapID, x = x, y = y, float = float, pin = icon.pin }
    end,
    RemoveMinimapIcon = function(self, ref, icon) removed[#removed + 1] = icon added[icon] = nil end,
  }
  rawset(_G, "LibStub", setmetatable({}, { __call = function(_, name, silent)
    if name == "HereBeDragons-Pins-2.0" then return HBDPins end
    if not silent then error("no lib " .. tostring(name)) end
  end }))
  MP.Update(true)
  check(MP.engine == "hbd", "HereBeDragons-Pins found via LibStub → used")
  local tgt
  for icon, a in pairs(added) do if a.pin and a.pin.kind == "target" then tgt = a end end
  check(tgt and tgt.map == 1424 and near(tgt.x, 0.5) and near(tgt.y, 0.3) and tgt.float == true and tgt.ref == MP,
    "target → AddMinimapIconMap(MP, pin, 1424, 0.5, 0.3, floatOnEdge)")
  local rare
  for icon, a in pairs(added) do if a.pin and a.pin.kind == "rare" then rare = a end end
  check(rare and near(rare.x, 0.5 - 50 / 3000) and near(rare.y, 0.4) and rare.float == false, "world-only pin converted to map coords (rare)")
  C.ClearWaypoint()
  MP.Update(true)
  local still
  for icon, a in pairs(added) do if a.pin and a.pin.kind == "target" then still = true end end
  check(#removed >= 1 and not still, "pin gone → RemoveMinimapIcon")
  rawset(_G, "LibStub", nil)
  MP.Update(true)
  check(MP.engine == "own" and not next(MP.hbdShown), "lib gone → back to own engine, HBD icons removed")

  -- Minimap itself never touched
  local after = 0
  for _ in pairs(mm.__scripts) do after = after + 1 end
  check(after == mmScripts and #mm.__points == mmPoints and mm:GetWidth() == 140, "Minimap scripts / anchors / size untouched")

  -- instance / disabled → nothing shown
  T:SetModuleEnabled("MinimapPins", false)
  MP.Update(true)
  local any = false
  for _, b in ipairs(MP.pool) do if b:IsShown() then any = true end end
  check(not any, "disabled → every pin hidden")
  T:SetModuleEnabled("MinimapPins", true)
  check(MP.Status():find("Minimap"), "status line")

  C_Map.GetPlayerMapPosition = oldPos
  rawset(_G, "C_TaxiMap", nil)
  M.group = {}
  if Q then Q.lastList = oldList end
  T:ClearSuggestion("Inventory:inv_repair")
end
