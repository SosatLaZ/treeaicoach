-- Scenario for Modules/PathDraw.lua (agent PATH), run by tests/wow/test_pathdraw.py on three API flavours.
-- Mocked 4-stop route (pick, objective, flight, turn-in) on map 1424 (3000 x 2000 yd) → world map overlay:
-- 3 dashed segments + 4 numbered badges (+ flight icon), on OUR frames only; minimap trail follows the player,
-- pooled textures reused (no leak), nothing drawn without a route, SPF hand-off keeps the world map only,
-- zero blocked actions in combat. Fake quests / names.
return function(M, ctx)
  local T = TreeAI
  local check = ctx.check
  local PD, R, P = T.PathDraw, T.Route, T.Planner
  check(PD and R and P and PD.db and PD.db.enabled, "PathDraw + Route + Planner loaded, PathDraw enabled")
  if not (PD and R and P) then return end
  local function near(a, b, eps) return a and b and math.abs(a - b) <= (eps or 1e-6) end
  M.LeaveCombat()

  -- ------------------------------------------------------------- pure geometry
  local t0, t1 = PD.Clip(0, 0, 200, 0, 50, false)
  check(near(t0, 0) and near(t1, 0.25), "round clip: player at centre, 200 px leg, radius 50 → [0, 0.25]")
  t0, t1 = PD.Clip(-100, 30, 100, 30, 50, true)
  check(near(t0, 0.25) and near(t1, 0.75), "square clip: crossing leg → [0.25, 0.75]")
  check(PD.Clip(-100, 80, 100, 80, 50, false) == nil, "leg outside the minimap → nothing")
  local px, py = PD.Project(100, 0, math.pi / 2, true, 0.3)
  check(near(px, 30, 1e-9) and near(py, 0, 1e-9), "rotating minimap facing west: north on the right")
  local opts = {}
  for _, o in ipairs(PD.options or {}) do opts[o.key] = o end
  check(opts.map and opts.minimap and opts.color and opts.color.type == "choice" and opts.width and opts.width.type == "range",
    "options: chemin carte / minimap, couleur, épaisseur")

  -- ------------------------------------------------------------- mocked world map (Blizzard frames, read only)
  local wm = CreateFrame("Frame", "WorldMapFrame", UIParent)
  local sc = CreateFrame("Frame", nil, wm)
  local child = CreateFrame("Frame", nil, sc)
  wm.ScrollContainer, sc.Child = sc, child
  wm.__shown = false
  function wm:GetMapID() return 1424 end
  function sc:GetLeft() return 100 end
  function sc:GetBottom() return 50 end
  function sc:GetWidth() return 1000 end
  function sc:GetHeight() return 667 end
  function child:GetLeft() return 100 end
  function child:GetBottom() return 50 end
  function child:GetWidth() return 1000 end
  function child:GetHeight() return 667 end
  local provider
  local hadMixins = rawget(_G, "CreateFromMixins") ~= nil
  function wm:AddDataProvider(p) provider = p end
  rawset(_G, "MapCanvasDataProviderMixin", { OnAdded = function() end })
  rawset(_G, "CreateFromMixins", function(...)
    local o = {}
    for i = 1, select("#", ...) do for k, v in pairs((select(i, ...))) do o[k] = v end end
    return o
  end)
  PD._mapHooked = nil
  check(PD.AttachMap() and not provider and PD.mapEngine == "canvas", "(4.2) world map: layer inside the map canvas, no data provider")
  local wmPoints, wmScripts = #wm.__points, 0
  for _ in pairs(wm.__scripts) do wmScripts = wmScripts + 1 end

  -- line objects: record start / end points on our own canvas
  local cv = PD.canvas
  check(cv and PD.view and cv == PD.view and PD.view:GetParent() == child and T.MapCanvas.InMap(cv), "(4.2) our layer = child of the map canvas (never on UIParent)")
  local mkLine = cv.CreateLine
  cv.CreateLine = function(self, ...)
    local l = mkLine(self, ...)
    function l:SetStartPoint(_, rel, x, y) self.sp = { rel, x, y } end
    function l:SetEndPoint(_, rel, x, y) self.ep = { rel, x, y } end
    function l:SetThickness(th) self.th = th end
    function l:SetColorTexture(r, g, b, a) self.rgba = { r, g, b, a } end
    return l
  end

  -- ------------------------------------------------------------- no route → nothing
  P.steps, P.cursor, P.route = nil, nil, nil
  wm:Show()
  M.Advance(0.5, 0.1)
  check(PD.badgesShown == 0 and PD.segments == 0, "no route: world map overlay empty")
  M.Advance(0.5, 0.2)
  check(PD.dotsShown == 0, "no route: no minimap trail")

  -- ------------------------------------------------------------- 4-stop route
  M.mapRects[1424] = M.mapRects[1424] or { top = 1000, left = 2000, w = 3000, h = 2000, continent = 0 }
  T.Geo.rects = {}
  R.db.hearth = false
  R.char.paused = false
  M.pos = { mapID = 1424, x = 0.45, y = 0.40 }
  M.facing, M.speed, M.minimapZoom, M.indoors = 0, 0, 0, false
  M.cvars.rotateMinimap = "0"
  local steps = {
    { k = "pick", q = 701, map = 1424, x = 48, y = 40, label = "Prendre « Sangliers »" },
    { k = "obj", q = 701, o = 1, map = 1424, x = 52, y = 42, label = "Tue « Sanglier »" },
    { k = "fly", map = 1424, x = 60, y = 50, label = "Vol pour Ailleurs" },
    { k = "turn", q = 701, map = 1424, x = 55, y = 45, label = "Rendre « Sangliers »" },
  }
  P.route = { id = "auto", title = "Plan auto (en jeu)", steps = steps, verdict = "ok" }
  P.steps, P.cursor, P.log = steps, 1, {}
  T:Fire("PLAN_BUILT")   -- (4.4 SMART) as the Planner does when it builds a route (no poll timing to rely on)
  local st = PD.Stops()
  check(st and #st == 4 and st[3].kind == "fly", "Route:Stops = 4 stops (3rd = flight): " .. tostring(st and #st))
  M.Advance(0.4, 0.1)
  check(PD.segments == 4 and PD.badgesShown == 4, ("world map: current leg + 3 segments + 4 badges (%d, %d)"):format(PD.segments, PD.badgesShown))
  check(PD._lines == true and PD.dashesShown >= 4, "segments drawn as dashed Line objects: " .. tostring(PD.dashesShown))
  local b = PD.badges
  local texts = {}
  for i = 1, 4 do texts[i] = b[i] and b[i].text:GetText() end
  check(table.concat(texts, ",") == "1,2,3,4", "badges numbered 1..4: " .. table.concat(texts, ","))
  local p1 = b[1] and b[1].bg.__points[1]
  check(p1 and p1[2] == cv and near(p1[4], 480, 1e-6) and near(p1[5], -0.40 * 667, 1e-6),
    "badge 1 at (48 %, 40 %) of the canvas: " .. tostring(p1 and p1[4]) .. ", " .. tostring(p1 and p1[5]))
  check(b[3].icon:IsShown() and tostring(b[3].icon:GetTexture()):find("Taxi") and not b[1].icon:IsShown(),
    "flight stop has a transport icon, quest stops do not")
  local d1 = PD.dashes[1] and PD.dashes[1].obj
  check(d1 and d1.sp and d1.sp[1] == cv and near(d1.sp[2], 450, 1e-6) and d1.th == 4 and d1.rgba and near(d1.rgba[2], 0.82),
    "(4.2) first dash = the current leg from the player (45 %), thicker, gold (« PNJ à voir »: stop 1 is a quest giver)")
  local dl = PD.dashes[PD.dashesShown] and PD.dashes[PD.dashesShown].obj
  check(dl and dl.th == 3 and dl.rgba and near(dl.rgba[2], 1), "later legs: thickness 3, the chosen colour")
  check(PD.view:IsVisible() and #wm.__points == wmPoints, "layer shown with the map; WorldMapFrame never anchored / moved")
  local n2 = 0
  for _ in pairs(wm.__scripts) do n2 = n2 + 1 end
  check(n2 == wmScripts, "no script set / hooked on WorldMapFrame")

  -- options : couleur / épaisseur / carte off
  PD.db.width, PD.db.color = 5, 2
  PD:OnOptionChanged()
  M.Advance(0.3, 0.1)
  dl = PD.dashes[PD.dashesShown] and PD.dashes[PD.dashesShown].obj
  check(dl and dl.th == 5 and near(dl.rgba[1], 1) and near(dl.rgba[2], 0.85) and d1.th == 6 and near(d1.rgba[2], 0.5),
    "option épaisseur 5 + couleur jaune applied (« PNJ à voir » turns orange next to yellow)")
  PD.db.map = false
  PD:OnOptionChanged()
  check(PD.badgesShown == 0 and PD.segments == 0, "« carte » off → nothing on the world map")
  PD.db.map, PD.db.width, PD.db.color = true, 3, 1
  PD:OnOptionChanged()
  check(PD.segments == 4, "back on")
  local nDash = #PD.dashes

  -- ------------------------------------------------------------- minimap trail
  wm.__shown = false
  check(not PD.view:IsVisible(), "map closed → layer hidden with it")
  M.Advance(0.6, 0.2)
  check(PD.dotsShown > 0 and PD.trail and PD.trail:GetParent() == Minimap, "minimap trail drawn on our child of Minimap: " .. PD.dotsShown)
  local mmPoints, mmScripts = #Minimap.__points, 0
  for _ in pairs(Minimap.__scripts) do mmScripts = mmScripts + 1 end
  local function firstDot() local d = PD.dots[1] local p = d and d.__points[1] return p and p[4], p and p[5] end
  local r0 = PD.mmRedraws
  M.Advance(2.2, 0.2)
  check(PD.mmRedraws == r0, "standing still → no redraw")
  local children = #PD.trail.__children
  local x0, y0 = firstDot()
  M.speed = 7
  for k = 1, 40 do
    M.pos = { mapID = 1424, x = 0.45 + k * 0.001, y = 0.40 + k * 0.0005 }
    M.Advance(0.2, 0.2)
  end
  local x1, y1 = firstDot()
  check(PD.mmRedraws > r0 + 30 and PD.mmRedraws <= r0 + 41, "walking → redraw ≤ 5 Hz: " .. (PD.mmRedraws - r0))
  check(x1 ~= x0 or y1 ~= y0, "trail moved with the player")
  check(#PD.trail.__children == children and #PD.dots <= 48, ("pooled textures reused (%d children, %d dots)"):format(#PD.trail.__children, #PD.dots))
  M.cvars.rotateMinimap = "1"
  M.facing = math.pi / 2
  M.Advance(0.4, 0.2)
  local x2, y2 = firstDot()
  check(x2 ~= x1 or y2 ~= y1, "rotating minimap → trail turns")
  M.cvars.rotateMinimap = "0"
  M.speed = 0
  check(#Minimap.__points == mmPoints, "Minimap itself never moved")
  local n3 = 0
  for _ in pairs(Minimap.__scripts) do n3 = n3 + 1 end
  check(n3 == mmScripts, "no script set on Minimap")

  -- combat : our frames only, nothing blocked
  M.EnterCombat()
  M.speed = 7
  for k = 1, 10 do M.pos = { mapID = 1424, x = 0.47 - k * 0.0004, y = 0.41 } M.Advance(0.2, 0.2) end
  wm:Show()
  M.Advance(0.4, 0.2)
  check(#M.blocked == 0, "combat: zero blocked actions (" .. #M.blocked .. ")")
  M.LeaveCombat()

  -- SPF guides: world map kept, minimap trail off unless the option says so
  -- (3.9.9) Core decides: SPF guides only when the player opted in (« Guidage : Suivi Forever ») and SPF is loaded
  local oldSPF, oldG = R.SPF, rawget(_G, "ShortestPathForever")
  R.SPF, R.following = function() return { NavigateRoute = function() end } end, true
  rawset(_G, "ShortestPathForever", { API = { NavigateRoute = function() end } })
  TreeAI:SetGuideMode("auto")
  M.Advance(1.2, 0.2)
  check(PD.dotsShown == 0 and PD.segments == 4, "SPF guiding: minimap trail off, world map still drawn")
  PD.db.minimapSPF = true
  PD:OnOptionChanged()
  M.Advance(0.4, 0.2)
  check(PD.dotsShown > 0, "option « garder la traînée » → trail back with SPF")
  PD.db.minimapSPF = false
  R.SPF, R.following = oldSPF, nil
  rawset(_G, "ShortestPathForever", oldG)
  TreeAI:SetGuideMode("treeai")

  -- route cleared → everything hidden, pool kept
  P.steps, P.cursor = nil, nil
  T:Fire("PLAN_BUILT")
  M.Advance(0.4, 0.2)
  local anyDot = false
  for _, d in ipairs(PD.dots) do if d:IsShown() then anyDot = true end end
  local anyBadge = false
  for _, bb in ipairs(PD.badges) do if bb.bg:IsShown() or bb.text:IsShown() then anyBadge = true end end
  check(PD.dotsShown == 0 and not anyDot and PD.badgesShown == 0 and not anyBadge, "no route → trail + badges hidden")
  check(#PD.dashes == nDash, "dash pool kept (no leak)")
  wm.__shown = false
  check(#M.blocked == 0, "zero blocked actions overall")
  check(PD.Status():find("^Chemin : "), "status: " .. PD.Status())
  -- client without Line objects → dotted textures instead of dashes
  cv.CreateLine = false
  PD._lines = nil
  for i = #PD.dashes, 1, -1 do PD.dashes[i].obj:Hide() PD.dashes[i] = nil end
  M.pos = { mapID = 1424, x = 0.45, y = 0.40 }
  local fresh = {}
  for i, s0 in ipairs(steps) do fresh[i] = { k = s0.k, q = s0.q, o = s0.o, map = s0.map, x = s0.x, y = s0.y, label = s0.label } end
  P.route.steps = fresh
  P.steps, P.cursor = fresh, 1
  wm:Show()
  M.Advance(0.4, 0.1)
  local dd = PD.dashes[1]
  check(PD.segments == 4 and PD._lines == false and dd and not dd.line and dd.obj:GetObjectType() == "Texture"
    and dd.obj.__points[1] and dd.obj.__points[1][2] == cv, "no CreateLine → 4 dotted segments with textures: " .. tostring(PD.segments)
    .. " " .. tostring(PD._lines) .. " " .. tostring(dd and dd.obj:GetObjectType()))
  wm.__shown = false
  P.steps, P.cursor = nil, nil
  rawset(_G, "MapCanvasDataProviderMixin", nil)
  if not hadMixins then rawset(_G, "CreateFromMixins", nil) end
end
