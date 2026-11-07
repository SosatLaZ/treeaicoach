-- Scenario for Modules/Radar.lua (agent RADAR), run on both API flavours by tests/wow/test_radar.py.
-- Mock world (wowmock): map 1424 = 3000 x 2000 yd, top (north) = 1000, left (west) = 2000, continent 0.
-- Player at map (0.5, 0.4) → world north 200, west 500. Fake names only.
return function(M, ctx)
  -- (3.9.2) the radar is off by default now: switch it on for its own scenario
  TreeAI:SetModuleEnabled("Radar", true)
  local T = TreeAI
  local R, C = T:GetModule("Radar"), T:GetModule("Compass")
  local check = ctx.check
  check(R and C and R.db and R.db.enabled, "Radar + Compass registered, Radar enabled")
  if not (R and C) then return end
  local pi = math.pi
  local function near(a, b, eps) return a and b and math.abs(a - b) <= (eps or 1e-6) end
  local function pt(px, py, x, y, msg) check(near(px, x) and near(py, y), msg .. (": %s, %s"):format(tostring(px), tostring(py))) end
  M.LeaveCombat()

  -- ------------------------------------------------------------- projection math (pure, exact numbers)
  local px, py, d, edge = R.Project(200, 0, 0, false, 400, 80)
  pt(px, py, 0, 40, "200 yd north at 400 yd zoom / 80 px → (0, 40)")
  check(near(d, 200) and edge == false, "distance 200, inside")
  px, py = R.Project(0, -300, 0, false, 400, 80)
  pt(px, py, 60, 0, "300 yd east (west = -300) → (60, 0)")
  px, py = R.Project(0, 100, 0, false, 200, 80)
  pt(px, py, -40, 0, "100 yd west at 200 yd zoom → (-40, 0)")
  px, py = R.Project(200, 0, pi / 2, false, 400, 80)
  pt(px, py, 0, 40, "north-up: facing west does not move the map")
  px, py = R.Project(200, 0, pi / 2, true, 400, 80)
  pt(px, py, 40, 0, "rotating map, facing west: north is on my right → (40, 0)")
  px, py = R.Project(0, 200, pi / 2, true, 400, 80)
  pt(px, py, 0, 40, "rotating map, facing west: a point west is straight ahead → (0, 40)")
  px, py = R.Project(-200, 0, pi, true, 400, 80)
  pt(px, py, 0, 40, "rotating map, facing south: a point south is ahead")
  px, py, d, edge = R.Project(1000, 0, 0, false, 400, 80)
  pt(px, py, 0, 80, "1000 yd north at 400 yd zoom → clamped on the rim (0, 80)")
  check(edge == true and near(d, 1000), "edge flag + real distance kept")
  px, py, d, edge = R.Project(300, -400, 0, false, 400, 80)
  pt(px, py, 64, 48, "500 yd NE (3-4-5) → rim, direction kept (64, 48)")
  check(edge and near(d, 500), "3-4-5 distance 500")
  check(R.ClampZoom(10) == 50 and R.ClampZoom(1000) == 400 and R.ClampZoom(150) == 150, "zoom clamp 50..400")

  -- world <-> map
  local c, n, w = R.World(1424, 0.5, 0.4)
  check(c == 0 and near(n, 200) and near(w, 500), "world of map (0.5, 0.4) = north 200, west 500")
  local mx, my = R.WorldToMap(1424, 0, 400, 500)
  check(near(mx, 0.5) and near(my, 0.3), "world → map inverse (0.5, 0.3)")
  check(R.WorldToMap(1424, 1, 400, 500) == nil, "other continent → nil")
  local me = R.Me()
  check(me and me.mapID == 1424 and near(me.n, 200) and near(me.w, 500) and me.c == 0, "player world position")

  -- clustering
  local cl = R.Cluster({ { c = 0, n = 0, w = 0 }, { c = 0, n = 10, w = 20 }, { c = 0, n = 300, w = 0 }, { c = 1, n = 0, w = 0 } }, 40)
  check(#cl == 3 and cl[1].count == 2 and near(cl[1].n, 5) and near(cl[1].w, 10), "cluster: 2 merged (centre 5,10), 2 apart")

  -- ------------------------------------------------------------- frame + live pins
  R.db.size, R.db.zoom, R.db.rotate = 180, 400, false  -- radius = 180/2 - 10 = 80 px
  -- the harness event storm clicks every button (menu rows included): reset what it may have toggled
  R.db.shown, R.db.locked, R.db.rings = true, false, true
  for _, l in ipairs(R.LAYERS) do R.db[l] = true end
  if R.menu then R.menu:Hide() end
  M.facing = 0
  R.Visibility()
  local F = R.frame
  check(F and F:IsShown(), "radar frame shown in the open world")
  check(F and F:GetName() == "TreeAIRadarFrame" and F:GetParent() == UIParent, "our own frame, child of UIParent")
  R.Layout()
  C.SetWaypoint(0.5, 0.3, "Point test")              -- 200 yd north
  M.group = { { name = "Amie", class = "MAGE", level = 26, online = true } }
  local oldPos = C_Map.GetPlayerMapPosition
  C_Map.GetPlayerMapPosition = function(id, u)
    if u == "party1" then return { x = 0.6, y = 0.4, GetXY = function(p) return p.x, p.y end } end  -- 300 yd east
    return oldPos(id, u)
  end
  rawset(_G, "C_TaxiMap", { GetTaxiNodesForMap = function(m)
    if m == 1424 then return { { nodeID = 7, name = "Moulin-de-test", position = { x = 0.5, y = 0.5 }, isUndiscovered = false, faction = 0 },
      { nodeID = 8, name = "Poste lointain", position = { x = 0.4, y = 0.4 }, isUndiscovered = true, faction = 0 } } end
    return {}
  end })
  R.slowAt = 0
  M.Advance(0.2)
  local function pinOf(kind, key)
    for _, b in ipairs(R.pinPool) do
      if b.pin and b:IsShown() and b.pin.kind == kind and (not key or b.pin.key == key) then return b end
    end
  end
  local function at(b) local p = b and b.__points[#b.__points] return p and p[4], p and p[5] end
  local tb = pinOf("target")
  check(tb ~= nil, "compass target pin drawn")
  if tb then local x, y = at(tb) pt(x, y, 0, 40, "target pin at (0, 40)") end
  local fb = pinOf("squad")
  check(fb and fb.pin.label == "Amie", "friend pin (live party position)")
  if fb then
    local x, y = at(fb)
    pt(x, y, 60, 0, "friend 300 yd east → (60, 0)")
    local col = fb.pin.color
    local ref = R.ClassRGB("MAGE")
    check(col and near(col[1], ref[1]) and near(col[3], ref[3]), "friend coloured by class (mage blue): " .. tostring(fb.pin.class) .. " " .. tostring(col and col[1]) .. "," .. tostring(col and col[3]))
  end
  local taxi = pinOf("taxi", "taxi:7")
  check(taxi and taxi.pin.known, "known flight master pin")
  if taxi then local x, y = at(taxi) pt(x, y, 0, -40, "flight master 200 yd south → (0, -40)") end
  local far = pinOf("taxi", "taxi:8")
  check(far and not far.pin.known, "undiscovered flight master shown (grey)")
  if far then local x, y = at(far) pt(x, y, -60, 0, "flight master 300 yd west → (-60, 0)") end
  -- tooltip on hover
  if fb then
    GameTooltip.__lines = {}
    M.Script(fb, "OnEnter")
    local joined = table.concat(GameTooltip.__lines, "|")
    check(joined:find("Amie") and joined:find("274 m") and joined:find("Mage"), "tooltip: name, distance, class: " .. joined)
  end

  -- zoom in : friend goes to the rim with an arrow
  R.SetZoom(200)
  R.lastSig = nil
  M.Advance(0.1)
  fb = pinOf("squad")
  if fb then
    local x, y = at(fb)
    local inset = (fb.size or 10) / 2 - 1
    pt(x, y, 80 - inset, 0, "friend out of range → rim, inset by its radius")
    check(fb.pin.edge and fb.edgeArrow:IsShown(), "rim arrow shown for the friend")
  end
  -- rotate: facing west → target (north) on the right
  R.db.rotate, M.facing = true, pi / 2
  R.SetZoom(400)
  M.Advance(0.1)
  tb = pinOf("target")
  if tb then local x, y = at(tb) pt(x, y, 40, 0, "rotating map, facing west → target on the right (40, 0)") end
  R.db.rotate, M.facing = false, 0

  -- buttons on the ring (mouse first): zoom − / +, lock, menu; right-click = menu
  check(R.controls and #R.controls == 4, "4 buttons on the ring")
  R.SetZoom(200)
  R.controls[2]:Click()
  check(near(R.db.zoom, 160), "+ button zooms in (200 → 160)")
  R.controls[1]:Click()
  check(near(R.db.zoom, 200), "- button zooms out (160 → 200)")
  R.lockBtn:Click()
  check(R.db.locked == true, "lock button locks")
  R.lockBtn:Click()
  check(R.db.locked == false, "lock button unlocks")
  M.Script(F, "OnMouseUp", "RightButton")
  check(R.menu and R.menu:IsShown(), "right-click opens the menu")
  local taxiRow
  for _, row in ipairs(R.menu.rows) do if row:IsShown() and row.item and row.item.layer == "taxi" then taxiRow = row end end
  check(taxiRow and taxiRow.text:GetText():find("%[x%]"), "menu row « Maîtres de vol » checked")
  if taxiRow then
    taxiRow:Click()
    check(R.db.taxi == false and taxiRow.text:GetText():find("%[ %]"), "menu click turns the layer off (box unchecked)")
    taxiRow:Click()
    check(R.db.taxi == true, "and back on")
  end
  R.controls[3]:Click()
  check(not R.menu:IsShown(), "menu button closes the menu")

  -- mouse wheel
  -- (smooth zoom: the wheel sets a goal, each frame eases towards it — settle() = a few frames)
  local function settle() for _ = 1, 40 do R.StepZoom() end end
  R.SetZoom(150)
  M.Wheel(F, 1)
  check(R.zoomGoal and near(R.zoomGoal, 120), "wheel up: zoom goal 150 → 120")
  settle()
  check(near(R.db.zoom, 120), "wheel up zooms in (150 → 120): " .. tostring(R.db.zoom))
  for _ = 1, 20 do M.Wheel(F, 1) end
  settle()
  check(R.db.zoom == 50, "zoom never below 50")
  for _ = 1, 20 do M.Wheel(F, -1) end
  settle()
  check(R.db.zoom == 400, "zoom never above 400")

  -- rares / elites / death
  M.units.nameplate3 = { name = "Croc-radar", classification = "rareelite", hostile = true, level = 28 }
  ctx.fire("NAME_PLATE_UNIT_ADDED", "nameplate3")
  check(#R.char.rares == 1 and R.char.rares[1].name == "Croc-radar", "rare recorded at the player's position")
  check(#R.char.dangers == 1, "rare elite also a danger zone")
  ctx.fire("NAME_PLATE_UNIT_ADDED", "nameplate3")
  check(#R.char.rares == 1 and R.char.rares[1].seen == 2, "same rare, same place → merged")
  ctx.fire("PLAYER_DEAD")
  check(R.char.death and near(R.char.death.n, 200), "death spot recorded")
  R.slowAt = 0
  M.Advance(0.1)
  check(pinOf("rare") and pinOf("death") and pinOf("danger"), "rare, death and danger pins drawn")
  M.player.dead = false
  ctx.fire("PLAYER_ALIVE")
  check(R.char.death == nil, "death spot cleared when alive")

  -- layer toggle
  R.db.taxi = false
  R.slowAt = 0
  M.Advance(0.1)
  check(not pinOf("taxi"), "taxi layer off → no flight master pin")
  R.db.taxi = true

  -- click a pin → compass target (world → map back conversion)
  M.pos = { mapID = 1424, x = 0.4, y = 0.4 }        -- move 300 yd west; the rare stays at (0.5, 0.4)
  R.slowAt = 0
  M.Advance(0.1)
  local rb = pinOf("rare")
  if rb then
    rb:Click()
    local t = C.Current()
    check(t and t.kind == "custom" and near(t.x, 0.5) and near(t.y, 0.4), "click on rare → compass waypoint at its map spot")
  else
    check(false, "rare pin present after moving")
  end
  M.pos = { mapID = 1424, x = 0.5, y = 0.4 }

  -- quest pins (Compass.QuestPoints stubbed: 3 spawns, 2 close together)
  local Q = T:GetModule("Quests")
  local oldQP, oldList = C.QuestPoints, Q.lastList
  C.QuestPoints = function(q)
    if q.id == 9901 then return { { mapID = 1424, x = 0.5, y = 0.35 }, { mapID = 1424, x = 0.505, y = 0.35 },
      { mapID = 1424, x = 0.7, y = 0.4 } }, "questie" end
    return { { mapID = 1424, x = 0.45, y = 0.4, name = "Garde-test" } }, "questie"
  end
  Q.lastList = { { id = 9901, title = "Peaux de test", obj = { { name = "Peau", have = 2, need = 6 } } },
    { id = 9902, title = "Retour test", complete = true, obj = {} } }
  R.RefreshQuests(R.Me(), true)
  local qp = R.questCache.pins
  local nq, nt, big = 0, 0, nil
  for _, p in ipairs(qp) do
    if p.kind == "quest" then nq = nq + 1 if p.count == 2 then big = p end end
    if p.kind == "turnin" then nt = nt + 1 end
  end
  check(nq == 2 and big and near(big.n, 300) and nt == 1, "quest spawns clustered (2 + 1) + 1 turn-in")
  C.QuestPoints, Q.lastList = oldQP, oldList
  R.questCache = { at = -1e9, pins = {} }

  -- performance: ~60 pins, full re-layout each time, plain Lua
  local many = {}
  for i = 1, 60 do many[i] = { key = "k" .. i, kind = "quest", layer = "quests", c = 0, n = 200 + i * 7, w = 500 - i * 5 } end
  R.pins, R.slowAt = many, M.now
  local t0 = os.clock()
  for _ = 1, 100 do R.lastSig = nil R.slowAt = M.now R.Update() end
  local ms = (os.clock() - t0) * 1000 / 100
  check(ms < 1, ("update with 60 pins ≤ 1 ms (%.3f ms)"):format(ms))
  check((R.shownPins or 0) == 60, "60 pins placed")

  -- instance: hidden, OnUpdate stops (frame hidden)
  M.instance = true
  R.Visibility()
  check(not F:IsShown() and R.hiddenWhy == "instance", "hidden in instances")
  M.instance = false
  R.Visibility()
  check(F:IsShown(), "back in the open world")

  -- slash
  M.RunSlash("/tai radar")
  check(not F:IsShown() and R.db.shown == false, "/tai radar hides")
  M.RunSlash("/tai radar")
  check(F:IsShown() and R.db.shown == true, "/tai radar shows")
  M.RunSlash("/tai radar zoom 90")
  check(R.db.zoom == 90, "/tai radar zoom 90")
  M.RunSlash("/tai radar statut")
  check(R.Status():find("Radar : actif"), "status line: " .. R.Status())

  C_Map.GetPlayerMapPosition = oldPos
  rawset(_G, "C_TaxiMap", nil)
  M.group = {}
  C.ClearWaypoint()
end
