-- ZONEFIX (4.0.1) — the first real-client screenshot of 4.0 (image 20): level-28 Horde hunter at Splintertree Post
-- (Orneval / Ashenvale), « mode sûr », zone « Orneval » chosen. Before: Trajet « ≈ 1 h 27 · 22 arrêts » mixing Orneval,
-- Les Tarides, Mille pointes (a flight to Freewind Post!), a dungeon quest (« Goûts changeants ») as stop 1, « Garde 27
-- quêtes » ; the compass on « Prendre « Impasse aux Serres-Roch… » » 65 m behind (not the card's stop 1), hiding « de
-- temps en temps », slow to update ; « objectif · ≈ ≈ 5 min sur place » ; the game's tracker full of other zones.
-- Every finding replayed and checked here, plus route modes, « Zones à faire », themes and the auto watch list.
-- Variants (ZF_VARIANT): full (Questie) · noquestie. Run by tests/wow/test_zonefix.py (forever + modern, --profile sur).
-- Fake quest / NPC ids (none of them a real dungeon quest id of Data/Dungeons.lua: those are always dungeon quests),
-- French titles, real client map bounds and taxi nodes.
return function(M, ctx)
  local T = TreeAI
  -- (BRAIN 4.0.2) le pilote auto met en pause les quêtes grises (« Brouet de rat », niv 23 ici) et change le trajet :
  -- il est testé à part (brain_scenario.lua) ; ici, les règles du trajet seules
  if T.modules.Brain and T.SetModuleEnabled then T:SetModuleEnabled("Brain", false) end
  local check = ctx.check
  local P, Z, X, R, Cm, W = T.Planner, T.ZonePlan, T.Export, T.Route, T.Compass, T.Window
  check(P and Z and X and R and Cm, "quest chain loaded")
  if not (P and Z and X and R and Cm) then return end
  local VARIANT = os.getenv("ZF_VARIANT") or "full"

  -- ================================================================== 0. mode sûr: the route by zone is ON
  check(T:ModuleProfile() == "sur" or os.getenv("ZF_PROFILE") == "tout", "the player's profile: mode sûr")
  check(not T:IsBeta("ZonePlan") and Z._active, "ZonePlan (zone route) runs in mode sûr (it was « bêta », off: capture 20)")

  -- ------------------------------------------------------------------ world (real client bounds, yards)
  local MAPS = {
    [1440] = { "Orneval", 1, 4672.9, 1700.0, 829.2, -4066.7 },
    [1413] = { "Les Tarides", 1, 1612.5, 2622.9, -5143.8, -7510.4 },
    [1441] = { "Mille pointes", 1, -3966.7, -433.3, -6900.0, -4833.3 },
    [1442] = { "Les Serres-Rocheuses", 1, 2916.7, 3245.8, -339.6, -1637.5 },
    [1454] = { "Orgrimmar", 1, 2273.9, -3680.6, 1338.5, -5083.2 },
    [1411] = { "Durotar", 1, 1808.3, -1962.5, -1716.7, -7250.0 },
    [1456] = { "Thunder Bluff", 1, -850.0, 516.7, -1545.8, -527.1 },
    [1437] = { "Les Paluns", 0, -2079.2, -1800.0, -4566.7, -5533.3 },
  }
  for id, m in pairs(MAPS) do
    M.mapRects[id] = { top = m[3], left = m[4], h = m[3] - m[5], w = m[4] - m[6], continent = m[2] }
    M.mapNames[id] = m[1]
  end
  T.Geo.rects = {}
  Z.ResetMaps()
  rawset(_G, "UnitFactionGroup", function() return "Horde", "Horde" end)
  M.player.level = 28
  M.group = {}
  M.FireEvent("GROUP_ROSTER_UPDATE")
  local function goTo(map, x, y)
    M.pos = { mapID = map, x = x / 100, y = y / 100 }
    M.player.zone = MAPS[map][1]
    Z._lastAllowed = nil
    M.FireEvent("ZONE_CHANGED_NEW_AREA")
  end
  goTo(1440, 73.3, 62.6)                               -- Splintertree Post, a few steps south of Mastok
  R.char.paused = false                                -- the generic event storm may have clicked « Pause »
  Cm.char.custom, Cm.pinned = nil, nil                 -- …or a minimap pin
  Z.char.ignore, Z.char.extra, Z.char.style = {}, {}, 1
  if W and W.db then W.db.card = true end              -- the storm may have unticked the card
  R.char.taxi = {}
  for _, id in ipairs({ 22, 23, 25, 29, 30, 77, 61, 58 }) do R.char.taxi[id] = true end
  rawset(_G, "GetBindLocation", function() return "Orgrimmar" end)
  if T.Speed and T.Speed.char then T.Speed.char.bind = { map = 1454, x = 54.1, y = 68.4, name = "Orgrimmar" } end

  -- ------------------------------------------------------------------ the quest log of the screenshot (level 28, solo)
  local DUNGEON = 81
  local function obj(text, have, need, typ) return { text = text, have = have, need = need, done = have >= need, type = typ or "monster" } end
  local LOG = {
    -- Orneval
    { id = 5110, title = "Les eaux du lac Mystral", level = 26, map = 1440, objectives = { obj("Fiole d'eau forte remplie : 0/1", 0, 1, "item") } },
    { id = 5205, title = "Les estafettes d'Ashenvale", level = 25, map = 1440, objectives = { obj("Estafette tuée : 2/6", 2, 6) } },
    { id = 5201, title = "La patte d'Ursangous", level = 25, map = 1440, objectives = { obj("Patte d'Ursangous : 0/1", 0, 1, "item") } },
    { id = 5230, title = "Le sang des furbolgs", level = 33, map = 1440, objectives = { obj("Furbolg tué : 0/8", 0, 8) } },  -- too hard solo
    -- Les Tarides (Bael Modan, the rats)
    { id = 5107, title = "Brouet de rat", level = 23, map = 1413, objectives = { obj("Rat fouisseur : 0/8", 0, 8, "item") } },
    { id = 5170, title = "Le journal de Khazgorm", level = 27, map = 1413, objectives = { obj("Journal de Khazgorm : 0/1", 0, 1, "item") } },
    { id = 5171, title = "Les contremaîtres de Bael'dun", level = 27, map = 1413, objectives = { obj("Contremaître de Bael'dun tué : 0/5", 0, 5) } },
    { id = 5172, title = "Les excavateurs de Bael'dun", level = 27, map = 1413, objectives = { obj("Excavateur de Bael'dun tué : 0/15", 0, 15) } },
    { id = 5173, title = "Le poignard de Tranchecrin", level = 26, map = 1413, objectives = { obj("Poignard de Tranchecrin : 0/1", 0, 1, "item") } },
    -- Mille pointes
    { id = 5174, title = "Arikara", level = 28, map = 1441, objectives = { obj("Peau de serpent d'Arikara : 0/1", 0, 1, "item") } },
    -- a Forever dungeon quest the client does NOT tag (Questie does: zone = the instanced Excavation Site)
    { id = 5300, title = "Goûts changeants", level = 31, map = 1437, objectives = { obj("Viande de raptor des fourrés : 0/4", 0, 4, "item") } },
  }
  M.quests = {}
  for _, q in ipairs(LOG) do M.quests[#M.quests + 1] = q end
  M.questTags = {}
  M.SetCompleted({ [5100] = true })                     -- « La flamme sacrée » (the story's first quest) done
  P.char.turned = {}
  M.SetPlan(nil)
  M.FireEvent("QUEST_LOG_UPDATE")

  -- ------------------------------------------------------------------ fake QuestieDB
  -- areas: 331 Orneval, 17 Les Tarides, 400 Mille pointes, 406 Serres-Rocheuses, 1637 Orgrimmar, 14 Durotar,
  -- 16732 Excavation Site (Wetlands) = a Forever dungeon with no UiMap of its own
  local AREAS = { [331] = 1440, [17] = 1413, [400] = 1441, [406] = 1442, [1637] = 1454, [14] = 1411, [16732] = 1437 }
  local function npc(name, lv, area, x, y, rank) return { name = name, minLevel = lv, maxLevel = lv, rank = rank or 0, spawns = { [area] = { { x, y } } } } end
  local function camp(name, lv, area, cx, cy)
    local s = {}
    for i = 0, 7 do s[#s + 1] = { cx + (i % 4) - 1.5, cy + math.floor(i / 4) * 2 - 1 } end
    return { name = name, minLevel = lv, maxLevel = lv + 1, rank = 0, spawns = { [area] = s } }
  end
  local function quest(name, ql, rl, zone, giver, ender, kills, items, pre)
    return { name = name, questLevel = ql, requiredLevel = rl, zoneOrSort = zone, startedBy = { { giver } }, finishedBy = { { ender } },
      objectives = { kills and { kills } or nil, nil, items and { items } or nil }, preQuestSingle = pre }
  end
  local QUESTS = {
    [5100] = quest("La flamme sacrée", 24, 20, 331, 701, 701),                                 -- done (story root)
    [5110] = quest("Les eaux du lac Mystral", 26, 20, 331, 701, 701, nil, { 8110 }, { 5100 }),
    [5211] = quest("Le brasier de Bois-brisé", 26, 21, 331, 701, 701, { 7211 }, nil, { 5100 }),   -- to take, Mastok
    [5292] = quest("Les cendres sacrées", 27, 22, 331, 700, 701, nil, { 8212 }, { 5100 }),        -- to take, Kuray'bin
    [5205] = quest("Les estafettes d'Ashenvale", 25, 20, 331, 700, 702, { 7205 }),
    [5201] = quest("La patte d'Ursangous", 25, 20, 331, 700, 700, nil, { 8201 }),
    [5230] = quest("Le sang des furbolgs", 33, 28, 331, 700, 700, { 7230 }),
    [5180] = quest("Impasse aux Serres-Rocheuses", 27, 22, 406, 701, 670, { 7180 }),           -- to take here, work in Stonetalon
    [5107] = quest("Brouet de rat", 23, 18, 17, 622, 622, nil, { 8107 }),
    [5170] = quest("Le journal de Khazgorm", 27, 22, 17, 623, 623, nil, { 8170 }),
    [5171] = quest("Les contremaîtres de Bael'dun", 27, 22, 17, 623, 623, { 7171 }),
    [5172] = quest("Les excavateurs de Bael'dun", 27, 22, 17, 623, 623, { 7172 }),
    [5173] = quest("Le poignard de Tranchecrin", 26, 21, 17, 624, 624, nil, { 8173 }),
    [5174] = quest("Arikara", 28, 24, 400, 612, 612, nil, { 8174 }),
    [5300] = quest("Goûts changeants", 31, 24, 16732, 3368, 3368, nil, { 8300 }),               -- dungeon (zone)
    [5401] = quest("Les chardons de Durotar", 25, 20, 14, 680, 680, { 7401 }),                  -- Durotar: done
  }
  local NPCS = {
    [700] = npc("Kuray'bin", 30, 331, 73.6, 60.0), [701] = npc("Mastok", 30, 331, 73.1, 61.5), [702] = npc("Pyrotechnicien", 30, 331, 71.2, 68.0),
    [670] = npc("Courant-noir", 30, 406, 47.4, 61.0), [622] = npc("Rocheyeux", 30, 17, 51.4, 30.6),
    [623] = npc("Feegly", 30, 17, 49.0, 84.0), [624] = npc("Gardien", 30, 17, 45.0, 59.0), [612] = npc("Cerf-de-vent", 30, 400, 46.0, 51.4),
    [3368] = npc("Borstan", 30, 1637, 62.0, 31.0), [680] = npc("Gar'thok", 30, 14, 51.0, 43.0),
    [7211] = camp("Crin-de-Chardon", 26, 331, 79.0, 54.0), [7205] = camp("Estafette", 25, 331, 62.0, 52.0),
    [7230] = camp("Furbolg", 33, 331, 55.0, 32.0), [7180] = camp("Harpie", 27, 406, 52.0, 66.0),
    [7110] = npc("Puits de lune", 1, 331, 49.0, 69.0), [7201] = npc("Ursangous", 26, 331, 42.0, 66.0),
    [7212] = npc("Brasero", 1, 331, 68.0, 58.0),
    [7107] = camp("Rat fouisseur", 22, 17, 46.0, 40.0), [7170] = npc("Coffre de Khazgorm", 1, 17, 48.0, 86.0),
    [7171] = camp("Contremaître de Bael'dun", 27, 17, 50.0, 86.0), [7172] = camp("Excavateur de Bael'dun", 27, 17, 51.0, 88.0),
    [7173] = camp("Tranchecrin", 26, 17, 44.0, 60.0), [7174] = npc("Arikara", 28, 400, 38.0, 27.0),
    [7300] = npc("Raptor des fourrés", 31, 16732, 50.0, 50.0), [7401] = camp("Chardon", 25, 14, 50.0, 40.0),
  }
  local ITEMS = {
    [8110] = { name = "Fiole d'eau forte remplie", npcDrops = { 7110 } }, [8201] = { name = "Patte d'Ursangous", npcDrops = { 7201 } },
    [8212] = { name = "Cendres sacrées", npcDrops = { 7212 } }, [8107] = { name = "Rat fouisseur", npcDrops = { 7107 } },
    [8170] = { name = "Journal de Khazgorm", npcDrops = { 7170 } }, [8173] = { name = "Poignard de Tranchecrin", npcDrops = { 7173 } },
    [8174] = { name = "Peau de serpent d'Arikara", npcDrops = { 7174 } }, [8300] = { name = "Viande de raptor des fourrés", npcDrops = { 7300 } },
  }
  local XP = {}
  for id, q in pairs(QUESTS) do XP[id] = 55 * q.questLevel + q.questLevel * q.questLevel end
  if VARIANT ~= "noquestie" then
    local lib = M.InstallQuestieDB({ areaToMap = AREAS, xp = XP, quests = QUESTS, npcs = NPCS, items = ITEMS })
    lib.Support.Get("ZoneDB").private.dungeons = { [16732] = { "Site de fouilles : Les Paluns" } }
  else
    for _, n in ipairs({ "LibQuestieDB", "QuestDB", "NpcDB", "ObjectDB", "ItemDB" }) do rawset(_G, n, nil) end
    -- the game's own waypoints (C_QuestLog.GetNextWaypoint) and quest maps; the dungeon quest tagged by the client
    M.waypoints = { [5110] = { 1440, 0.49, 0.69 }, [5205] = { 1440, 0.62, 0.52 }, [5201] = { 1440, 0.42, 0.66 },
      [5230] = { 1440, 0.55, 0.32 }, [5107] = { 1413, 0.46, 0.40 }, [5170] = { 1413, 0.48, 0.86 }, [5171] = { 1413, 0.50, 0.86 },
      [5172] = { 1413, 0.51, 0.88 }, [5173] = { 1413, 0.44, 0.60 }, [5174] = { 1441, 0.38, 0.27 } }
    for _, q in ipairs(M.quests) do if q.id == 5300 then q.tag = DUNGEON end end
  end

  -- ------------------------------------------------------------------ helpers
  local function strip(s) return (tostring(s or ""):gsub("|c%x%x%x%x%x%x%x%x", ""):gsub("|r", ""):gsub("|T.-|t", "")) end
  local function exportNow() X:Rebuild(); M.Advance(8, 0.1) end
  local function replan() P:Refresh(true); M.Advance(5, 0.05) end
  local function routeQuests()
    local set = {}
    for i = P.cursor or 1, #(P.steps or {}) do
      local s = P.steps[i]
      if s.q and not s._done and not s._other then set[s.q] = true end
    end
    return set
  end
  local function stopsText(n)
    local t = {}
    for i, e in ipairs(R:Stops(64)) do
      if i > (n or 8) then break end
      t[#t + 1] = i .. ". " .. strip(e.step and P:StepLabel(e.step) or e.text)
    end
    return table.concat(t, " · ")
  end
  local function head(id) for _, l in ipairs(Z:HeadLines()) do if l.id == id then return l end end end
  local function watched()
    local out = {}
    for _, q in ipairs(M.watchOrder) do out[#out + 1] = q end
    return out
  end
  local function watchedSet() local s = {} for _, q in ipairs(watched()) do s[q] = true end return s end
  local function dumpWatch() local t = {} for _, q in ipairs(watched()) do t[#t + 1] = tostring(q) end return table.concat(t, ",") end
  local BARRENS, TN = { 5107, 5170, 5171, 5172, 5173 }, { 5174 }

  -- the game's tracker before TreeAI touches it: everything the game auto-watched when the quests were taken
  for _, q in ipairs({ 5107, 5170, 5171, 5174, 5300, 5110, 5205 }) do M.WatchQuest(q, true) end
  M.watchLog = {}
  -- (they were there before the login: no change was seen while TreeAI ran — not the player's clicks)
  for k in pairs(Z._watchPending) do Z._watchPending[k] = nil end
  Z.char.watchManual, Z.char.watchOurs = {}, {}
  -- a Blizzard user waypoint 467 m away (the diamond « 467 m » of the screenshot), super-tracked
  M.userWaypoint = { uiMapID = 1440, position = { x = 0.66, y = 0.70, GetXY = function() return 0.66, 0.70 end } }
  M.superTrackUser = true

  -- ================================================================== A. zone « Orneval » chosen
  exportNow()
  Z:PickZone(1440)
  M.Advance(4, 0.05); exportNow(); replan(); M.Advance(4, 0.5)
  local rq = routeQuests()
  check(rq[5110] and rq[5205] and rq[5201], "A: the Orneval quests of the log are in the route: " .. stopsText())
  local mixed = {}
  for _, q in ipairs(BARRENS) do if rq[q] then mixed[#mixed + 1] = q end end
  for _, q in ipairs(TN) do if rq[q] then mixed[#mixed + 1] = q end end
  check(#mixed == 0, "A: no Barrens / Thousand Needles quest mixed in (" .. table.concat(mixed, ",") .. "): " .. stopsText())
  check(not rq[5300], "A: the dungeon quest « Goûts changeants » (Questie: instanced zone; client: no tag) is never in a solo route")
  check(Z.masked and Z.masked["donjon"] and #Z.masked["donjon"] == 1 and tostring(Z:MaskedText()):find("1 de donjon", 1, true),
    "A: …and said: « " .. tostring(Z:MaskedText()) .. " »")
  local flight = false
  for _, e in ipairs(R:Stops(64)) do
    if e.kind == "fly" or e.kind == "hs" then flight = true end
    if e.step and e.step.map and Z.ZoneOf(e.step.map) ~= 1440 and not e.step.onWay then flight = true end
  end
  check(not flight, "A: no flight / hearth / stop out of Orneval (« Vol pour Poste de Librevent » was stop 2): " .. stopsText())
  local all = R:Stops(64)
  local _, total = R.Horizon(all)
  check(total <= R.HORIZON_S + 300 and R.HORIZON_S <= 3600, ("A: one hour at most (%d min)"):format(math.floor(total / 60)))
  local oz = head("zone:others")
  check(oz and strip(oz.text):find("^Autres zones : 6 quêtes %(Les Tarides, Mille pointes%)"), "A: « " .. strip(oz and oz.text) .. " »")
  check(oz and oz.onClick and tostring(oz.tooltip):find("Les Tarides : 5", 1, true), "A: the other zones, detailed in the tooltip, one click to choose")
  -- « Garde 27 quêtes, mets-en 10 de côté (9 trop dure, 1 trop loin) » counted the 4 zones: now the chosen zone, by name
  local prop
  for _, l in ipairs(R:PlanLines(P) or {}) do if strip(l.text):find("mets%-en") then prop = strip(l.text) end end
  check(not prop or prop:find("^Orneval : garde"), "A: a « garde / mets-en de côté » line names the chosen zone: « " .. tostring(prop) .. " »")
  local ptxt = R.ProposalText({ {}, {}, {} }, { { why = "trop dure" }, { why = "trop dure" }, { why = "trop loin" } }, "Orneval")
  check(ptxt == "Orneval : garde 3 quêtes, mets-en 3 de côté (2 trop dures, 1 trop loin)", "A: wording « " .. tostring(ptxt) .. " »")
  check(tostring(Z:MaskedText()):find("1 trop haute", 1, true), "A: the level-33 Orneval quest is « trop haute » (left out, said)")
  check(strip((head("zone:pick") or {}).text) == "Zone : Orneval ▾", "A: zone line « Zone : Orneval ▾ »")

  -- ================================================================== B. the quick win 65 m behind = stop 1, everywhere
  local f1 = R:Stops(1)[1]
  if VARIANT ~= "noquestie" then                         -- (quests to take are known through Questie only)
    check(f1 and f1.step and f1.step.k == "pick" and (f1.step.q == 5211 or f1.step.q == 5292),
      "B: the « ! » NPC a few steps away (Mastok / Kuray'bin) is stop 1 of the route: " .. stopsText(3))
  end
  local imp = false
  for _, e in ipairs(all) do if e.step and e.step.q == 5180 then imp = true end end
  check(not imp, "B: « Impasse aux Serres-Rocheuses » (work in Stonetalon) is not in the Orneval route")
  -- an old click on a stop (the compass point of the screenshot) does not outrank the route once that stop is done
  Cm.SetTarget(Cm, { mapID = 1440, x = 0.731, y = 0.615, label = "Prendre « Impasse aux Serres-Rocheuses »", source = "plan",
    stepKey = "pick:5180:" })
  P.navBy = "compass"
  P:Evaluate()
  Cm.Rebuild("force")
  local ct = Cm.Current()
  check(ct and ct.plan, "B: a compass point for a stop that is not in the route is dropped: the compass is back on stop 1 ("
    .. tostring(ct and ct.label) .. ")")
  -- compass = card stop 1 (same object)
  Cm.Rebuild("force")
  ct = Cm.Current()
  f1 = R:Stops(1)[1]
  check(ct and ct.plan and f1 and f1.step and ct.step == f1.step, "B: compass target == the card's stop 1 (same step): "
    .. tostring(ct and ct.label) .. " / " .. stopsText(1))
  if W and W.ShowCard then
    W.ShowCard(); W.RenderCard(true)
    local card = W.GetCard()
    local row1                                            -- the route's stop 1 (badge « 1 »), after a notice / danger row
    for _, r in ipairs(card and card.steps or {}) do
      if r:IsShown() and r.badge and r.badge.num and r.badge.num:GetText() == "1" then row1 = r break end
    end
    local t1 = row1 and strip(row1.title:GetText())
    local want = strip(W.StopItem(P, f1, 1, R:Stops(64), nil, false, 1).title)
    check(t1 and (t1 == want or t1:sub(1, 12) == want:sub(1, 12)), "B: the card says the same stop 1: « " .. tostring(t1) .. " » / « " .. want .. " »")
    -- clicking the card's stop 1 = the compass on the route (no extra manual point)
    Cm.char.custom = nil
    P:Navigate(f1.step)
    check(not Cm.char.custom and Cm.Current() and Cm.Current().plan, "B: « guider » on stop 1 = the compass follows the route, no manual point")
  end
  -- a manual click on ANOTHER stop: aimed at until that stop is done
  local st3                                               -- an objective further on (not stop 1), with a place
  for i, e in ipairs(R:Stops(64)) do
    if i > 1 and e.step and e.step.k == "obj" and e.step.x and not P:IsCurrentStop(e.step) then st3 = e.step break end
  end
  if st3 then
    P:Navigate(st3)
    Cm.Rebuild("force")
    check(Cm.char.custom and Cm.char.custom.source == "plan" and Cm.Current() and Cm.Current().kind == "custom",
      "B: a click on another stop: the compass goes there (manual target)")
    st3._done = true
    P.ReleaseNav(P)
    st3._done = nil
    Cm.Rebuild("force")
    check(not Cm.char.custom and Cm.Current() and Cm.Current().plan, "B: that stop done → back on the route's stop 1")
  end

  -- ================================================================== C. the compass never disappears for nothing
  Cm.db.shown = true
  local fr = rawget(_G, "TreeAICompassFrame")
  local st = Cm.Refresh()
  check(fr and fr:IsShown() and not st.dup and not Cm.Widgets().mini,
    "C: a Blizzard waypoint 467 m away (not our target) does NOT hide the compass")
  -- super-tracked quest whose arrow points elsewhere: unrelated too
  -- the game super-tracks the SAME quest but its arrow points elsewhere (turn-in vs objective): unrelated too
  local cur = Cm.Current()
  local curQ = cur and cur.questID
  if curQ and cur.mapID and rawget(_G, "C_SuperTrack") then
    local oldW = M.waypoints[curQ]
    M.superTrack = curQ
    M.waypoints[curQ] = { cur.mapID, cur.x > 0.5 and cur.x - 0.2 or cur.x + 0.2, cur.y }
    st = Cm.Refresh()
    check(fr:IsShown() and not st.dup, "C: the game's super-tracked quest pointing elsewhere does not hide it")
    M.waypoints[curQ] = { cur.mapID, cur.x, cur.y }
    st = Cm.Refresh()
    check(st.dup == "blizzard", "C: …but the game's arrow on the same quest AND spot = one arrow")
    M.superTrack, M.waypoints[curQ] = nil, oldW
    st = Cm.Refresh()
  end
  -- the other arrow on the SAME spot (≤ 20 m): one arrow, and the compass says why in one short line
  local t = Cm.Current()
  local CMap = rawget(_G, "C_Map")
  if t and t.mapID and CMap and CMap.HasUserWaypoint then   -- (the modern / legacy mocks have no user waypoint)
    M.userWaypoint = { uiMapID = t.mapID, position = { x = t.x + 0.0005, y = t.y, GetXY = function() return t.x + 0.0005, t.y end } }
    st = Cm.Refresh()
    local wd = Cm.Widgets()
    check(st.dup == "blizzard" and wd.mini and fr:IsShown() and fr:GetHeight() < 40 and strip(wd.tag:GetText()):find("même cible", 1, true),
      "C: the game's arrow on the same spot → compass reduced to one line saying why: « " .. strip(wd.tag:GetText()) .. " »")
    M.userWaypoint = nil
    st = Cm.Refresh()
    check(not st.dup and not Cm.Widgets().mini and fr:GetHeight() > 100, "C: the other arrow gone → the full compass is back")
  end
  local wd
  -- a replan (zone pick / Recalculer): the target stays until the new route is ready
  local before = Cm.Current()
  Z:Recalc()
  Cm.Rebuild("force")
  local during = Cm.Current()
  check(during and during.plan and not Cm.Refresh().none and fr:IsShown(), "C: during a replan the compass keeps the last stop (no blink)")
  M.Advance(5, 0.05)
  check(Cm.Current() and Cm.Current().plan, "C: after the replan, the new stop 1")
  check(before ~= nil, "C: there was a target before the replan")
  -- combat: the arrow stays, without text (option off by default)
  check(Cm.db.hideCombat == false, "C: « hide in combat » is off by default (it hid the compass at every fight)")
  M.EnterCombat(); M.FireEvent("PLAYER_REGEN_DISABLED")
  st = Cm.Refresh()
  wd = Cm.Widgets()
  check(fr:IsShown() and (wd.arrow:IsShown() or not st.rel) and not wd.dist:IsShown() and not wd.label:IsShown(),
    "C: combat: the arrow alone, no text")
  Cm.db.hideCombat = true
  Cm.Refresh()
  check(not fr:IsShown() and Cm.hideWhy == "combat", "C: …and hidden only when the player ticked « Cacher en combat »")
  Cm.db.hideCombat = false
  M.LeaveCombat(); M.FireEvent("PLAYER_REGEN_ENABLED")
  st = Cm.Refresh()
  check(fr:IsShown() and Cm.Widgets().dist:IsShown(), "C: after the fight, the texts are back")
  -- no map position (a cave): the world position is enough
  local oldMap = rawget(_G, "C_Map").GetPlayerMapPosition
  if M.flavor == "forever" then
    rawget(_G, "C_Map").GetPlayerMapPosition = function() return nil end
    st = Cm.Refresh()
    check(fr:IsShown() and st.world and st.dist, "C: no map position (cave): the compass stays, on the world position")
    rawget(_G, "C_Map").GetPlayerMapPosition = oldMap
    Cm.Refresh()
  end

  -- ================================================================== D. refresh rate: 20+ Hz arrow, 5+ Hz text while moving; nothing at rest
  Cm.SetWaypoint(80, 62.6, "Point d'essai", 1440, "perso")   -- ~330 yd east, on the same line
  Cm.Refresh()
  wd = Cm.Widgets()
  local nText, nRot = 0, 0
  local setText, setRot = wd.dist.SetText, wd.arrow.SetRotation
  wd.dist.SetText = function(self, v) nText = nText + 1 return setText(self, v) end
  wd.arrow.SetRotation = function(self, v) nRot = nRot + 1 return setRot(self, v) end
  M.FireEvent("PLAYER_STARTED_MOVING")
  for _ = 1, 20 do                                         -- 1 s of walking (7 yd/s), turning a little
    M.pos = { mapID = 1440, x = M.pos.x + 7 * 0.05 / M.mapRects[1440].w, y = M.pos.y }
    M.facing = (M.facing or 0) + 0.01
    M.Advance(0.05, 0.05)
  end
  check(nText >= 5, "D: distance / time rewritten ≥ 5 times in 1 s of walking (" .. nText .. ")")
  check(nRot >= 20, "D: arrow turned ≥ 20 times in 1 s while moving / turning (" .. nRot .. ")")
  M.FireEvent("PLAYER_STOPPED_MOVING")
  M.Advance(1, 0.05)
  check(not fr:GetScript("OnUpdate") and not Cm.Animating(), "D: standing still: no OnUpdate at all")
  nText, nRot = 0, 0
  M.Advance(3, 0.05)
  check(nText == 0 and nRot == 0, "D: …and no work (" .. nText .. " / " .. nRot .. ")")
  M.facing = M.facing + 0.5                                 -- turning in place (no event on this client): seen within 1 s
  M.Advance(1.2, 0.05)
  check(nRot > 0, "D: turning in place is seen at once (glance ≤ 1 s)")
  wd.dist.SetText, wd.arrow.SetRotation = setText, setRot
  Cm.ClearWaypoint()
  goTo(1440, 73.3, 62.6)
  M.Advance(2, 0.1)
  -- D2 (PERF4 hand-off): standing still costs nothing — not even at an « Arrivé » target (the arrow is hidden there:
  -- the glance used to believe « you turned » every second → full recompute + animation for nothing), no tab / card
  -- re-render, no route recompute; moving wakes everything at once; a 5-min heartbeat refreshes anyway
  Cm.SetWaypoint(73.3, 62.6, "Ici", 1440, "perso")
  Cm.Refresh()
  M.Advance(6, 0.1)                                         -- what the zone change started (export, replan) settles
  R:Stops(1); R:Stops(64)                                   -- (the lists as the compass and the tab show them now)
  local nRef, nLines, nRaw = 0, 0, 0
  local baseRefresh, baseLines, baseRaw = Cm.Refresh, P.Lines, R.StopsRaw
  Cm.Refresh = function(...) nRef = nRef + 1 return baseRefresh(...) end
  P.Lines = function(...) nLines = nLines + 1 return baseLines(...) end
  R.StopsRaw = function(...) nRaw = nRaw + 1 return baseRaw(...) end
  check(Cm.state and Cm.state.arrived, "D2: at the target: « Arrivé »")
  M.Advance(12, 0.1)
  check(nRef == 0 and not Cm.Animating() and not fr:GetScript("OnUpdate"),
    "D2: arrived and still for 12 s: no recompute, no animation (" .. nRef .. " refresh)")
  check(nLines == 0 and nRaw == 0, "D2: …no tab / card render, no route recompute (" .. nLines .. " / " .. nRaw .. ")")
  M.pos = { mapID = 1440, x = M.pos.x + 60 / M.mapRects[1440].w, y = M.pos.y }   -- 60 yd further (no event here)
  M.Advance(3.2, 0.1)
  check(nRef > 0 and nLines > 0 and nRaw > 0, "D2: moved: compass, tab and route follow within 3 s ("
    .. nRef .. " / " .. nLines .. " / " .. nRaw .. ")")
  M.Advance(3.5, 0.5)
  nLines = 0
  M.Advance(301, 1)
  check(nLines >= 1, "D2: still for 5 min: one heartbeat render anyway (" .. nLines .. ")")
  Cm.Refresh, P.Lines, R.StopsRaw = baseRefresh, baseLines, baseRaw
  Cm.ClearWaypoint()
  goTo(1440, 73.3, 62.6)
  M.Advance(2, 0.1)

  -- ================================================================== E. one format, no « ≈ ≈ »
  replan()
  local stAll = R:Stops(64)
  local bad
  local via = false
  for i, e in ipairs(stAll) do
    if i > 20 then break end
    local it = W.StopItem(P, e, i, stAll, nil, via, i)
    if e.kind and e.kind ~= "quest" then via = true end
    if tostring(it.sub):find("≈ ≈", 1, true) or tostring(it.title):find("≈ ≈", 1, true) then bad = it.sub end
  end
  check(not bad, "E: no « ≈ ≈ » in a stop line: " .. tostring(bad))
  if W.Open then
    W.Open("route")
    local lay = W.lastLay
    local dbl
    for _, r in ipairs(lay and lay.rects or {}) do if tostring(r.label):find("≈ ≈", 1, true) then dbl = r.label end end
    check(lay and not dbl, "E: the Trajet page has no « ≈ ≈ »: " .. tostring(dbl))
    local zl
    for _, r in ipairs(lay and lay.rects or {}) do if r.kind == "header" and r.label == "Zones à faire" then zl = r end end
    check(zl, "E: the Trajet page lists « Zones à faire »")
    W.Close()
  end

  -- ================================================================== F. route modes (per character), card header = the theme
  local mode = head("zone:mode")
  check(mode and strip(mode.text) == "Mode : Le plus rapide" and tostring(mode.tooltip):find("Par histoire", 1, true),
    "F: « Mode : Le plus rapide », the tooltip explains the five")
  mode.onClick()
  check(Z.char.style == 2 and strip(head("zone:mode").text) == "Mode : Finir la zone", "F: one click → « Finir la zone » (kept per character)")
  replan()
  check(P.route and P.route.style == "clear", "F: the route is rebuilt with the new mode")
  Z:SetStyle(3)
  replan()
  -- « Par histoire »: the three quests of « La flamme sacrée » together
  local pos = {}
  for i, s in ipairs(P.steps or {}) do
    if s.q and not s._done and not s._other and not pos[s.q] then pos[s.q] = i end
  end
  local story = { 5110, 5211, 5292 }
  if VARIANT ~= "noquestie" then
    local lastS, firstS, inS = 0, 999, 0
    for _, q in ipairs(story) do if pos[q] then inS = inS + 1; lastS = math.max(lastS, pos[q]); firstS = math.min(firstS, pos[q]) end end
    local intruder = false
    for q, i in pairs(pos) do
      local isS = false
      for _, sq in ipairs(story) do if sq == q then isS = true end end
      if not isS and i > firstS and i < lastS then intruder = true end
    end
    check(inS == 3 and not intruder, "F: « Par histoire »: the story's quests one after the other: " .. stopsText(10))
    local th = Z:ThemeParts(5110)
    check(th and th.full == "Orneval · Bois-brisé : la flamme sacrée (3 quêtes)", "F: theme « " .. tostring(th and th.full) .. " »")
  end
  if W and W.RenderCard then
    W.RenderCard(true)
    local card = W.GetCard()
    local ht, hs = strip(card.hero.title:GetText()), strip(card.hero.sub:GetText())
    check(ht:find("^Orneval") or ht:find("Bois%-brisé") or ht:find("[Ff]lamme"), "F: card header = the theme (« " .. ht .. " »)")
    check(hs:find("^Par histoire", 1, false), "F: card summary = the mode (« " .. hs .. " »)")
    card.hero:GetScript("OnClick")(card.hero, "LeftButton")
    check(Z.char.style == 4 and strip(card.hero.sub:GetText()):find("^Récompenses"), "F: one click on the card header → next mode")
  end
  Z:SetStyle(1)
  replan()

  -- ================================================================== G. « Zones à faire »
  Z.counts = nil
  Z:CountZones()
  for _ = 1, 60 do if not Z._counting then break end M.Advance(0.1, 0.05) end
  local zl = Z:ZonesToDo()
  local byName = {}
  for _, e in ipairs(zl) do byName[e.name] = e end
  check(zl[1] and zl[1].name == "Orneval" and zl[1].here, "G: my zone first")
  local tar = byName["Les Tarides"]
  check(tar and tar.inLog == 5 and not tar.done and (tar.sec or 0) > 0, "G: Les Tarides: 5 in my log, travel time known")
  local txt = tar and Z.ZoneRowText(tar)
  check(txt and txt:find("^Les Tarides · niv %d+") and txt:find("5 en cours", 1, true), "G: « " .. tostring(txt) .. " »")
  if VARIANT ~= "noquestie" then
    M.completed[5401] = true
    Z.counts = nil
    Z:CountZones()
    for _ = 1, 60 do if not Z._counting then break end M.Advance(0.1, 0.05) end
    zl = Z:ZonesToDo()
    local dur
    for _, e in ipairs(zl) do if e.name == "Durotar" then dur = e end end
    check(dur and dur.done and select(1, Z.ZoneRowText(dur)):find("déjà finie", 1, true), "G: Durotar: « déjà finie »")
  end
  Z:PickZone(1413)
  replan()
  rq = routeQuests()
  check(rq[5107] and rq[5171] and not rq[5110], "G: a click on « Les Tarides » = the route there")
  Z:PickZone(1440)
  replan()

  -- ================================================================== H. the game's tracker = the route's quests, in order
  local AW = Z.AutoWatchOn and Z:AutoWatchOn()
  check(AW, "H: « Suivi auto par zone » on by default (mode sûr)")
  M.Advance(4, 0.5)
  local ws = watchedSet()
  local want = Z:WatchWanted(X.LogSnapshot()) or {}
  local okW = #want > 0
  for _, q in ipairs(want) do if not ws[q] then okW = false end end
  for q in pairs(ws) do
    local inW = false
    for _, w in ipairs(want) do if w == q then inW = true end end
    if not inW then okW = false end
  end
  check(okW and #watched() <= 6, "H: tracker = the route's quests (" .. dumpWatch() .. ")")
  check(not ws[5107] and not ws[5170] and not ws[5171] and not ws[5174] and not ws[5300], "H: other zones and the dungeon quest left the tracker")
  -- the player tracks a Barrens quest by hand: kept
  M.WatchQuest(5172, true)
  M.FireEvent("ZONE_CHANGED_NEW_AREA")
  M.Advance(4, 0.5)
  check(watchedSet()[5172] and Z.char.watchManual[5172] == true, "H: a quest tracked by hand stays (" .. dumpWatch() .. ")")
  -- …and unwatches an Orneval quest by hand: never put back
  local w1 = want[1]
  M.WatchQuest(w1, false)
  M.FireEvent("ZONE_CHANGED_NEW_AREA")
  M.Advance(4, 0.5)
  check(not watchedSet()[w1] and Z.char.watchManual[w1] == false, "H: a quest removed by hand is not put back (" .. dumpWatch() .. ")")
  -- the game auto-watches a quest just taken in another zone: TreeAI takes it out again
  M.quests[#M.quests + 1] = { id = 5179, title = "Les plaines dorées", level = 26, map = 1413, objectives = { obj("Herbe dorée : 0/5", 0, 5, "item") } }
  M.WatchQuest(5179, true)                                -- the objective tracker (autoQuestWatch) runs first
  M.FireEvent("QUEST_ACCEPTED", 5179)
  M.Advance(4, 0.5)
  check(not watchedSet()[5179] and Z.char.watchManual[5179] == nil, "H: the game's auto-watch of an other-zone quest is undone (" .. dumpWatch() .. ")")
  -- combat: nothing touched until the fight ends
  M.watchLog = {}
  M.WatchQuest(5179, true)
  M.FireEvent("QUEST_WATCH_UPDATE", 5179)                 -- the game re-adds it (objective progress)
  M.EnterCombat(); M.FireEvent("PLAYER_REGEN_DISABLED")
  M.FireEvent("ZONE_CHANGED_NEW_AREA")
  M.Advance(4, 0.5)
  local inFight = 0
  for _, e in ipairs(M.watchLog) do if e.combat and e.id ~= 5179 then inFight = inFight + 1 end end
  check(inFight == 0 and watchedSet()[5179], "H: in combat nothing is changed (queued)")
  M.LeaveCombat(); M.FireEvent("PLAYER_REGEN_ENABLED")
  M.Advance(4, 0.5)
  check(not watchedSet()[5179], "H: …done right after the fight")
  -- throttle: at most one pass every 2 s
  Z._watchAt = nil
  local n1 = Z:WatchPass()
  local n2 = Z:WatchPass()
  check(n1 ~= nil and n2 == nil, "H: one pass at most every 2 s")
  -- off: nothing touched any more
  Z:SetAutoWatch(false)
  M.watchLog = {}
  M.WatchQuest(5107, true)
  M.FireEvent("ZONE_CHANGED_NEW_AREA")
  M.Advance(4, 0.5)
  check(#M.watchLog == 1 and watchedSet()[5107], "H: option off → TreeAI does not touch the tracker")
  Z:SetAutoWatch(true)
  M.Advance(4, 0.5)

  -- ================================================================== I. the hub toggle
  local H = T.Hub
  if H and H.Entries then
    local e
    for _, x in ipairs(H.Entries()) do if x.id == "suiviauto" then e = x end end
    check(e and tostring(e.title):find("Suivi auto par zone : oui", 1, true), "I: hub: « Suivi auto par zone : oui »")
  end

  -- ================================================================== J. dungeons (JOURNAL contracts, 4.0.1)
  -- a quest of a dungeon (T.Dungeon.QuestDungeon: Forever's dungeon quest list) never in a solo / duo route; a quest
  -- of a dungeon already done (T.Dungeon.IsDone) never at all — a ready turn-in stays; neither in the game's tracker.
  -- Feature-detected: stubs here (the module may not offer them yet), the real ones restored after.
  local D = T.Dungeon
  if type(D) == "table" then
    local oldQD, oldDone = rawget(D, "QuestDungeon"), rawget(D, "IsDone")
    local BFD = { key = "bfd", n = "Profondeurs de Brassenoire" }
    local RFK = { key = "rfk", n = "Kraal de Tranchebauge" }
    D.QuestDungeon = function(q)
      q = tonumber(q)
      return (q == 5201 and BFD) or (q == 5205 and RFK) or (oldQD and oldQD(q)) or nil
    end
    D.IsDone = function(d)
      if d == RFK then return true, { src = "run" } end
      if d == BFD then return false end
      if oldDone then return oldDone(d) end
      return false
    end
    M.FireEvent("QUEST_LOG_UPDATE")
    exportNow(); replan(); M.Advance(4, 0.5)
    local rq = routeQuests()
    check(not rq[5201] and not rq[5205], "J: a dungeon quest (Dungeon.QuestDungeon) and a done dungeon's quest left the route: "
      .. stopsText(6))
    local log = X.LogSnapshot()
    local ok1, w1 = Z:Accept(5201, nil, log[5201], 28, 1)
    check(not ok1 and w1 == "donjon", "J: solo: « donjon » (" .. tostring(w1) .. ")")
    Z.db.dungeons = true
    local ok2 = Z:Accept(5201, nil, log[5201], 28, 3)
    local ok3, w3 = Z:Accept(5205, nil, log[5205], 28, 3)
    check(ok2 and not ok3 and w3 == "donjon fait",
      "J: 3 in the group, dungeon quests on: the dungeon's quest yes, the done dungeon's never (" .. tostring(w3) .. ")")
    Z.db.dungeons = false
    check(Z:Accept(5205, nil, { done = 1 }, 28, 1), "J: …a done dungeon's quest ready to turn in stays (turned in outside)")
    check(Z.DungeonDone(5205) and not Z.DungeonDone(5201), "J: Z.DungeonDone")
    check(not watchedSet()[5205] and not watchedSet()[5201], "J: neither is in the game's tracker (" .. dumpWatch() .. ")")
    check(tostring(Z:MaskedText()):find("1 d'un donjon déjà fait", 1, true), "J: said: " .. tostring(Z:MaskedText()))
    D.QuestDungeon, D.IsDone = oldQD, oldDone
    M.FireEvent("QUEST_LOG_UPDATE")
    exportNow(); replan(); M.Advance(4, 0.5)
    check(routeQuests()[5205], "J: (stubs gone: the quest is back in the route)")
  end

  -- ================================================================== K. the compass / route journal (real-game trace)
  -- a ring of the last 300 decisions (≤ 30 KB) in TreeAIDB.diag.nav, written on state changes only and out of combat;
  -- in /tai erreurs and in the window (Outils → « Journal de la boussole »)
  local NTr = T.NavTrace
  check(NTr and NTr.Lines, "K: the journal exists")
  if NTr and NTr.Lines then
    local function has(pat) for _, l in ipairs(NTr.Lines()) do if l:find(pat) then return l end end end
    check(has("cible · .+ → .+ %(") , "K: target changes are logged with their reason (" .. tostring(has("cible ·")) .. ")")
    check(has("trajet · refait %(.-%) : %d+ arrêt"), "K: replans: trigger, stops, first stop (" .. tostring(has("trajet · refait")) .. ")")
    check(has("zone Orneval") or VARIANT == "noquestie", "K: …and the chosen zone")
    check(has("boussole · affichée") and (has("boussole · en ligne courte") or M.flavor == "legacy"),
      "K: hide / show with the reason")                     -- (no Blizzard waypoint / super-track on a classic client)
    -- a walk of 4 s: the update rate actually achieved
    Cm.SetWaypoint(80, 62.6, "Point d'essai", 1440, "perso")
    Cm.Refresh()
    M.FireEvent("PLAYER_STARTED_MOVING")
    for _ = 1, 80 do
      M.pos = { mapID = 1440, x = M.pos.x + 7 * 0.05 / M.mapRects[1440].w, y = M.pos.y }
      M.Advance(0.05, 0.05)
    end
    M.FireEvent("PLAYER_STOPPED_MOVING")
    M.Advance(1, 0.05)
    local rate = has("marche · .- en mouvement : flèche (%d+) Hz, texte (%d+) Hz")
    local fa, ta = tostring(rate or ""):match("flèche (%d+) Hz, texte (%d+) Hz")
    check(rate and tonumber(fa) >= 20 and tonumber(ta) >= 4, "K: the achieved rate while moving (" .. tostring(rate) .. ")")
    Cm.ClearWaypoint()
    -- predicted vs actual of a timed trip (Temps de trajet)
    local Nv = T.NavTime
    if Nv and Nv.Arrive then
      Nv.Arrive({ t0 = (GetTime() or 0) - 60, pred = 50, crow0 = 300, map = 1440, mode = "w", src = "modèle", idle = 0,
        moving = 0, combat = true })
      check(has("temps · trajet de 274 m : prévu 50 s, réel 1 min 00 %(%+20 %%%)"), "K: predicted vs actual (" .. tostring(has("temps ·")) .. ")")
    end
    -- in combat: kept in memory, written once the fight is over
    local stored = #NTr.Store().lines
    M.EnterCombat(); M.FireEvent("PLAYER_REGEN_DISABLED")
    NTr.Log("cible", "essai en combat")
    check(#NTr.Store().lines == stored and NTr.Pending() == 1, "K: in combat nothing is written (pending)")
    M.LeaveCombat(); M.FireEvent("PLAYER_REGEN_ENABLED")
    M.Advance(1, 0.5)
    check(NTr.Pending() == 0 and has("essai en combat %(combat%)"), "K: …written after the fight")
    -- the ring: 300 lines, 30 KB at most
    for i = 1, 420 do NTr.Log("trajet", ("ligne d'essai n° %d %s"):format(i, string.rep("x", 90))) end
    local st = NTr.Store()
    local bytes = 0
    for _, l in ipairs(st.lines) do bytes = bytes + #l + 1 end
    check(#st.lines <= 300 and bytes <= 30000 and st.lines[#st.lines]:find("n° 420", 1, true),
      "K: ring capped (" .. #st.lines .. " lines, " .. bytes .. " bytes)")
    -- /tai erreurs and the window
    local rep = T.Bugs and T.Bugs.Collect and T.Bugs.Collect()
    local inRep = false
    for _, l in ipairs(rep and rep.lines or {}) do if l:find("== Trajet / boussole", 1, true) then inRep = true end end
    check(inRep, "K: in the /tai erreurs report")
    check(W and W.pageIndex and W.pageIndex.navlog, "K: window page « Journal de la boussole »")
    local hubOk = false
    for _, e in ipairs(T.HUB_EXTRA or {}) do if e.id == "navlog" then hubOk = true end end
    check(hubOk, "K: Outils → « Journal de la boussole »")
    NTr.Clear()
  end

  -- leave the shared mock as found
  M.userWaypoint, M.superTrackUser = nil, nil
  rawset(_G, "UnitFactionGroup", function() return "Alliance", "Alliance" end)
end
