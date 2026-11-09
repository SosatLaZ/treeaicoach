-- TreeAI Lite · Quêtes — action « Init » (WeakAuras). Lecture seule, rien dans le chat, 1 Hz max.
-- Idées reprises de TreeAI (Compass : géométrie monde, Planner : plus proche voisin + 2-opt).
local E = aura_env
local sqrt, atan2, floor, pi = math.sqrt, math.atan2, math.floor, math.pi

local function S(v) -- valeur secrète (famille Midnight) → nil
  local f = issecretvalue
  if v ~= nil and type(f) == "function" and f(v) then return nil end
  return v
end
E.S = S

local function world(mapID, x, y)
  local CM = C_Map
  if not (CM and CM.GetWorldPosFromMapPos and mapID and x and y and CreateVector2D) then return nil end
  local cont, w = CM.GetWorldPosFromMapPos(mapID, CreateVector2D(x, y))
  cont = S(cont)
  if not (cont and w and w.GetXY) then return nil end
  local wx, wy = w:GetXY()
  wx, wy = S(wx), S(wy)
  if not (wx and wy) then return nil end
  return cont, wx, wy
end

-- point {mapID,x,y} → point monde {c, n (nord), w (ouest)} (mis en cache)
local cache = {}
function E.W(p)
  if p.c then return p end
  local k = p.mapID .. ":" .. floor(p.x * 10000) .. ":" .. floor(p.y * 10000)
  local hit = cache[k]
  if hit == nil then
    local c, n, w = world(p.mapID, p.x, p.y)
    hit = c and { c = c, n = n, w = w } or false
    cache[k] = hit
  end
  if hit then p.c, p.n, p.w = hit.c, hit.n, hit.w end
  return hit and p or nil
end

function E.Dist(a, b)
  if not (a and b and a.c and a.c == b.c) then return nil end
  local dn, dw = b.n - a.n, b.w - a.w
  return sqrt(dn * dn + dw * dw), atan2(dw, dn)
end

function E.Player()
  if IsInInstance and S(IsInInstance()) then return nil end
  local CM = C_Map
  if not (CM and CM.GetBestMapForUnit and CM.GetPlayerMapPosition) then return nil end
  local m = S(CM.GetBestMapForUnit("player"))
  if not m then return nil end
  local pos = CM.GetPlayerMapPosition(m, "player")
  if not (pos and pos.GetXY) then return nil end
  local x, y = pos:GetXY()
  x, y = S(x), S(y)
  if not (x and y) or (x == 0 and y == 0) then return nil end
  local me = { mapID = m, x = x, y = y }
  local c, n, w = world(m, x, y)  -- jamais en cache : le joueur bouge
  if not c then return nil end
  me.c, me.n, me.w = c, n, w
  return me
end

local DIRS = { "devant", "devant à g.", "à gauche", "derrière à g.", "derrière", "derrière à d.", "à droite", "devant à d." }
function E.Dir(bearing)
  local f = GetPlayerFacing and S(GetPlayerFacing())
  if not (bearing and f) then return "" end
  local r = (bearing - f) % (2 * pi)
  return DIRS[floor((r + pi / 8) / (pi / 4)) % 8 + 1]
end

function E.Meters(yd)
  local m = yd * 0.9144
  if m >= 1000 then return (("%.1f km"):format(m / 1000):gsub("%.", ",")) end
  return floor(m + 0.5) .. " m"
end

