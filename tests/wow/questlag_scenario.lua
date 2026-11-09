-- Harness scenario (4.3.6, agent V5H item 5): « lag quand j'accepte une quête / quand le suivi change ». Hunter 30 in
-- a group of 4 with a full-ish log; accepting a quest (QUEST_ACCEPTED + QUEST_LOG_UPDATE burst + the watch the game
-- adds) and (un)tracking a quest must never cost one frame more than 8 ms of TreeAI work (os.clock of the harness:
-- the event's own frame, then every frame for 6 s). Run by tests/wow/test_questlag.py.
return function(M, ctx)
  local check = ctx.check
  local function out(s) io.write("QUESTLAG ", s, "\n") end
  M.LeaveCombat()
  M.player.level = 30
  local qs = { { header = "Les Tarides" } }
  for i = 1, 18 do
    qs[#qs + 1] = { id = 9100 + i, title = "Quête de test " .. i, level = 25 + (i % 6),
      objectives = { { text = "Objet : 1/8", done = false, have = 1, need = 8 } } }
  end
  M.quests = qs
  M.group = {
    { name = "Amie", class = "PRIEST", level = 29, online = true },
    { name = "Compagnon", class = "WARRIOR", level = 30, online = true },
    { name = "Camarade", class = "MAGE", level = 29, online = true },
  }
  ctx.fire("GROUP_ROSTER_UPDATE")
  ctx.fire("QUEST_LOG_UPDATE")
  M.Advance(20, 0.05)

  local worst, frames, worstTop = 0, 0, ""
  local t0, snap
  local T = TreeAI
  local function copy() local c = {} for k, v in pairs(T._stats or {}) do c[k] = v.ms end return c end
  local function top(before)
    local l = {}
    for k, v in pairs(T._stats or {}) do local d = v.ms - (before[k] or 0) if d > 0.2 then l[#l + 1] = { k, d } end end
    table.sort(l, function(a, b) return a[2] > b[2] end)
    local o = {}
    for i = 1, math.min(4, #l) do o[#o + 1] = ("%s %.1f"):format(l[i][1], l[i][2]) end
    return table.concat(o, ", ")
  end
  M.FrameHook = function(start)
    if start then t0 = os.clock() snap = copy() return end
    local ms = (os.clock() - t0) * 1000
    frames = frames + 1
    if ms > worst then worst = ms worstTop = top(snap) end
  end
  local function measure(tag, fn)
    worst, worstTop = 0, ""
    collectgarbage("collect") collectgarbage("stop")   -- the mock's own garbage must not land in a measured frame
    local before = copy()
    local s = os.clock()
    fn()
    local ev = (os.clock() - s) * 1000
    M.Advance(6, 1 / 60)
    collectgarbage("restart")
    if ev > worst then worstTop = top(before) end
    local w = math.max(worst, ev)
    out(("%s : trame de l'événement %.2f ms, pire trame %.2f ms [%s]"):format(tag, ev, w, worstTop))
    check(w < 8, ("%s : pire trame %.2f ms < 8 ms"):format(tag, w))
  end
  measure("accepter une quête", function()
    local q = { id = 9200, title = "Nouvelle quête de test", level = 29,
      objectives = { { text = "Croc : 0/6", done = false, have = 0, need = 6 } } }
    table.insert(M.quests, q)
    ctx.fire("QUEST_ACCEPTED", #M.quests - 1, 9200)
    ctx.fire("QUEST_LOG_UPDATE")
    ctx.fire("UNIT_QUEST_LOG_CHANGED", "player")
    ctx.fire("QUEST_LOG_UPDATE")
    if M.WatchQuest then M.WatchQuest(9200, true, 25) end
    ctx.fire("QUEST_LOG_UPDATE")
  end)
  measure("suivre / ne plus suivre", function()
    if M.WatchQuest then M.WatchQuest(9105, true, 25) M.WatchQuest(9101, false, 25) end
    ctx.fire("QUEST_LOG_UPDATE")
  end)
  -- objective progress (4/8 → 5/8): texts only; objective filled (8/8): may replan, sliced
  measure("objectif +1", function()
    local o = M.quests[2].objectives[1]
    o.have = o.have + 1 o.text = ("Objet : %d/8"):format(o.have)
    ctx.fire("UNIT_QUEST_LOG_CHANGED", "player")
    ctx.fire("QUEST_LOG_UPDATE")
    ctx.fire("QUEST_WATCH_UPDATE", 1)
    ctx.fire("QUEST_LOG_UPDATE")
  end)
  measure("objectif rempli", function()
    local q = M.quests[3]
    local o = q.objectives[1]
    o.have, o.done, o.text = 8, true, "Objet : 8/8"
    q.complete = true
    ctx.fire("UNIT_QUEST_LOG_CHANGED", "player")
    ctx.fire("QUEST_LOG_UPDATE")
    ctx.fire("QUEST_WATCH_UPDATE", 2)
    ctx.fire("QUEST_LOG_UPDATE")
  end)
  -- « reste à tuer » on a quest mob's nameplate: right after each kill (cheap text update), gone once done
  local H = T.Highlight or T.modules.Highlight
  if H and H.Plates and H._active then
    local q = { id = 9300, title = "Les loups de test", level = 29,
      objectives = { { text = "Loup gris tué : 3/6", done = false, have = 3, need = 6 } } }
    table.insert(M.quests, q)
    ctx.fire("QUEST_ACCEPTED", 9300) ctx.fire("QUEST_LOG_UPDATE")
    M.Advance(4, 0.05)
    local pf = {}
    rawset(_G, "C_NamePlate", { GetNamePlateForUnit = function(u)
      if not M.units[u] then return nil end
      pf[u] = pf[u] or CreateFrame("Frame", nil, UIParent) return pf[u] end })
    M.units.nameplate1 = { name = "Loup gris", guid = "Creature-0-0-0-0-3101-1", hostile = true, level = 28 }
    ctx.fire("NAME_PLATE_UNIT_ADDED", "nameplate1")
    local function plate() local pl = H.Plates() local f = pl.nameplate1 return f and f:IsShown() and f.text:GetText() or nil end
    check(plate() == "3", "barre de nom : reste 3 (" .. tostring(plate()) .. ")")
    local o = q.objectives[1]
    for _, left in ipairs({ 2, 1 }) do
      o.have = 6 - left o.text = ("Loup gris tué : %d/6"):format(o.have)
      measure("monstre tué (reste " .. left .. ")", function()
        ctx.fire("UNIT_QUEST_LOG_CHANGED", "player") ctx.fire("QUEST_WATCH_UPDATE", 1) ctx.fire("QUEST_LOG_UPDATE")
      end)
      check(plate() == tostring(left), ("barre de nom à jour : reste %d (%s)"):format(left, tostring(plate())))
      check((H.countsCost or 0) < 1 or os.getenv("TREEAI_REALCLOCK") ~= "1",
        ("mise à jour des nombres %.2f ms < 1 ms"):format(H.countsCost or 0))
    end
    o.have, o.done, o.text = 6, true, "Loup gris tué : 6/6"
    q.complete = true
    measure("objectif des loups rempli", function()
      ctx.fire("UNIT_QUEST_LOG_CHANGED", "player") ctx.fire("QUEST_LOG_UPDATE")
    end)
    check(plate() == nil, "objectif rempli : plus de marque (" .. tostring(plate()) .. ")")
    out(("barre de nom : mise à jour %.2f ms"):format(H.countsCost or 0))
  end
  M.FrameHook = nil
end
