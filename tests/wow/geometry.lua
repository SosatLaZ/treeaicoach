-- tests/wow/geometry.lua — PLACEBUG (4.0.1): the real client's frame geometry for the mock, OPT-IN
-- (`lua5.1 tests/wow/harness.lua --geometry [k=v,...] …`). The plain mock answers GetLeft() = 450 for every frame and
-- never resolves an anchor; with this engine the mock behaves like WoW for everything that places frames:
--  * anchors resolved like the client: 9 points, relative frames (object or name), offsets in the frame's own scale,
--    effective scale (UIParent × parents), two-point sizing, SetAllPoints, SetClampedToScreen, same point replaced;
--  * LOGIN like the real client (the 4.0.0 « everything jumps to a corner a second after login » report):
--    UIParent unsized (GetWidth / GetHeight 0, GetScreenWidth / GetScreenHeight 0) and every GetRect / GetLeft / GetTop
--    nil until the first frame drawn after PLAYER_ENTERING_WORLD; the UI scale (CVar) is applied AFTER login (UIParent
--    768 units high, then h units) with UI_SCALE_CHANGED + DISPLAY_SIZE_CHANGED; « Edit Mode » re-anchors the Blizzard
--    frames ~0.8 s after the world shows and the quest tracker grows when the quests arrive;
--  * StartMoving / StopMovingOrSizing like the client: the moved frame is re-anchored (nearest of the 9 points relative
--    to UIParent — opt move=topleft: the old « TOPLEFT relative to BOTTOMLEFT » style) and flagged user-placed;
--  * a fake Blizzard screen (minimap, objective tracker, chat, action bars, unit frames, buffs) + EnumerateFrames.
-- Options (k=v, comma separated): w, h = final UIParent size (1920×1080), login = UI scale before the CVar (1),
-- scaleAt = seconds after the first frame when the scale lands (0.3), editAt = Edit Mode layout (0.8),
-- move = nearest | topleft, blizzard = 1 | 0, ready = 1 → screen laid out from the start (no login race),
-- events = late (UI_SCALE_CHANGED + DISPLAY_SIZE_CHANGED when the scale lands) | early (fired at PLAYER_ENTERING_WORLD,
-- during the loading screen; the scale then lands silently), rectsAt = seconds after UIParent is sized during which the
-- other frames still answer nil (not laid out yet).
-- Test helpers (M.geo): Rect(f) / RawRect(f) (UIParent units, clamped / not), Drag(handle, dx, dy), SetScreen(w, h),
-- SetUIScale(s), Corner(f), OnScreen(f), Log (SetPoint / ClearAllPoints on TreeAI frames, with time), Ours().
return function(M, spec)
  local W = assert(M.Widget, "wowmock.lua: M.Widget missing")
  local opts = { w = 1920, h = 1080, login = 1, scaleAt = 0.3, editAt = 0.8, move = "nearest", blizzard = "1", ready = "0",
    events = "late", rectsAt = 0 }
  for k, v in tostring(spec or ""):gmatch("([%w_]+)=([^,]+)") do opts[k] = tonumber(v) or v end
  local geo = { opts = opts, t = 0, ready = tostring(opts.ready) == "1", pew = false, Log = {}, moving = nil }
  M.geo = geo
  local UIP = _G.UIParent
  -- screen in « abs » units (effective scale 1: 768 high), UIParent scale s → UIParent = abs / s
  local aspect = opts.w / opts.h
  geo.absW, geo.absH = 768 * aspect, 768
  geo.finalScale = 768 / opts.h
  geo.scale = geo.ready and geo.finalScale or (tonumber(opts.login) or 1)
  geo.scalePending = not geo.ready
  geo.editPending = tostring(opts.blizzard) == "1"

  local HX = { TOPLEFT = 0, LEFT = 0, BOTTOMLEFT = 0, TOP = 0.5, CENTER = 0.5, BOTTOM = 0.5, TOPRIGHT = 1, RIGHT = 1, BOTTOMRIGHT = 1 }
  local VY = { TOPLEFT = 1, TOP = 1, TOPRIGHT = 1, LEFT = 0.5, CENTER = 0.5, RIGHT = 0.5, BOTTOMLEFT = 0, BOTTOM = 0, BOTTOMRIGHT = 0 }
  geo.HX, geo.VY = HX, VY

  local function parentOf(f) local p = rawget(f, "__parent") return p end
  local function effScale(f, depth)
    depth = depth or 0
    if f == UIP then return geo.scale end
    if type(f) ~= "table" or depth > 30 then return geo.scale end
    local p = parentOf(f)
    local own = rawget(f, "__scale") or 1
    if not p then return own end
    return own * effScale(p, depth + 1)
  end
  geo.EffScale = effScale
  local function isOurs(f)
    local n = type(f) == "table" and rawget(f, "__name")
    return type(n) == "string" and n:sub(1, 6) == "TreeAI"
  end
  local function log(f, what, ...)
    if isOurs(f) then geo.Log[#geo.Log + 1] = { t = geo.t, now = M.now, frame = rawget(f, "__name"), what = what, args = { ... } } end
  end

  -- point normalisation like the client: (point[, relativeTo][, relativePoint][, x, y])
  local function normalize(self, a1, a2, a3, a4, a5)
    local point = type(a1) == "string" and a1:upper() or a1
    if not HX[point] then error("SetPoint: invalid point " .. tostring(a1), 3) end
    local rel, relPoint, x, y
    if type(a2) == "number" then rel, relPoint, x, y = nil, point, a2, a3
    else
      rel = a2
      if type(a3) == "number" then relPoint, x, y = point, a3, a4
      else relPoint, x, y = a3, a4, a5 end
    end
    if type(rel) == "string" then rel = rawget(_G, rel) end
    if rel == nil then rel = parentOf(self) or UIP end
    relPoint = type(relPoint) == "string" and relPoint:upper() or point
    if not HX[relPoint] then error("SetPoint: invalid relative point " .. tostring(relPoint), 3) end
    if x ~= nil and type(x) ~= "number" then error("SetPoint: bad x offset", 3) end
    if y ~= nil and type(y) ~= "number" then error("SetPoint: bad y offset", 3) end
    x, y = x or 0, y or 0
    if x ~= x or y ~= y or math.abs(x) == math.huge or math.abs(y) == math.huge then error("SetPoint: offset is not finite", 3) end
    return point, rel, relPoint, x, y
  end

  local prevSetPoint, prevClear, prevSetAll = W.SetPoint, W.ClearAllPoints, W.SetAllPoints
  function W:SetPoint(...)
    local p, rel, rp, x, y = normalize(self, ...)
    local before = #self.__points
    prevSetPoint(self, p, rel, rp, x, y)                       -- the taint wrapper (combat / protected) still applies
    if #self.__points > before then
      local new = self.__points[#self.__points]
      for i = #self.__points - 1, 1, -1 do                     -- the client: the same point again replaces it
        if self.__points[i][1] == p then table.remove(self.__points, i) end
      end
      if new then log(self, "SetPoint", p, rel and rawget(rel, "__name"), rp, x, y) end
    end
  end
  function W:ClearAllPoints(...)
    local n = #self.__points
    prevClear(self, ...)
    if n > 0 and #self.__points == 0 then log(self, "ClearAllPoints") end
  end
  function W:SetAllPoints(rel, ...)
    prevSetAll(self, rel, ...)
    if M.inCombat and M.strictTaint then return end
    if type(rel) == "string" then rel = rawget(_G, rel) end
    if type(rel) ~= "table" then rel = parentOf(self) or UIP end
    self.__points = { { "TOPLEFT", rel, "TOPLEFT", 0, 0 }, { "BOTTOMRIGHT", rel, "BOTTOMRIGHT", 0, 0 } }
  end
  function W:GetPoint(i)
    local p = self.__points[i or 1]
    if p then return p[1], p[2], p[3], p[4], p[5] end
    return nil
  end
  function W:SetClampedToScreen(v) self.__clamped = v and true or false end

  -- rectangle in abs units (left, bottom, width, height) or nil (not laid out / no anchor / bad chain)
  local function absRect(f, depth, raw)
    if not geo.ready then return nil end
    if f == UIP then return 0, 0, geo.absW, geo.absH end
    if geo.rectsUntil and geo.t < geo.rectsUntil then return nil end
    depth = depth or 0
    if type(f) ~= "table" or depth > 25 then return nil end
    local pts = rawget(f, "__points")
    if not pts or #pts == 0 then return nil end
    local es = effScale(f)
    local L, R, CX, T, B, CY
    for _, p in ipairs(pts) do
      local rl, rb, rw, rh = absRect(p[2], depth + 1)
      if not rl then return nil end
      local ax = rl + rw * HX[p[3]] + (p[4] or 0) * es
      local ay = rb + rh * VY[p[3]] + (p[5] or 0) * es
      local hx, vy = HX[p[1]], VY[p[1]]
      if hx == 0 then L = ax elseif hx == 1 then R = ax else CX = ax end
      if vy == 1 then T = ay elseif vy == 0 then B = ay else CY = ay end
    end
    local w, h = (rawget(f, "__w") or 0) * es, (rawget(f, "__h") or 0) * es
    local left, width, bottom, height
    if L and R then left, width = L, R - L elseif L then left, width = L, w
    elseif R then left, width = R - w, w elseif CX then left, width = CX - w / 2, w else return nil end
    if T and B then bottom, height = B, T - B elseif B then bottom, height = B, h
    elseif T then bottom, height = T - h, h elseif CY then bottom, height = CY - h / 2, h else return nil end
    if rawget(f, "__clamped") and not raw then
      if left < 0 then left = 0 elseif left + width > geo.absW then left = math.max(0, geo.absW - width) end
      if bottom < 0 then bottom = 0 elseif bottom + height > geo.absH then bottom = math.max(0, geo.absH - height) end
    end
    return left, bottom, width, height
  end
  geo.AbsRect = absRect
  local function twoH(f) local h, n = {}, 0 for _, p in ipairs(f.__points) do local x = HX[p[1]] if not h[x] then h[x] = true n = n + 1 end end return n >= 2 end
  local function twoV(f) local h, n = {}, 0 for _, p in ipairs(f.__points) do local y = VY[p[1]] if not h[y] then h[y] = true n = n + 1 end end return n >= 2 end

  function W:GetEffectiveScale() return effScale(self) end
  function W:IsRectValid() return absRect(self) ~= nil end
  function W:GetRect()
    local l, b, w, h = absRect(self)
    if not l then return nil end
    local es = effScale(self)
    return l / es, b / es, w / es, h / es
  end
  function W:GetLeft() local l = absRect(self) if l then return l / effScale(self) end return nil end
  function W:GetBottom() local _, b = absRect(self) if b then return b / effScale(self) end return nil end
  function W:GetRight() local l, _, w = absRect(self) if l then return (l + w) / effScale(self) end return nil end
  function W:GetTop() local _, b, _, h = absRect(self) if b then return (b + h) / effScale(self) end return nil end
  function W:GetCenter()
    local l, b, w, h = absRect(self)
    if not l then return nil end
    local es = effScale(self)
    return (l + w / 2) / es, (b + h / 2) / es
  end
  local baseW, baseH = W.GetWidth, W.GetHeight
  function W:GetWidth()
    if self == UIP then return geo.ready and geo.absW / geo.scale or 0 end
    if twoH(self) then local l, _, w = absRect(self) if l then return w / effScale(self) end end
    return baseW(self)
  end
  function W:GetHeight()
    if self == UIP then return geo.ready and geo.absH / geo.scale or 0 end
    if twoV(self) then local _, b, _, h = absRect(self) if b then return h / effScale(self) end end
    return baseH(self)
  end
  function W:GetSize() return self:GetWidth(), self:GetHeight() end
  local baseSetSize = W.SetSize
  function W:SetSize(w, h)
    if self == UIP then geo.SetScreen(w, h) return end
    return baseSetSize(self, w, h)
  end
  -- moving like the client
  function W:SetMovable(v) self.__movable = v and true or false end
  function W:IsMovable() return self.__movable or false end
  function W:SetUserPlaced(v) self.__userPlaced = v and true or false end
  function W:IsUserPlaced() return self.__userPlaced or false end
  function W:StartMoving()
    if not self.__movable then return end
    self.__isMoving = true
    geo.moving = self
  end
  function W:StopMovingOrSizing()
    if not self.__isMoving then return end
    self.__isMoving = false
    if geo.moving == self then geo.moving = nil end
    local l, b, w, h = absRect(self)
    if not l then return end
    local es = effScale(self)
    if opts.move == "topleft" then
      self.__points = { { "TOPLEFT", UIP, "BOTTOMLEFT", l / es, (b + h) / es } }
    else
      local cx, cy = l + w / 2, b + h / 2
      local hz = (cx < geo.absW / 3 and "LEFT") or (cx > geo.absW * 2 / 3 and "RIGHT") or ""
      local vt = (cy > geo.absH * 2 / 3 and "TOP") or (cy < geo.absH / 3 and "BOTTOM") or ""
      local pt = vt .. hz
      if pt == "" then pt = "CENTER" end
      local x = (hz == "LEFT" and l) or (hz == "RIGHT" and (l + w - geo.absW)) or (cx - geo.absW / 2)
      local y = (vt == "TOP" and (b + h - geo.absH)) or (vt == "BOTTOM" and b) or (cy - geo.absH / 2)
      self.__points = { { pt, UIP, pt, x / es, y / es } }
    end
    self.__userPlaced = true
    log(self, "StopMovingOrSizing")
  end

  -- screen API
  _G.GetScreenWidth = function() return geo.ready and geo.absW / geo.scale or 0 end
  _G.GetScreenHeight = function() return geo.ready and geo.absH / geo.scale or 0 end
  _G.GetPhysicalScreenSize = function() return opts.w, opts.h end

  -- helpers --------------------------------------------------------------------------------------------------
  --- rectangle in UIParent units (left, bottom, width, height), clamped like the client draws it
  function geo.Rect(f, raw)
    local l, b, w, h = absRect(f, 0, raw)
    if not l then return nil end
    local s = geo.scale
    return { l = l / s, b = b / s, w = w / s, h = h / s }
  end
  function geo.RawRect(f) return geo.Rect(f, true) end
  function geo.Screen() return geo.absW / geo.scale, geo.absH / geo.scale end
  --- on screen: the unclamped rectangle is (almost) entirely inside UIParent
  function geo.OnScreen(f, tol)
    local r = geo.RawRect(f)
    if not r then return false end
    local SW, SH = geo.Screen()
    tol = tol or 2
    return r.l >= -tol and r.b >= -tol and r.l + r.w <= SW + tol and r.b + r.h <= SH + tol
  end
  --- the frame touches two perpendicular screen edges (a corner) — the 4.0.0 symptom; returns the corner name
  function geo.Corner(f, tol)
    local r = geo.Rect(f)
    if not r then return nil end
    local SW, SH = geo.Screen()
    tol = tol or 3
    local L, R = r.l <= tol, r.l + r.w >= SW - tol
    local B, T = r.b <= tol, r.b + r.h >= SH - tol
    if (L or R) and (B or T) then return (T and "TOP" or "BOTTOM") .. (L and "LEFT" or "RIGHT") end
    return nil
  end
  --- our named top-level frames (children of UIParent), visible or not
  function geo.Ours()
    local out = {}
    for i = 1, #M.frames do
      local f = M.frames[i]
      if isOurs(f) and rawget(f, "__parent") == UIP then out[#out + 1] = f end
    end
    return out
  end
  --- the player drags `handle` (the frame itself or its header / grip) by dx, dy UIParent units, like the mouse
  function geo.Drag(handle, dx, dy)
    geo.moving = nil
    local s0 = handle.__scripts.OnDragStart or handle.__scripts.OnMouseDown
    if s0 then M.SafeCall("OnDragStart", s0, handle, "LeftButton") end
    local mv = geo.moving
    if mv then
      local l, b = absRect(mv)
      if l then
        local es = effScale(mv)
        local s = geo.scale
        mv.__points = { { "BOTTOMLEFT", UIP, "BOTTOMLEFT", (l + dx * s) / es, (b + dy * s) / es } }
      end
    end
    local s1 = handle.__scripts.OnDragStop or handle.__scripts.OnMouseUp
    if s1 then M.SafeCall("OnDragStop", s1, handle) end
    return mv
  end
  --- resolution change (fullscreen ↔ windowed, new monitor): UIParent = w × h units (UI scale auto)
  function geo.SetScreen(w, h, quiet)
    w, h = tonumber(w) or opts.w, tonumber(h) or opts.h
    local s0 = geo.scale
    geo.absW, geo.absH = 768 * w / h, 768
    geo.finalScale = 768 / h
    geo.scale = geo.finalScale
    if not quiet and geo.ready then
      if math.abs(s0 - geo.scale) > 1e-6 then M.FireEvent("UI_SCALE_CHANGED") end
      M.FireEvent("DISPLAY_SIZE_CHANGED")
    end
  end
  --- UI scale slider (same monitor): UIParent = abs / s
  function geo.SetUIScale(s)
    geo.scale = tonumber(s) or geo.scale
    if geo.ready then M.FireEvent("UI_SCALE_CHANGED") end
  end

  -- fake Blizzard screen: anchored to UIParent edges like the default UI (nothing valid before the first frame)
  geo.blizzard = {}
  local function blizz(name, kind, parent, w, h, ...)
    local f = rawget(_G, name)
    if type(f) ~= "table" then f = _G.CreateFrame(kind or "Frame", name, parent or UIP) end
    f.__parent = parent or UIP
    f.__w, f.__h = w, h
    f.__points = {}
    if select("#", ...) > 0 then f.__points[1] = { ... } end
    geo.blizzard[#geo.blizzard + 1] = f
    return f
  end
  if tostring(opts.blizzard) == "1" then
    local cl = blizz("MinimapCluster", "Frame", UIP, 192, 192, "TOPRIGHT", UIP, "TOPRIGHT", 0, 0)
    local mm = rawget(_G, "Minimap")
    if type(mm) == "table" then mm.__parent = cl; mm.__w, mm.__h = 140, 140; mm.__points = { { "CENTER", cl, "CENTER", 9, -10 } } end
    blizz("BuffFrame", "Frame", UIP, 300, 90, "TOPRIGHT", UIP, "TOPRIGHT", -205, -13)
    blizz("ObjectiveTrackerFrame", "Frame", UIP, 248, 140, "TOPRIGHT", UIP, "TOPRIGHT", -85, -230)
    local chat = rawget(_G, "ChatFrame1")
    if type(chat) == "table" then chat.__parent = UIP; chat.__w, chat.__h = 430, 180; chat.__points = { { "BOTTOMLEFT", UIP, "BOTTOMLEFT", 32, 95 } } end
    blizz("MainMenuBar", "Frame", UIP, 1024, 53, "BOTTOM", UIP, "BOTTOM", 0, 0)
    blizz("MultiBarBottomLeft", "Frame", UIP, 500, 42, "BOTTOM", UIP, "BOTTOM", -6, 60)
    blizz("MultiBarRight", "Frame", UIP, 42, 500, "RIGHT", UIP, "RIGHT", -2, -40)
    blizz("PlayerFrame", "Frame", UIP, 232, 100, "TOPLEFT", UIP, "TOPLEFT", -19, -4)
    local tf = blizz("TargetFrame", "Frame", UIP, 232, 100, "TOPLEFT", UIP, "TOPLEFT", 250, -4)
    tf.__shown = false
    blizz("CastingBarFrame", "Frame", UIP, 195, 13, "BOTTOM", UIP, "BOTTOM", 0, 140)
  end
  -- « Edit Mode » applies the saved layout a moment after the world shows; the quest list arrives (tracker grows)
  local function applyEditMode()
    local ot = rawget(_G, "ObjectiveTrackerFrame")
    if type(ot) == "table" then ot.__h = 520; ot.__points = { { "TOPRIGHT", UIP, "TOPRIGHT", -60, -215 } } end
    local mb = rawget(_G, "MainMenuBar")
    if type(mb) == "table" then mb.__points = { { "BOTTOM", UIP, "BOTTOM", 0, 4 } } end
    if M.FireEvent then pcall(M.FireEvent, "EDIT_MODE_LAYOUTS_UPDATED") end
  end
  geo.ApplyEditMode = applyEditMode

  -- EnumerateFrames (the real client has it; the plain mock does not): Blizzard frames + every CreateFrame
  local enum, enumIdx = {}, {}
  _G.EnumerateFrames = function(f)
    if f == nil then
      enum, enumIdx = {}, {}
      local seen = {}
      local function add(x) if type(x) == "table" and not seen[x] then seen[x] = true enum[#enum + 1] = x enumIdx[x] = #enum end end
      for _, n in ipairs({ "Minimap", "ChatFrame1" }) do add(rawget(_G, n)) end
      for i = 1, #M.frames do add(M.frames[i]) end
      return enum[1]
    end
    local i = enumIdx[f]
    return i and enum[i + 1] or nil
  end

  -- login: the screen is laid out at the first frame after PLAYER_ENTERING_WORLD, the UI scale lands a bit later
  local fireRaw = M.FireEventRaw
  M.FireEventRaw = function(event, ...)
    if event == "PLAYER_ENTERING_WORLD" and not geo.pew then
      geo.pew = true
      if opts.events == "early" and geo.scalePending then
        local r = fireRaw(event, ...)
        fireRaw("UI_SCALE_CHANGED")
        fireRaw("DISPLAY_SIZE_CHANGED")
        return r
      end
    end
    return fireRaw(event, ...)
  end
  local prevTick = M.GeoTick
  M.GeoTick = function(dt)
    if prevTick then prevTick(dt) end
    geo.t = geo.t + (dt or 0)
    if geo.pew and not geo.ready then geo.ready = true end
    if geo.ready and not geo.readyAt then
      geo.readyAt = geo.t
      if (tonumber(opts.rectsAt) or 0) > 0 then geo.rectsUntil = geo.t + tonumber(opts.rectsAt) end
    end
    if geo.ready and geo.scalePending and geo.t >= geo.readyAt + (tonumber(opts.scaleAt) or 0.3) then
      geo.scalePending = false
      geo.scale = geo.finalScale
      if opts.events ~= "early" then
        M.FireEvent("UI_SCALE_CHANGED")
        M.FireEvent("DISPLAY_SIZE_CHANGED")
      end
    end
    if geo.ready and geo.editPending and geo.t >= geo.readyAt + (tonumber(opts.editAt) or 0.8) then
      geo.editPending = false
      applyEditMode()
    end
  end
  return geo
end
