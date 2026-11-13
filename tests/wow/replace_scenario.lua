-- Scenario for the « sans autres addons » round (agent REPLACE, 4.0.1), run by tests/wow/test_replace.py on the modern
-- and forever flavours: QuestPins (map + minimap quest icons from a fake QuestieDB read at run time + the real log),
-- Watch « Suivi TreeAI » (route quests, live distance / direction / time, Blizzard tracker taken over taint-safely),
-- Addons (« Mes addons » page, Questie silenced through ITS options, SPF tracker), QoL (grey selling / repair without
-- doubling Quick, instant loot, camera), Meter (C_DamageMeter read after combat only). Perf: count hook on QuestPins
-- (idle = 0, moving / map open ≤ 40 kinstr/min). Fake quests and names only.
return function(M, ctx)
  local T = TreeAI
  local check = ctx.check
  local QP, W, A, Q, DM, X = T.QuestPins, T.Watch, T.Addons, T.QoL, T.Meter, T.Export
  check(QP and W and A and Q and DM and X, "QuestPins + Watch + Addons + QoL + Meter + Export loaded")
  if not (QP and W and A and Q and DM and X) then return end
  local function near(a, b, eps) return a and b and math.abs(a - b) <= (eps or 1e-6) end
  local function blocked() return #(M.blocked or {}) end
  local function viol(kind) local n = 0 for _, v in ipairs(M.violations) do if not kind or v.kind == kind then n = n + 1 end end return n end
  M.LeaveCombat()
  local b0, v0 = blocked(), #M.violations

  -- ------------------------------------------------------------------ world, log, fake QuestieDB
  M.player.level = 28
  M.mapRects[1424] = { top = 1000, left = 2000, w = 3000, h = 2000, continent = 1 }
  T.Geo.rects = {}
  M.pos = { mapID = 1424, x = 0.50, y = 0.50 }
  M.facing, M.speed, M.minimapZoom, M.indoors = 0, 0, 0, false
  M.cvars.rotateMinimap = "0"
  M.group = {}
  M.FireEvent("GROUP_ROSTER_UPDATE")
  M.AddItem(9001, { name = "Totem de test", quality = 1 })
  M.quests = {
    { id = 1001, title = "Loups", level = 27, complete = false, item = 9001,
      objectives = { { text = "Loup tué : 2/6", done = false, have = 2, need = 6 } } },
    { id = 1002, title = "Crocs", level = 27, complete = true, objectives = {} },
  }
  M.SetCompleted({})
  M.SetPlan(nil)
  local rid = 3
  M.InstallQuestieDB({
    areaToMap = { [10] = 1424 },
    xp = { [1001] = 2500, [1002] = 2400, [2001] = 2600, [2002] = 1800, [2008] = 3100 },
    quests = {
      [1001] = { name = "Loups", questLevel = 27, requiredLevel = 22, zoneOrSort = 10, startedBy = { { 700 } }, finishedBy = { { 700 } }, objectives = { { { 501 } } } },
      [1002] = { name = "Crocs", questLevel = 27, requiredLevel = 22, zoneOrSort = 10, startedBy = { { 700 } }, finishedBy = { { 701 } }, objectives = {} },
      [2001] = { name = "Nouvelle", questLevel = 28, requiredLevel = 24, zoneOrSort = 10, startedBy = { { 700 } }, finishedBy = { { 700 } }, objectives = { { { 501 } } } },
      [2002] = { name = "Autre", questLevel = 28, requiredLevel = 24, zoneOrSort = 10, startedBy = { { 700 } }, finishedBy = { { 700 } }, objectives = {} },
      [2003] = { name = "Trop haut", questLevel = 32, requiredLevel = 30, zoneOrSort = 10, startedBy = { { 702 } }, finishedBy = { { 702 } }, objectives = {} },
      [2004] = { name = "Grise", questLevel = 21, requiredLevel = 16, zoneOrSort = 10, startedBy = { { 703 } }, finishedBy = { { 703 } }, objectives = {} },
      [2005] = { name = "Suite", questLevel = 28, requiredLevel = 24, zoneOrSort = 10, preQuestSingle = { 1999 }, startedBy = { { 704 } }, finishedBy = { { 704 } }, objectives = {} },
      [2006] = { name = "Autre classe", questLevel = 28, requiredLevel = 24, zoneOrSort = 10, requiredClasses = 1, startedBy = { { 705 } }, finishedBy = { { 705 } }, objectives = {} },
      [2007] = { name = "Exclusive", questLevel = 28, requiredLevel = 24, zoneOrSort = 10, exclusiveTo = { 1001 }, startedBy = { { 706 } }, finishedBy = { { 706 } }, objectives = {} },
      [2008] = { name = "Dure", questLevel = 31, requiredLevel = 26, zoneOrSort = 10, startedBy = { { 707 } }, finishedBy = { { 707 } }, objectives = {} },
    },
    npcs = {
      [700] = { name = "Chef", minLevel = 30, maxLevel = 30, rank = 0, spawns = { [10] = { { 52, 52 } } } },
      [701] = { name = "Maire", minLevel = 30, maxLevel = 30, rank = 0, spawns = { [10] = { { 60, 40 } } } },
      [702] = { name = "Haut", spawns = { [10] = { { 53, 50 } } } },
      [703] = { name = "Gris", spawns = { [10] = { { 54, 50 } } } },
      [704] = { name = "Suiveur", spawns = { [10] = { { 55, 50 } } } },
      [705] = { name = "Guerrier", spawns = { [10] = { { 56, 50 } } } },
      [706] = { name = "Exclu", spawns = { [10] = { { 57, 50 } } } },
      [707] = { name = "Dur", spawns = { [10] = { { 45, 45 } } } },
      [501] = { name = "Loup", minLevel = 26, maxLevel = 27, rank = 0, spawns = { [10] = { { 40, 60 }, { 41, 61 }, { 42, 60 } } } },
    },
  })
  check(rid, "fake race id")
  X:Rebuild()
  M.Advance(6, 0.1)
  local exp = X:Data()
  check(exp and exp.src == "LibQuestieDB" and #(exp.quests or {}) >= 8, "export built from the fake QuestieDB (" .. tostring(exp and #exp.quests) .. " quests)")
  local inExp = {}
  for _, r in ipairs(exp and exp.quests or {}) do inExp[r.id] = r.st end
  check(inExp[1001] == "log" and inExp[2001] == "avail" and inExp[2004] == "avail" and not inExp[2006],
    "export: log + available (grey kept for the option, other class already out)")

  -- ------------------------------------------------------------------ QuestPins : the list (pure)
  local pins = QP.Pins(true)
  local by = {}
  for _, p in ipairs(pins) do by[p.key] = p end
  local turn, obj, chef, dur = by["t:npc:701"], by["o:1001:1:1"], by["a:npc:700"], by["a:npc:707"]
  check(turn and turn.kind == "turnin" and near(turn.x, 60, 0.6) and near(turn.y, 40, 0.6), "« ? » at the finisher of the ready quest (Maire 60,40)")
  check(obj and obj.kind == "obj" and obj.r and obj.r >= 1.2 and obj.otype == "kill" and obj.objText == "Loup tué : 2/6",
    "objective area of « Loups » (cluster, radius, kill icon, live text)")
  check(chef and chef.kind == "avail" and #chef.qs == 2, "one « ! » for the giver with 2 quests (Nouvelle + Autre)")
  check(dur and dur.kind == "avail" and near(dur.cr, 1) and near(dur.cg, 0.5) and near(dur.cb, 0.25), "« Dure » (31) orange for a level 28")
  check(chef and near(chef.cr, 1) and near(chef.cg, 1) and near(chef.cb, 0), "level-28 quests yellow")
  local bad = {}
  for _, k in ipairs({ "a:npc:702", "a:npc:703", "a:npc:704", "a:npc:705", "a:npc:706" }) do if by[k] then bad[#bad + 1] = k end end
  check(#bad == 0, "filtered out: level too high, grey, prerequisite missing, other class, exclusive with a log quest: " .. table.concat(bad, ","))
  QP.db.trivial = true
  QP:OnOptionChanged()
  local grey = false
  for _, p in ipairs(QP.Pins()) do if p.key == "a:npc:703" then grey = true end end
  check(grey, "option « quêtes grises » shows the grey quest")
  QP.db.trivial = false
  QP:OnOptionChanged()
  -- tooltip: what, who, XP, distance + time, why
  local me = QP.PlayerWorld()
  check(me and me.c == 1, "player world position")
  local title, lines = QP.TipLines(chef, me)
  local all = title
  for _, l in ipairs(lines) do all = all .. " | " .. l[1] end
  -- la quête du trajet mène le groupe (« Sur ton trajet : arrêt N »), l'autre suit sur une ligne « + »
  local lead = chef.lead
  local other = (lead and lead.id == 2001) and "Autre" or "Nouvelle"
  check((title:find("%[28%] Nouvelle") or title:find("%[28%] Autre")) and all:find("À prendre chez Chef", 1, true)
    and all:find("+ [28] " .. other, 1, true) and all:find("%d %d00 XP au rendu") and all:find(" m · ≈ %d+ min à pied")
    and (all:find("Sur ton trajet : arrêt %d+") or all:find("À ton niveau", 1, true)),
    "tooltip: quest, giver, other quest, XP, distance + time, why: " .. all)
  check(not all:find("≈ ≈", 1, true), "no doubled « ≈ »")
  local ttl, tl2 = QP.TipLines(turn, me)
  local s2 = ttl
  for _, l in ipairs(tl2) do s2 = s2 .. " | " .. l[1] end
  check(s2:find("À rendre à Maire", 1, true) and s2:find("L'XP tombe au rendu", 1, true), "turn-in tooltip: who + why: " .. s2)
  -- click = compass
  local C = T.Compass
  local got = QP.Click(dur)
  check(got and C.char.custom and tostring(C.char.custom.name):find("Prendre « Dure »", 1, true), "click on a « ! » = compass on the giver")
  C.ClearWaypoint()

  -- ------------------------------------------------------------------ Questie still drawing → silent
  rawset(_G, "Questie", { db = { profile = { enabled = true, enableMapIcons = true, enableMiniMapIcons = true, trackerEnabled = true, minimap = { hide = false } } } })
  check(QP.QuestieDraws("map") and not QP.Want("map") and not QP.Want("minimap"), "Questie draws its icons → TreeAI draws none (no duplicates)")
  _G.Questie.db.profile.enableMiniMapIcons = false
  check(not QP.Want("map") and QP.Want("minimap"), "Questie minimap icons off → TreeAI minimap icons only")
  _G.Questie.db.profile.enabled = false
  check(QP.Want("map") and QP.Want("minimap"), "Questie icons off (silent) → TreeAI draws everything")

  -- ------------------------------------------------------------------ world map: (4.2) our layer INSIDE the map canvas
  local wm = CreateFrame("Frame", "WorldMapFrame", UIParent)
  local sc = CreateFrame("Frame", nil, wm)
  local child = CreateFrame("Frame", nil, sc)
  wm.ScrollContainer, sc.Child = sc, child
  wm.__shown = false
  local cx, cw = 100, 1000
  function wm:GetMapID() return 1424 end
  function sc:GetLeft() return 100 end
  function sc:GetBottom() return 50 end
  function sc:GetWidth() return 1000 end
  function sc:GetHeight() return 667 end
  function child:GetLeft() return cx end
  function child:GetBottom() return 50 end
  function child:GetWidth() return cw end
  function child:GetHeight() return 667 * cw / 1000 end
  local providers = {}
  function wm:AddDataProvider(p) providers[#providers + 1] = p end
  rawset(_G, "MapCanvasDataProviderMixin", { OnAdded = function() end })
  rawset(_G, "CreateFromMixins", function(...)
    local o = {}
    for i = 1, select("#", ...) do for k, v in pairs((select(i, ...))) do o[k] = v end end
    return o
  end)
  QP._mapHooked = nil
  check(QP.AttachMap() and QP.mapEngine == "canvas" and #providers == 0, "(4.2) world map: layer in the canvas, no data provider")
  local wmScripts, wmPoints = 0, #wm.__points
  for _ in pairs(wm.__scripts) do wmScripts = wmScripts + 1 end
  wm:Show()
  M.Advance(0.05, 0.05)
  local shown = QP.mapShown
  check(shown >= 4 and QP.view and QP.view:IsVisible() and QP.canvas == QP.view and QP.view:GetParent() == child,
    "map open: icons drawn on OUR layer, a child of the map canvas (" .. shown .. ")")
  local chefBtn
  for _, e in ipairs(QP.mapPins) do if e.b:IsShown() and e.b.pin and e.b.pin.key == "a:npc:700" then chefBtn = e.b end end
  local pt = chefBtn and chefBtn.__points[1]
  local ox, oy = 0, 0
  if chefBtn and chefBtn.pin.stop then ox, oy = 6, 4 end   -- arrêt du trajet : à côté de la pastille numérotée
  check(pt and pt[2] == QP.canvas and near(pt[4], 520 + ox, 1e-6) and near(pt[5], -0.52 * 667 + oy, 1e-6), "« ! » of Chef at (52 %, 52 %) of the canvas")
  check(chefBtn and chefBtn.icon:GetTexture():find("AvailableQuestIcon"), "« ! » texture")
  local objE
  for _, e in ipairs(QP.mapPins) do if e.b:IsShown() and e.b.pin and e.b.pin.kind == "obj" then objE = e end end
  check(objE and objE.disk:IsShown(), "objective area drawn as a disk")
  check(#wm.__points == wmPoints, "WorldMapFrame never anchored / moved")
  local n2 = 0
  for _ in pairs(wm.__scripts) do n2 = n2 + 1 end
  check(n2 == wmScripts, "no script set / hooked on WorldMapFrame")
  -- pan: the layer moves with the canvas, nothing re-placed ; zoom: icons re-placed, same canvas point
  local r0 = QP.mapRedraws
  cx = 60
  M.Advance(0.1, 0.05)
  check(QP.mapRedraws == r0, "pan = nothing to do (the layer is in the canvas)")
  child:SetScale(1.5)
  M.Advance(0.1, 0.05)
  local pz = chefBtn and chefBtn.__points[1]
  check(QP.mapRedraws == r0 + 1 and pz and near(pz[4] * QP.view:GetScale(), 520 + ox * QP.view:GetScale(), 1e-6), "zoom = icons re-placed on the same canvas point")
  M.Advance(3, 0.2)
  check(QP.mapRedraws == r0 + 1, "static open map: no redraw")
  child.__scale = 1
  -- density caps: 60 extra givers in the zone
  local extra = {}
  for i = 1, 60 do
    local id = 3000 + i
    exp.quests[#exp.quests + 1] = { id = id, t = "Masse " .. i, lv = 28, rl = 24, st = "avail", xp = 100,
      s = { map = 1424, x = 30 + (i % 10) * 4, y = 20 + math.floor(i / 10) * 6, k = "npc", id = 8000 + i } }
    extra[#extra + 1] = id
  end
  exp.made = (exp.made or 0) + 1
  QP.MapDirty()
  if QP.Invalidate then QP.Invalidate() end
  M.Advance(0.1, 0.05)
  local nAvail = 0
  for _, e in ipairs(QP.mapPins) do if e.b:IsShown() and e.b.pin.kind == "avail" then nAvail = nAvail + 1 end end
  check(QP.mapShown <= QP.MAX_MAP and nAvail <= QP.MAX_AVAIL_MAP, ("density caps: %d icons (≤ %d), %d « ! » (≤ %d)"):format(QP.mapShown, QP.MAX_MAP, nAvail, QP.MAX_AVAIL_MAP))
  local keepTurn = false
  for _, e in ipairs(QP.mapPins) do if e.b:IsShown() and e.b.pin.kind == "turnin" then keepTurn = true end end
  check(keepTurn, "turn-ins kept first under the cap")
  for i = #exp.quests, 1, -1 do if exp.quests[i].id >= 3000 then table.remove(exp.quests, i) end end
  exp.made = exp.made + 1
  wm.__shown = false
  check(not QP.view:IsVisible(), "map closed → layer hidden with the map")

  -- ------------------------------------------------------------------ minimap: own engine, then HereBeDragons
  rawset(_G, "LibStub", nil)
  QP.Invalidate()
  QP.Wake()
  QP.MiniTick(true)
  check(QP.miniEngine == "own" and QP.miniShown >= 1, "minimap: own engine, icons in range shown (" .. QP.miniShown .. ")")
  local mb = QP.miniPins[1]
  check(mb and mb:GetParent() == Minimap, "minimap icons are our children of Minimap")
  local places = QP.miniPlaces
  M.speed = 0
  for _ = 1, 10 do QP.MiniTick() end
  check(QP.miniPlaces == places, "standing still: no re-placement")
  check(QP.asleep, "standing still: the minimap ticker stops")
  -- walking (minimap north-up): only the holder slides, icons are not re-anchored one by one
  local slides, pts0 = QP.miniSlides or 0, #(mb.__points or {})
  M.pos = { mapID = 1424, x = 0.505, y = 0.50 }
  M.Advance(0.05, 0.05)
  QP.MiniTick()
  check((QP.miniSlides or 0) == slides + 1 and QP.miniPlaces == places and #(mb.__points or {}) == pts0,
    "walking: one anchor for the holder, no icon re-placed")
  M.pos = { mapID = 1424, x = 0.50, y = 0.50 }
  M.Advance(0.05, 0.05)
  QP.MiniTick()
  local hbdCalls, hbdRemoved = 0, 0
  local lib = { AddMinimapIconMap = function() hbdCalls = hbdCalls + 1 end, RemoveMinimapIcon = function() hbdRemoved = hbdRemoved + 1 end }
  rawset(_G, "LibStub", setmetatable({ GetLibrary = function(_, name) if name == "HereBeDragons-Pins-2.0" then return lib end end },
    { __call = function(_, name) if name == "HereBeDragons-Pins-2.0" then return lib end end }))
  QP:OnOptionChanged()
  QP.MiniTick(true)
  check(QP.miniEngine == "hbd" and hbdCalls >= 1, "HereBeDragons present (Questie embeds it): icons handed to the library (" .. hbdCalls .. ")")
  local c1 = hbdCalls
  for _ = 1, 5 do QP.MiniTick() end
  check(hbdCalls == c1, "HereBeDragons: nothing re-sent while the list is unchanged")
  QP.ClearMini()
  rawset(_G, "LibStub", nil)
  QP:OnOptionChanged()

  -- ------------------------------------------------------------------ accepting a quest removes its « ! »
  table.insert(M.quests, { id = 2001, title = "Nouvelle", level = 28, complete = false, objectives = { { text = "Loup tué : 0/4", done = false, have = 0, need = 4 } } })
  M.FireEvent("QUEST_ACCEPTED", 3, 2001)
  M.FireEvent("QUEST_LOG_UPDATE")
  M.Advance(1, 0.1)
  local chef2
  for _, p in ipairs(QP.Pins()) do if p.key == "a:npc:700" then chef2 = p end end
  check(chef2 and #chef2.qs == 1 and chef2.qs[1].id == 2002, "accepted quest leaves the « ! » at once (the other stays)")
  table.remove(M.quests, 3)
  M.FireEvent("QUEST_REMOVED", 2001)
  M.FireEvent("QUEST_LOG_UPDATE")
  M.Advance(1, 0.1)

  -- ------------------------------------------------------------------ Watch « Suivi TreeAI »
  local Qm = T.modules.Quests
  if Qm and Qm.Update then Qm.Update() end
  W.db.shown = true              -- (the harness storm clicked every button, our « close » included)
  W.Apply()
  W.Render()
  local f = _G.TreeAIWatchFrame
  check(f and f:IsShown() and W.rowsShown and W.rowsShown >= 5, "Suivi TreeAI shown (" .. tostring(W.rowsShown) .. " rows)")
  local texts, objRow, turnRow = {}, nil, nil
  for i = 1, W.rowsShown or 0 do
    local r = W.rows[i]
    texts[#texts + 1] = tostring(r.fs:GetText()) .. "|" .. tostring(r.rt:GetText())
    if r.rowd and r.rowd.kind == "obj" and r.qe and r.qe.q.id == 1001 then objRow = r end
    if r.rowd and r.rowd.kind == "turn" then turnRow = r end
  end
  local blob = table.concat(texts, " / ")
  check(blob:find("[27] Loups", 1, true) and blob:find("Loup tué 2/6", 1, true) and blob:find("Rendre à Maire", 1, true),
    "rows: quest, objective with progress, turn-in with the NPC: " .. blob)
  check(objRow and tostring(objRow.rt:GetText()):find("^%d+ m [NESO]+ · %d+ min$"), "live distance · direction · time: " .. tostring(objRow and objRow.rt:GetText()))
  check(turnRow and tostring(turnRow.rt:GetText()):find("m NE · %d+ min"), "turn-in distance + direction (north-east): " .. tostring(turnRow and turnRow.rt:GetText()))
  check(objRow and objRow.bar:IsShown() and objRow.barBg:IsShown(), "objective progress bar")
  -- live updates: only changed rounded values are rewritten, nothing when still
  local w0 = W.textWrites
  W.UpdateTexts()
  check(W.textWrites == w0, "same position: no text rewritten")
  M.pos = { mapID = 1424, x = 0.47, y = 0.55 }
  M.Advance(0.05, 0.05)            -- (the core reads the position once per instant)
  W.UpdateTexts()
  check(W.textWrites > w0, "player moved: distances rewritten (" .. (W.textWrites - w0) .. ")")
  M.pos = { mapID = 1424, x = 0.50, y = 0.50 }
  M.Advance(0.05, 0.05)
  W.UpdateTexts()
  -- sort toggle
  W.ToggleSort()
  M.Advance(0.5, 0.1)
  check(W.db.sort == 2 and f.sortBtn.t:GetText() == "Par distance", "header toggle: « Par distance »")
  W.ToggleSort()
  M.Advance(0.5, 0.1)
  check(W.db.sort == 1 and f.sortBtn.t:GetText() == "Ordre du trajet", "back to route order")
  -- quest item: secure button out of combat only
  M.Advance(0.5, 0.1)
  local it = W.items[1]
  check(it and it:IsShown() and it:GetAttribute("type") == "item" and it:GetAttribute("item") == "item:9001" and it:GetParent() == UIParent,
    "quest item: secure button (type=item) on UIParent, placed at the row")
  -- right-click menu
  local entries = W.MenuEntries(objRow and objRow.qe)
  local names = {}
  for _, e in ipairs(entries) do names[#names + 1] = e.text end
  local nm = table.concat(names, " / ")
  check(nm:find("Guider ici", 1, true) and nm:find("Retirer du suivi", 1, true) and nm:find("Abandonner", 1, true), "menu: guide, hide, abandon: " .. nm)
  for _, e in ipairs(entries) do if e.text == "Retirer du suivi" then T:UserAction(e.fn) end end
  M.Advance(0.5, 0.1)
  local still = false
  for i = 1, W.rowsShown do local r = W.rows[i] if r.qe and r.qe.q.id == 1001 then still = true end end
  check(not still and W.char.hidden[1001], "« Retirer du suivi » hides the quest")
  W.char.hidden = {}
  W.Render()
  -- (4.0.3 integ) REPLACE → pilote auto : a quest parked by the pilot leaves the tracker (one « En pause (n) » line)
  -- and the map (no objective area) on BRAIN_PARKED ; a quest ready to turn in never does
  do
    local hadBrain = T.Brain
    local Bn = hadBrain or {}
    local oldParked, oldList = Bn.Parked, Bn.ParkedList
    local paused = { [1001] = { k = "far", t = "autre continent", n = "Loups" }, [1002] = { k = "grey", t = "grise", n = "Crocs" } }
    Bn.Parked = function(q) return paused[tonumber(q)] end
    Bn.ParkedList = function() return { { q = 1001, k = "far", t = "autre continent", n = "Loups" } } end
    T.Brain = Bn
    T:Fire("BRAIN_PARKED")
    M.Advance(1, 0.1)
    local has1001, has1002, pauseRow = false, false, nil
    for i = 1, W.rowsShown or 0 do
      local r = W.rows[i]
      if r.qe and r.qe.q.id == 1001 then has1001 = true end
      if r.qe and r.qe.q.id == 1002 then has1002 = true end
      if r.kind == "paused" then pauseRow = r end
    end
    check(not has1001 and has1002 and pauseRow and tostring(pauseRow.fs:GetText()):find("En pause (1)", 1, true),
      "Suivi TreeAI: parked quest hidden, ready-to-turn-in kept, « En pause (1) » line: " .. tostring(pauseRow and pauseRow.fs:GetText()))
    local obj1001, turn1002 = false, false
    for _, p in ipairs(QP.Pins()) do
      if tostring(p.key):find("^o:1001:") then obj1001 = true end
      if p.kind == "turnin" then for _, q in ipairs(p.qs) do if q.id == 1002 then turn1002 = true end end end
    end
    check(not obj1001 and turn1002, "QuestPins: no objective area for a parked quest, the « ? » of a finished one kept")
    Bn.Parked, Bn.ParkedList = oldParked, oldList
    T.Brain = hadBrain
    T:Fire("BRAIN_PARKED")
    M.Advance(1, 0.1)
    local back, pr = false, false
    for i = 1, W.rowsShown or 0 do
      local r = W.rows[i]
      if r.qe and r.qe.q.id == 1001 then back = true end
      if r.kind == "paused" then pr = true end
    end
    local objBack = false
    for _, p in ipairs(QP.Pins()) do if tostring(p.key):find("^o:1001:") then objBack = true end end
    check(back and not pr and objBack, "resumed (BRAIN_PARKED): the quest is back in the tracker and on the map")
  end
  -- in combat: the progress is written in place (text, bar), nothing re-laid out, nothing blocked
  W.Render()
  local rendersBefore, bBefore = W.renders, blocked()
  M.EnterCombat()
  M.quests[1].objectives[1].text, M.quests[1].objectives[1].have = "Loup tué : 3/6", 3
  M.FireEvent("QUEST_LOG_UPDATE")
  if Qm and Qm.Update then Qm.Update() end
  W.Render()
  local prog
  for i = 1, W.rowsShown do local r = W.rows[i] if r.rowd and r.rowd.kind == "obj" and r.qe and r.qe.q.id == 1001 then prog = r end end
  check(prog and tostring(prog.fs:GetText()):find("Loup tué 3/6", 1, true) and W.renders == rendersBefore and blocked() == bBefore,
    "in combat: progress « 3/6 » written in place, no re-layout, nothing blocked: " .. tostring(prog and prog.fs:GetText()))
  M.LeaveCombat()
  M.Advance(1, 0.1)
  -- (4.2 ROUTE42) the game's tracker: never hidden, never moved — the column is anchored UNDER it
  local tf = CreateFrame("Frame", "ObjectiveTrackerFrame", UIParent)
  tf.__protected = true          -- worst case: its quest item buttons are secure
  tf:SetPoint("TOPRIGHT", UIParent, "TOPRIGHT", -60, -230)
  tf:Show()
  local tfPts = #tf.__points
  check(W.TakeOver() == false and tf:IsShown() and not W.replaced, "the game's tracker is never taken over (option removed)")
  if W.char then W.char.pos = nil end
  W._force = true
  W.Render()
  local wfa = _G.TreeAIWatchFrame
  local ap = { wfa:GetPoint(1) }
  check(ap[2] == tf and ap[1] == "TOPRIGHT" and ap[3] == "BOTTOMRIGHT" and W.anchoredTo == "ObjectiveTrackerFrame",
    "Suivi TreeAI anchored under the game's tracker")
  M.EnterCombat()
  W.Render()
  M.LeaveCombat()
  M.Advance(0.5, 0.1)
  check(tf:IsShown() and (tf.__alpha or 1) == 1 and #tf.__points == tfPts and blocked() == b0, "tracker untouched, in and out of combat")
  -- our window hidden in combat (alpha), shown again before the end of the fight: visible, never a transparent
  -- frame that would take the clicks
  local wf = _G.TreeAIWatchFrame
  M.EnterCombat()
  W.db.shown = false
  W:OnOptionChanged("shown")
  check(wf.__alpha == 0, "in combat: Suivi TreeAI hidden by alpha")
  W.db.shown = true
  W:OnOptionChanged("shown")
  check(wf:IsShown() and wf.__alpha == 1, "shown again in the same fight: alpha back to 1 (" .. tostring(wf.__alpha) .. ")")
  M.LeaveCombat()
  M.Advance(0.5, 0.1)
  check(wf:IsShown() and wf.__alpha == 1 and blocked() == b0, "after the fight: still shown (the pending hide gave up)")
  -- Questie tracker still on : (4.2) the column sits under it, no row offers to cut it anymore.
  -- (5.1 LOOK) READ-ONLY: TreeAI no longer writes in Questie's (or Shortest Path Forever's) options — the « silence »
  -- writers are gone ; the « Autres addons » page says what TreeAI reads and what it leaves, each line switchable
  _G.Questie.db.profile.enabled, _G.Questie.db.profile.trackerEnabled = true, true
  W.Render()
  local qrow
  for i = 1, W.rowsShown do if W.rows[i].kind == "questie" then qrow = W.rows[i] end end
  check(not qrow, "(4.2) no « cut Questie's tracker » row in the column")
  check(A.SilenceQuestie == nil and A.RestoreQuestie == nil and A.SetSPFTracker == nil and A.SPFChoose == nil,
    "(5.1) no function writes in another addon's options any more")
  local p = _G.Questie.db.profile
  local qst = A.QuestieState()
  check(qst.loaded and qst.readable and qst.icons and qst.tracker and not qst.silent, "Questie state read (icons + tracker shown)")
  check(p.enabled == true and p.trackerEnabled == true and p.minimap.hide ~= true, "Questie's options untouched")
  rawset(_G, "Questie", { loaded = true })
  check(A.QuestieState().loaded and not A.QuestieState().readable, "unreadable Questie options: assumed visible, nothing written")
  rawset(_G, "Questie", nil)

  -- ------------------------------------------------------------------ « Autres addons » page + SPF (read-only), no notice
  rawset(_G, "ShortestPathForever", { API = {} })
  rawset(_G, "ShortestPathForeverDB", { tracker = true, journeyTracker = true })
  A.Installed(true)
  check(A.SPFState().tracker and A.SPFState().loaded, "SPF tracker detected (its saved options, read only)")
  check(A.SPFConflict() == false, "no more « its route or yours » question")
  rawset(_G, "Questie", { db = { profile = { enabled = true, trackerEnabled = true, minimap = { hide = false } } } })
  A.Installed(true)
  local secs = A.Sections()
  local titles = {}
  for _, s in ipairs(secs) do titles[#titles + 1] = s.title end
  local ts = table.concat(titles, " / ")
  check(ts:find("Questie", 1, true) and ts:find("Shortest Path", 1, true), "page sections for the installed addons: " .. ts)
  local qs
  for _, s in ipairs(secs) do if s.key == "questie" then qs = s end end
  check(qs and qs.lines[1].id == "data" and qs.lines[1].kind == "take" and qs.lines[2].id == "icons" and qs.lines[2].kind == "leave",
    "Questie section: its base is read, its icons and tracker are left to it")
  A.SetLink("questie", "icons", false)
  check(not A.Link("questie", "icons") and A.Link("questie", "data"), "each line switches off by itself")
  A.SetLink("questie", "icons", true)
  local Wn = T.Window
  if Wn and Wn.Open then
    Wn.Open("addons")
    M.Advance(0.3, 0.1)
    check(Wn.IsOpen() and Wn.CurrentPage() == "addons", "window opens on « Autres addons »")
    Wn.Close()
  end
  check(A.Notice == nil, "no unsolicited notice any more")
  for _, e in ipairs(T:GetSuggestions()) do check(not tostring(e.id):find("Addons:notice", 1, true), "no « Addons:notice » line") end
  check(_G.ShortestPathForeverDB.tracker == true and _G.ShortestPathForeverDB.journeyTracker == true
    and _G.Questie.db.profile.enabled == true and _G.Questie.db.profile.trackerEnabled == true, "nothing written in SPF's / Questie's saved options")
  A.SetLink("spf", "arrow", false)   -- (the rest of this scenario plays without Shortest Path Forever)
  rawset(_G, "Questie", nil)
  rawset(_G, "ShortestPathForever", nil)
  rawset(_G, "ShortestPathForeverDB", nil)
  A.Installed(true)
  A.SetLink("spf", "arrow", true)

  -- ------------------------------------------------------------------ QoL: grey selling / repair, never twice
  local K = T.modules.Quick
  M.SetBagItem(2, 1, 4865, 3)
  local sold0 = M.sold or 0
  if K then K.db.autoSell, K.db.autoRepair = true, true end
  check(not K or Q.QuickDoesIt() == (K._active and true or false), "Quick doing it is detected")
  if K and K._active then
    T:SetModuleEnabled("Quick", false)
  end
  Q.db.sellRepair = true
  M.SetMerchant(true)
  M.Advance(1, 0.1)
  check((M.sold or 0) > sold0, "merchant: greys sold by Confort when the quest bar does not (" .. ((M.sold or 0) - sold0) .. ")")
  M.SetMerchant(false)
  if K then T:SetModuleEnabled("Quick", true) end
  -- instant loot: every slot at LOOT_READY when the game auto-loots
  local looted = {}
  rawset(_G, "LootSlot", function(i) looted[#looted + 1] = i end)
  M.cvars.autoLootDefault = "1"
  M.loot = { { name = "A" }, { name = "B" }, { name = "C" } }
  M.FireEvent("LOOT_READY", true)
  check(#looted == 3 and looted[1] == 3, "instant loot: 3 slots taken at once (" .. #looted .. ")")
  M.FireEvent("LOOT_READY", true)
  check(#looted == 3, "one pass per loot window")
  M.Advance(0.5, 0.1)
  M.FireEvent("LOOT_READY", false)
  check(#looted == 3, "no auto-loot this time (modifier) → nothing taken")
  rawset(_G, "LootSlot", nil)
  M.loot = nil
  -- camera / auto-loot settings: one click, never in combat
  M.cvars.cameraDistanceMaxZoomFactor = "1.9"
  T:UserAction(Q.ToggleCamera)
  check(M.cvars.cameraDistanceMaxZoomFactor == "2.6", "camera: max zoom-out on a click")
  M.EnterCombat()
  local vb = viol()
  T:UserAction(Q.ToggleCamera)
  check(M.cvars.cameraDistanceMaxZoomFactor == "2.6" and viol() == vb, "in combat: refused, no blocked SetCVar")
  M.LeaveCombat()
  T:UserAction(Q.ToggleCamera)
  check(M.cvars.cameraDistanceMaxZoomFactor == "1.9", "camera back to normal")

  -- ------------------------------------------------------------------ Meter: C_DamageMeter after combat only
  local reads = 0
  local inCombatRead = false
  local secret = M.Secret
  rawset(_G, "C_DamageMeter", {
    IsDamageMeterAvailable = function() return true, "" end,
    GetCombatSessionFromType = function(st)
      reads = reads + 1
      if M.inCombat then inCombatRead = true end
      local function v(x) if M.inCombat and secret and M.flavor == "forever" then return secret(x) end return x end
      if st == 0 then
        return { totalAmount = v(30000), durationSeconds = 600, combatSources = {
          { name = "Arbrelune", classFilename = "HUNTER", totalAmount = v(18000), amountPerSecond = v(30), isLocalPlayer = true },
          { name = "Amie", classFilename = "PRIEST", totalAmount = v(12000), amountPerSecond = v(20) } } }
      end
      return { totalAmount = v(4200), durationSeconds = 42, combatSources = {
        { name = "Amie", classFilename = "PRIEST", totalAmount = v(1200), amountPerSecond = v(28.6) },
        { name = "Arbrelune", classFilename = "HUNTER", totalAmount = v(3000), amountPerSecond = v(71.4), isLocalPlayer = true } } }
    end,
    GetSessionDurationSeconds = function() return 42 end,
    ResetAllCombatSessions = function() end,
  })
  T:SetModuleEnabled("Meter", true)
  DM.db.shown, DM.db.mode = true, 1
  DM.Apply()
  local d = DM.Read(1)
  check(d and #d.rows == 2 and d.rows[1].name == "Arbrelune" and d.rows[1].amount == 3000 and d.rows[1].pct == 71 and d.dur == 42,
    "last fight: sorted, % of the total, duration")
  local mf = _G.TreeAIMeterFrame
  check(mf and mf:IsShown() and tostring(DM.rows[1].l:GetText()):find("1. Arbrelune", 1, true)
    and tostring(DM.rows[1].r:GetText()):find("3000 · 71/s · 71 %%"), "window rows: name, damage, per second, share: " .. tostring(DM.rows[1] and DM.rows[1].r:GetText()))
  DM.ToggleMode()
  check(DM.db.mode == 2 and tostring(DM.rows[1].r:GetText()):find("18,0 k", 1, true), "session view: « 18,0 k »")
  DM.ToggleMode()
  local r0b = reads
  M.EnterCombat()
  M.FireEvent("DAMAGE_METER_COMBAT_SESSION_UPDATED", 0, 1)
  M.FireEvent("DAMAGE_METER_CURRENT_SESSION_UPDATED")
  M.Advance(1, 0.1)
  DM.Render()
  check(reads == r0b and not inCombatRead, "in combat: the meter is never read (secret values)")
  check(tostring(mf.foot:GetText()):find("En combat", 1, true), "footer: numbers at the end of the fight")
  M.LeaveCombat()
  M.Advance(1, 0.1)
  check(reads > r0b and not inCombatRead, "after the fight: refreshed")
  _G.C_DamageMeter.IsDamageMeterAvailable = function() return false, "Compteur coupé dans les options" end
  DM.Render()
  check(tostring(mf.foot:GetText()):find("Compteur coupé dans les options", 1, true), "unavailable: the game's reason is shown")
  T:SetModuleEnabled("Meter", false)
  rawset(_G, "C_DamageMeter", nil)

  -- ------------------------------------------------------------------ perf (count hook, QuestPins only)
  local function measure(secs, step, each)
    local n = 0
    local getinfo = debug.getinfo
    debug.sethook(function()
      local lvl = 2
      while true do
        local info = getinfo(lvl, "S")
        if not info then break end
        local src = info.source
        if src:find("TreeAI", 1, true) then
          if src:find("QuestPins.lua", 1, true) then n = n + 1 end
          break
        end
        lvl = lvl + 1
      end
    end, "", 37)   -- (4.1) échantillon tous les 37 (premier, × 37 / 1000 ci-dessous) : un pas de 100 se calait sur la
                   -- cadence des passes (±15 % selon le reste du code qui tourne entre deux passes)
    local t = 0
    while t < secs do
      if each then each(t) end
      M.Advance(step, step)
      t = t + step
    end
    debug.sethook()
    return n * 37 / 1000 * 60 / secs
  end
  M.speed = 0
  M.Advance(5, 0.5)
  local idle = measure(60, 0.5)
  check(idle == 0, ("idle (map closed, standing still): %.1f kinstr/min (0)"):format(idle))
  local x0 = 0.5
  M.speed = 7
  local moving = measure(60, 0.2, function(t) M.pos = { mapID = 1424, x = x0 + t * 0.0008, y = 0.5 } end)
  -- (4.1 ROUTEFIX) lecture sans repliement (pas de 37) : 4.1.0 = 72,9 en marchant, 14,5 carte ouverte, 134,1 carte tirée
  check(moving <= 80, ("moving, map closed, own minimap engine: %.1f kinstr/min (≤ 80)"):format(moving))
  M.speed = 0
  M.pos = { mapID = 1424, x = 0.5, y = 0.5 }
  wm:Show()
  local opened = measure(60, 0.5, function() end)
  check(opened <= 40, ("map open 60 s: %.1f kinstr/min (≤ 40)"):format(opened))
  local panning = measure(30, 0.2, function(t) cx = 60 + t * 10 end)
  -- (4.1) mesuré au 1/100 (au 1/1000, l'aliasing donnait 38 à la 4.0.6 pour un vrai ≈ 70 modern / 95 forever) :
  -- plafond 120 (4.1 : 63 / 87)
  check(panning <= 150, ("map open and dragged: %.1f kinstr/min (≤ 150)"):format(panning))
  wm.__shown = false
  M.Advance(2, 0.5)
  local after = measure(30, 0.5)
  check(after == 0, ("map closed again, still: %.1f kinstr/min (0)"):format(after))
  io.stdout:write(("PERF QuestPins idle %.1f · moving %.1f · map open %.1f · dragged %.1f kinstr/min\n"):format(idle, moving, opened, panning))

  check(blocked() == b0, "zero blocked actions")
  local vs = {}
  for i = v0 + 1, #M.violations do vs[#vs + 1] = M.violations[i].kind .. ": " .. M.violations[i].msg end
  check(#vs == 0, "no violation: " .. table.concat(vs, " | "))
end