-- quêtes du journal : { id, title, done, obj = premier objectif non fini, pt = {mapID,x,y} | nil }
function E.ReadLog()
  local QL = C_QuestLog
  local out = {}
  if not (QL and QL.GetNumQuestLogEntries and QL.GetInfo) then return out end
  local poi = {}
  local me = E.Player()
  if me and QL.GetQuestsOnMap then
    for _, q in ipairs(QL.GetQuestsOnMap(me.mapID) or {}) do
      if q.questID and q.x and q.y then poi[q.questID] = { mapID = me.mapID, x = q.x, y = q.y } end
    end
  end
  local n = S(QL.GetNumQuestLogEntries()) or 0
  for i = 1, math.min(n, 60) do
    local info = QL.GetInfo(i)
    local id = info and not info.isHeader and S(info.questID)
    if id then
      local q = { id = id, title = S(info.title) or ("Quête " .. id) }
      q.done = (QL.ReadyForTurnIn and QL.ReadyForTurnIn(id)) or (QL.IsComplete and QL.IsComplete(id)) or false
      if not q.done and QL.GetQuestObjectives then
        for _, o in ipairs(QL.GetQuestObjectives(id) or {}) do
          if not o.finished then q.obj = S(o.text) break end
        end
      end
      if QL.GetNextWaypoint then
        local m, x, y = QL.GetNextWaypoint(id)
        m, x, y = S(m), S(x), S(y)
        if m and x and y and (x > 0 or y > 0) then q.pt = { mapID = m, x = x, y = y } end
      end
      q.pt = q.pt or poi[id]
      out[#out + 1] = q
    end
  end
  return out
end

-- ordre de rendu : plus proche voisin depuis le joueur, puis 2-opt (chemin ouvert)
function E.Order(me, pts)
  local n = #pts
  local left, path, cur = {}, {}, me
  for i = 1, n do left[i] = pts[i] end
  while #left > 0 do
    local bi, bd = 1, math.huge
    for i, p in ipairs(left) do
      local d = E.Dist(cur, p) or math.huge
      if d < bd then bi, bd = i, d end
    end
    cur = table.remove(left, bi)
    path[#path + 1] = cur
  end
  local function d(a, b) return E.Dist(a, b) or 1e9 end
  local improved, guard = true, 0
  while improved and guard < 50 do
    improved, guard = false, guard + 1
    for i = 1, n - 1 do
      for k = i + 1, n do
        local a = (i == 1) and me or path[i - 1]
        local b, c, e = path[i], path[k], path[k + 1]
        local before = d(a, b) + (e and d(c, e) or 0)
        local after = d(a, c) + (e and d(b, e) or 0)
        if after < before - 0.01 then
          local lo, hi = i, k
          while lo < hi do path[lo], path[hi] = path[hi], path[lo]; lo, hi = lo + 1, hi - 1 end
          improved = true
        end
      end
    end
  end
  return path
end

function E.Build()
  if E.dirty or not E.log then E.log, E.dirty = E.ReadLog(), false end
  local me = E.Player()
  local lines, turnin = {}, {}
  local best, bd, bb
  for _, q in ipairs(E.log) do
    local p = q.pt and E.W(q.pt)
    if q.done then
      turnin[#turnin + 1] = { q = q, p = p }
    elseif p and me then
      local dist, bear = E.Dist(me, p)
      if dist and (not bd or dist < bd) then best, bd, bb = q, dist, bear end
    end
  end
  if best then
    lines[#lines + 1] = ("|cff7fd4ff>|r %s · %s · %s"):format(E.Dir(bb), E.Meters(bd), best.obj or best.title)
  end
  if #turnin > 0 then
    local known, unknown = {}, {}
    for _, t in ipairs(turnin) do
      if t.p and me and E.Dist(me, t.p) then t.p.q = t.q; known[#known + 1] = t.p else unknown[#unknown + 1] = t.q end
    end
    lines[#lines + 1] = ("|cff7fff7fÀ rendre (%d)|r"):format(#turnin)
    local prev = me
    for i, p in ipairs(me and E.Order(me, known) or {}) do
      if i > 5 then break end
      local dist = E.Dist(me, p)
      lines[#lines + 1] = (" %d. %s · %s"):format(i, p.q.title, dist and E.Meters(dist) or "?")
      prev = p
    end
    for i, q in ipairs(unknown) do
      if #lines > 7 then break end
      lines[#lines + 1] = "  · " .. q.title .. " (lieu inconnu)"
    end
  end
  return #lines > 0 and table.concat(lines, "\n") or nil
end

E.dirty = true
if not E.ticker and C_Timer and C_Timer.NewTicker then
  E.ticker = C_Timer.NewTicker(1, function() WeakAuras.ScanEvents("TREEAI_LITE_TICK") end)
end
