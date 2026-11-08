-- brain_scenario.lua (BRAIN 4.0.2) — « Pilote auto » on the real addon, forever + modern (+ legacy). Fake names only.
-- Level-28 Horde hunter (orc), quest log loaded, alone then in a duo then a group of 3.
--  A. context: idle / quest, taxi (card notice « à l'arrivée »), dead, ghost, dungeon, city (errands), afk, PvP danger,
--     grinding, level-up, bags / ammo / repair / rested / log flags ; T.Brain.Context() contract.
--  B. park: grey (off-route from the Journal facts, on-route judged by the pilot), elite solo (« trop dure »), other
--     continent ; never: finished, freshly accepted, progressed, aimed by the player ; watch list untracked then
--     restored ; « En pause » section + « Pilote auto » page ; unpark when the reason disappears (group of 3, progress,
--     the player re-watches it) ; undo / « Reprendre » = the player wins for the session ; rule off = everything back.
--  C. zone pick when the route is nearly empty (announce, undo, the player's own change wins) ; route mode « Groupe »
--     with ZonePlan modes (duo → Groupe, disband → back, player change wins) ; errands (urgent repair → a stop in the
--     route at a known NPC, StopDone on arrival, undo) ; hearth advice ; « Suivre le groupe » (Group.Intent contract) ;
--     master switch off = everything given back. Zero blocked action, nothing in the chat, no Lua error.
return function(M, ctx)
  local T = TreeAI
  local check = ctx.check
  local B, W, P, Z = T.Brain, T.Window, T.Planner, T.ZonePlan
  check(B and B.Context and B.Parked and B.ParkedList and B.Undo and B.Resume, "Brain contracts: Context, Parked, ParkedList, Undo, Resume")
  check(B and B._active, "Pilote auto on by default (safe module)")
  check(T.SAFE_MODULES.Brain, "Brain is a safe-mode module")
  check(W and W.pageIndex and W.pageIndex.brain, "Window: « Pilote auto » page registered")
  if not (B and B._active and W and P and Z) then return end
  local flavor = M.flavor
  local modern = flavor ~= "legacy"
  M.LeaveCombat()
  M.RunSlash("/tai module Planner off")   -- the route is set by hand below (Planner:Evaluate still runs)
  local v0, chat0 = #M.violations, #M.output
  local oldFaction = UnitFactionGroup
  UnitFactionGroup = function() return "Horde", "Horde" end
  M.player.race, M.player.raceFile, M.player.level = "Orc", "Orc", 28
  M.group = {}
  M.mapRects[1413] = { top = 1000, left = -2000, w = 4000, h = 3000, continent = 1 }
  M.mapNames[1413] = "Les Tarides"
  M.mapRects[1454] = { top = 900, left = 2500, w = 1000, h = 800, continent = 0 }
  M.mapNames[1454] = "Orgrimmar"
  -- (4.0.3 integ) maps read before these rectangles (the storm, the merged modules): read again ; storm toggles undone
  if T.Geo and type(T.Geo.rects) == "table" then T.Geo.rects[1413], T.Geo.rects[1454] = nil, nil end
  if T.settings then T.settings.debug = false end
  M.pos = { mapID = 1424, x = 0.5, y = 0.4 }
  -- the game's watch list (mock, ZONEFIX: C_QuestLog / classic by index + QUEST_WATCH_LIST_CHANGED, like the client)
  local hasWatch = M.WatchQuest ~= nil and M.watchOrder ~= nil
  local function watchedSet()
    local s = {}
    for _, q in ipairs(M.watchOrder or {}) do s[q] = true end
    return s
  end
  -- the « Suivi auto par zone » (ZoneWatch) keeps the watch list after the route ; a pass now (2 s gap skipped)
  local function zonePass()
    if Z.WatchPass then Z._watchAt = nil Z:WatchPass() end
  end
  local function forgetManual()
    if Z.char then Z.char.watchManual, Z.char.watchOurs = {}, {} end
    if type(Z._watchPending) == "table" then for k in pairs(Z._watchPending) do Z._watchPending[k] = nil end end
  end
  local function setLog()
    M.quests = {
      { header = "Contreforts de Hautebrande" },
      { id = 7001, title = "Vieille corvée", level = 18, xp = 120, map = 1424, objectives = { { text = "Bric-à-brac : 1/6", have = 1, need = 6, type = "item" } } },
      { id = 7002, title = "Le chef ogre", level = 29, tag = 1, xp = 2400, map = 1424, objectives = { { text = "Chef ogre tué : 0/1", have = 0, need = 1 } } },
      { id = 7003, title = "Livraison urgente", level = 28, xp = 1800, map = 1424, complete = true, objectives = { { text = "Colis : 1/1", have = 1, need = 1, done = true } } },
      { id = 7004, title = "Plumes de harpie", level = 27, xp = 2100, map = 1424, objectives = { { text = "Plume de harpie : 2/8", have = 2, need = 8, type = "item" } } },
      { header = "Les Tarides" },
      { id = 7005, title = "Courrier de l'Ouest", level = 26, xp = 1500, map = 1413, objectives = { { text = "Lettre : 0/1", have = 0, need = 1, type = "item" } } },
    }
    if hasWatch then
      M.watchOrder = {}
      for _, id in ipairs({ 7001, 7002, 7003, 7004, 7005 }) do M.WatchQuest(id, true) end
      forgetManual()                       -- seeded by the test: not the player's hand
    end
    M.waypoints = { [7003] = { 1424, 0.51, 0.41 }, [7005] = { 1413, 0.4, 0.4 }, [7002] = { 1424, 0.7, 0.7 } }
  end
  setLog()
  M.FireEvent("QUEST_LOG_UPDATE")
  local Q = T.modules.Quests
  if Q and Q.Update then Q.Update() end
  -- the route (by hand): 7004 here (2 stops), 7001 on the way (grey), the turn-in of 7003
  local function setRoute(extra)
    local steps = {
      { k = "obj", q = 7004, o = 1, map = 1424, x = 55, y = 45 },
      { k = "obj", q = 7001, o = 1, map = 1424, x = 60, y = 50 },
      { k = "turn", q = 7003, map = 1424, x = 51, y = 41 },
      { k = "turn", q = 7004, map = 1424, x = 50, y = 40 },
    }
    for _, s in ipairs(extra or {}) do steps[#steps + 1] = s end
    P.auto = { id = "auto", title = "Trajet TreeAI", steps = steps, minutes = 20 }
    P.steps, P.key = nil, nil
    P:Evaluate()
  end
  setRoute()
  local S = B._S
  local function settle(sec) M.Advance(sec or 4, 0.5) end
  local function pass()
    local Ab = T.Abandon
    if Ab and Ab.Compute then Ab.Compute() end
    return B.ParkPass()
  end
  local function onRoute(q)
    for i = P.cursor or 1, #(P.steps or {}) do local s = P.steps[i] if tonumber(s.q) == q and not s._done and not s._other then return true end end
    return false
  end
  local function noticeText()
    local e = (T.GetSuggestions and T:GetSuggestions()) or {}
    for _, s in ipairs(e) do if s.id == "Brain:card" then return s.text, s end end
    return nil
  end

  -- ===================================================================== A. contexte
  B.UpdateContext()
  local c = B.Context()
  check(c.main == "quest", "context: route with a current stop → « quest » (" .. tostring(c.main) .. ")")
  check(type(c.flags) == "table" and c.flags.group == 1, "context flags: alone")
  -- taxi : « En vol » + avis « à l'arrivée » (genre « always » : jamais mis en attente en vol)
  M.onTaxi = true
  M.FireEvent("PLAYER_CONTROL_LOST")
  settle(2)
  check(B.Context().main == "taxi", "context: on a taxi → « taxi » (" .. tostring(B.Context().main) .. ")")
  local nt = noticeText()
  check(nt and nt:find("^En vol : à l'arrivée, ") ~= nil, "taxi: card notice « En vol : à l'arrivée, … » (" .. tostring(nt) .. ")")
  check(W.CARD_NOTICE["Brain:card"] and W.CARD_NOTICE["Brain:card"][1] == nt, "taxi: the notice is a card notice row")
  M.onTaxi = false
  M.FireEvent("PLAYER_CONTROL_GAINED")
  settle(2)
  check(B.Context().main == "quest", "context: landed → « quest »")
  check(noticeText() == nil, "landed: taxi notice gone")
  -- mort / fantôme : rien n'est décidé
  M.player.dead = true
  M.FireEvent("PLAYER_DEAD")
  settle(1)
  check(B.Context().main == "dead", "context: dead")
  M.player.dead, M.player.ghost = false, true
  M.FireEvent("PLAYER_ALIVE")
  settle(1)
  check(B.Context().main == "ghost", "context: ghost (" .. tostring(B.Context().main) .. ")")
  M.player.ghost = false
  M.FireEvent("PLAYER_UNGHOST")
  settle(1)
  -- donjon
  M.instance = true
  M.FireEvent("PLAYER_ENTERING_WORLD")
  settle(1)
  check(B.Context().main == "dungeon", "context: in a dungeon")
  M.instance = false
  M.FireEvent("PLAYER_ENTERING_WORLD")
  settle(1)
  -- absent
  M.player.afk = true
  M.FireEvent("PLAYER_FLAGS_CHANGED", "player")
  settle(1)
  check(B.Context().main == "afk", "context: AFK")
  M.player.afk = false
  M.FireEvent("PLAYER_FLAGS_CHANGED", "player")
  settle(1)
  -- JcJ en zone contestée (drapeau, pas un contexte)
  M.player.pvp = true
  M.zonePvP = "contested"
  M.FireEvent("PLAYER_FLAGS_CHANGED", "player")
  settle(1)
  check(B.Context().flags.pvp == true, "flag: PvP-flagged in a contested zone")
  M.player.pvp = false
  M.FireEvent("PLAYER_FLAGS_CHANGED", "player")
  settle(1)
  check(B.Context().flags.pvp == false, "flag: PvP off")
  -- à farmer : 3 combats en 5 min, aucune quête avancée, pas d'arrêt à moins de 400 yd
  local stepsKeep = P.steps
  P.steps = {}
  for _ = 1, 3 do M.EnterCombat() M.FireEvent("PLAYER_REGEN_DISABLED") settle(1) M.LeaveCombat() M.FireEvent("PLAYER_REGEN_ENABLED") settle(3) end
  S.progressAt = nil
  B.UpdateContext()
  check(B.Context().main == "grind", "context: grinding (" .. tostring(B.Context().main) .. ")")
  P.steps = stepsKeep
  S.fights = {}
  B.UpdateContext()
  -- drapeaux : repos, journal, sacs pleins, munitions basses, réparation
  M.player.rested = 12000
  M.FireEvent("UPDATE_EXHAUSTION")
  settle(5)
  check(B.Context().flags.rested == true and B.Context().flags.restedXP == 12000, "flag: rested XP")
  -- (legacy mock : GetNumQuestLogEntries counts the headers twice ; the real Classic API gives the quests 2nd)
  check((B.Context().flags.log == 5 or (not modern and B.Context().flags.log == 7)) and B.Context().flags.logFull == false,
    "flag: log 5/" .. tostring(B.Context().flags.logMax) .. " (" .. tostring(B.Context().flags.log) .. ")")
  -- ville : Orgrimmar = « En ville » (+ courses)
  M.pos = { mapID = 1454, x = 0.5, y = 0.5 }
  M.FireEvent("ZONE_CHANGED_NEW_AREA")
  settle(1)
  check(B.Context().main == "city" and tostring(B.Context().label):find("Orgrimmar", 1, true), "context: in a capital (" .. tostring(B.Context().label) .. ")")
  M.pos = { mapID = 1424, x = 0.5, y = 0.4 }
  M.FireEvent("ZONE_CHANGED_NEW_AREA")
  settle(1)
  -- niveau pris
  M.player.level = 29
  M.FireEvent("PLAYER_LEVEL_UP", 29, 100, 50, 0, 1, 0, 0, 0, 0)
  settle(1)
  check(B.Context().flags.levelUp == true, "flag: level-up just happened")
  check(B.char.train == 29, "level 29: trainer pending (odd level, no price read: not a need yet)")
  M.player.level = 28
  B.char.train = false

  -- ===================================================================== B. quêtes en pause
  for q in pairs(B.char.parked) do B._Unpark(q, "test", false, true) end   -- the passes of part A may have parked some
  local n = pass()
  local p1, p2, p3, p5 = B.Parked(7001), B.Parked(7002), B.Parked(7003), B.Parked(7005)
  check(p1 and p1.k == "grey", "grey quest ON the route parked by the pilot (" .. tostring(p1 and p1.k) .. ")")
  check(p1 and tostring(p1.t):find("grise", 1, true), "grey: the game's fact as the reason (" .. tostring(p1 and p1.t) .. ")")
  check(p2 and p2.k == "hard" and tostring(p2.t):find("élite", 1, true), "elite alone: « trop dure » (" .. tostring(p2 and p2.t) .. ")")
  check(p5 and p5.k == "far" and tostring(p5.t):find("continent", 1, true), "other continent parked (" .. tostring(p5 and p5.t) .. ")")
  check(not p3, "a finished quest is never parked")
  check(not B.Parked(7004), "a normal quest of the route stays")
  check(n >= 3, "park pass: changes counted (" .. tostring(n) .. ")")
  P:Evaluate()
  local zw = Z.AutoWatchOn and Z:AutoWatchOn()
  if hasWatch then
    if zw then zonePass() end              -- auto watch by zone: it follows the route, the parked quests leave it
    local w = watchedSet()
    check(not w[7001] and not w[7002] and not w[7005] and w[7004], "parked quests left the game's watch list (" .. (zw and "auto watch by zone" or "the pilot") .. ")")
    if not zw then check(B.char.parked[7001].w == true, "the pilot remembers it removed the watch") end
  end
  check(not onRoute(7001), "the parked grey quest left the route (ZonePlan rules)")
  local ok7004 = onRoute(7004)
  check(ok7004, "7004 still on the route")
  -- page « Pilote auto » + section « En pause » de la page Quêtes
  W.Open("brain")
  local rows, hero = {}, nil
  for _, r in ipairs(W.lastLay.rects) do
    if r.kind == "hero" then hero = r end
    rows[#rows + 1] = r.kind .. ":" .. r.label
  end
  local all = table.concat(rows, "\n")
  check(hero and hero.label:find("Pilote auto", 1, true), "page: hero « Pilote auto · … »")
  check(all:find("header:En pause", 1, true) and all:find("Vieille corvée", 1, true) and all:find("Le chef ogre", 1, true),
    "page: « En pause » lists the parked quests")
  check(all:find("header:Décisions", 1, true) and all:find("en pause", 1, true), "page: « Décisions » with the time and the reason")
  W.char.collapsed["q:brain"] = false
  W.Open("quests")
  local qrows = {}
  for _, r in ipairs(W.lastLay.rects) do qrows[#qrows + 1] = r.kind .. ":" .. r.label end
  check(table.concat(qrows, "\n"):find("En pause (pilote auto)", 1, true), "Quests page: « En pause (pilote auto) » section")
  W.Close()
  -- avis de la carte : une fois
  B.UpdateNotice()
  local an = noticeText()
  check(an and an:find("quêtes en pause", 1, true), "card notice: « Pilote auto : N quêtes en pause » (" .. tostring(an) .. ")")
  -- jamais : prise à l'instant, avancée, visée à la main
  M.quests[#M.quests + 1] = { id = 7006, title = "Écailles anciennes", level = 17, xp = 90, map = 1424, objectives = { { text = "Écaille : 0/5", have = 0, need = 5, type = "item" } } }
  M.FireEvent("QUEST_ACCEPTED", 7006)
  M.FireEvent("QUEST_LOG_UPDATE")
  if Q and Q.Update then Q.Update() end
  pass()
  check(not B.Parked(7006), "a quest accepted 0 s ago is never parked (wanted)")
  M.quests[#M.quests + 1] = { id = 7007, title = "Course contre la montre", level = 16, xp = 80, map = 1424, timer = { 900, 100 },
    objectives = { { text = "Fiole : 0/1", have = 0, need = 1, type = "item" } } }
  M.FireEvent("QUEST_LOG_UPDATE")
  if Q and Q.Update then Q.Update() end
  pass()
  check(not B.Parked(7007), "a timed quest (chrono running) is never parked, even grey")
  -- reprise quand la raison disparaît : groupe de 3 → l'élite revient
  M.group = { { name = "Ami", class = "WARRIOR", level = 28, online = true }, { name = "Amie", class = "PRIEST", level = 28, online = true } }
  M.FireEvent("GROUP_ROSTER_UPDATE")
  pass()
  check(not B.Parked(7002), "elite: back when the group is 3 (unpark)")
  local last = B.Decisions(3)
  local found = false
  for _, e in ipairs(last) do if tostring(e.t):find("Le chef ogre", 1, true) and tostring(e.t):find("vous êtes 3", 1, true) then found = true end end
  check(found, "decision log: « « Le chef ogre » reprise : vous êtes 3 maintenant »")
  if hasWatch then
    setRoute({ { k = "obj", q = 7002, o = 1, map = 1424, x = 70, y = 70 } })   -- the rebuilt route takes it (group of 3)
    if zw then zonePass() end
    check(watchedSet()[7002], "unparked: back in the watch list")
    setRoute()
  end
  M.group = {}
  M.FireEvent("GROUP_ROSTER_UPDATE")
  pass()
  check(not B.Parked(7002), "alone again at once: no back-and-forth (calm 10 min)")
  S.unparkedAt[7002].t = S.unparkedAt[7002].t - 601
  pass()
  check(B.Parked(7002), "alone again 10 min later: the elite goes back in pause")
  -- quête de donjon : en pause seul (à 5), reprise à 4 + « Groupe de 4 : Kraal… possible » (clic : prêt ?)
  M.quests[#M.quests + 1] = { id = 1102, title = "Un ancien mal", level = 29, tag = 81, xp = 3000, map = 1424,
    objectives = { { text = "Chef tué : 0/1", have = 0, need = 1 } } }
  M.FireEvent("QUEST_LOG_UPDATE")
  if Q and Q.Update then Q.Update() end
  pass()
  local pd = B.Parked(1102)
  check(pd and pd.k == "hard" and tostring(pd.t):find("groupe de 5", 1, true), "dungeon quest alone: in pause (" .. tostring(pd and pd.t) .. ")")
  M.group = { { name = "Ami", class = "WARRIOR", level = 28, online = true }, { name = "Amie", class = "PRIEST", level = 28, online = true },
    { name = "Camarade", class = "MAGE", level = 28, online = true } }
  M.FireEvent("GROUP_ROSTER_UPDATE")
  S.announce = nil
  pass()
  check(not B.Parked(1102), "group of 4: the dungeon quest is back")
  check(S.announce and S.announce.kind == "dungeon" and tostring(S.announce.title):find("Groupe de 4", 1, true),
    "group of 4: « Groupe de 4 : <donjon> possible » (" .. tostring(S.announce and S.announce.title) .. ")")
  check(S.announce and type(S.announce.fn) == "function", "the dungeon notice opens « Prêt pour le donjon ? » on a click")
  M.group = {}
  M.FireEvent("GROUP_ROSTER_UPDATE")
  table.remove(M.quests)
  M.FireEvent("QUEST_REMOVED", 1102)
  M.FireEvent("QUEST_LOG_UPDATE")
  if Q and Q.Update then Q.Update() end
  pass()
  -- tu avances une quête en pause : elle revient
  M.FireEvent("QUEST_WATCH_UPDATE", modern and 7002 or 3)   -- classic clients: the quest log index
  pass()
  check(not B.Parked(7002), "progress on a parked quest: unparked (« tu y avances »)")
  -- annuler : le choix du joueur gagne (session)
  local e5
  for _, e in ipairs(B.Decisions(30)) do if e.u and e.u.kind == "park" and e.u.q == 7005 and not e.undone then e5 = e end end
  check(e5, "the park of 7005 is in the log with an undo")
  check(e5 and T:UserAction(function() B.Undo(e5.id) end) and not B.Parked(7005), "undo: 7005 unparked")
  pass()
  check(not B.Parked(7005), "undone: never parked again this session (the player wins)")
  check(B.Overrides().keep[7005], "override kept for the session")
  -- « Reprendre » (bouton de la page)
  check(B.Resume(7001) and not B.Parked(7001), "« Reprendre » : 7001 back")
  pass()
  check(not B.Parked(7001), "« Reprendre » : the player wins (not re-parked)")
  -- suivie à nouveau à la main : elle revient
  B.Overrides().keep[7001] = nil
  S.progress[7002] = nil
  pass()
  if hasWatch then
    check(B.Parked(7001), "7001 parked again (override cleared for the test)")
    P:Evaluate()
    if zw then zonePass() end
    M.Advance(2, 0.5)                      -- far from any game event: a change of the player's hand
    M.WatchQuest(7001, true)
    M.Advance(1, 0.5)
    if zw then zonePass() end              -- the auto watch classifies it (watchManual = true)
    pass()
    check(not B.Parked(7001), "the player re-watched it by hand: unparked (" .. (zw and "watchManual" or "pilot") .. ")")
    check(B.Overrides().keep[7001], "re-watched by hand: kept for the session")
    -- the same without the auto watch by zone: the pilot keeps the watch list itself
    if zw and Z.SetAutoWatch then
      Z:SetAutoWatch(false)
      B.Overrides().keep[7001] = nil
      forgetManual()
      S.progress[7001] = nil
      pass()
      local p = B.Parked(7001)
      check(p and not watchedSet()[7001] and p.w == true, "auto watch off: the pilot removed the watch itself")
      check(B.Resume(7001) and watchedSet()[7001], "auto watch off: « Reprendre » puts the watch back")
      B.Overrides().keep[7001] = nil
      pass()
      check(B.Parked(7001) and not watchedSet()[7001], "auto watch off: parked again")
      M.Advance(2, 0.5)
      M.WatchQuest(7001, true)
      check(not B.Parked(7001), "auto watch off: re-watched by hand → unparked at once")
      Z:SetAutoWatch(true)
    end
  end
  -- règle coupée : tout revient
  B.SetRule("park", false)
  settle(2)
  check(#B.ParkedList() == 0, "rule « park » off: every quest back")
  B.SetRule("park", true)
  settle(4)

  -- ===================================================================== C. zone, mode, courses, foyer, groupe
  -- C1. zone : trajet presque vide, une zone voisine pleine (comptage ZonePlan) → choisie, annoncée, annulable
  Z.char.mode, Z.char.zones = "auto", {}
  B.char.pick = false
  B.Overrides().zone = nil
  Z.counts = { lvl = 28, n = { [1416] = 12, [1424] = 1, [1413] = 30 } }
  local keepAuto = P.auto
  P.auto = { id = "auto", title = "Trajet TreeAI", steps = { { k = "turn", q = 7003, map = 1424, x = 51, y = 41 } }, minutes = 2 }
  P.steps, P.key = nil, nil
  P:Evaluate()
  check(B.ZonePass() == 0 and Z.char.mode == "auto", "zone: one turn-in left on the route → stay (never leave a turn-in behind)")
  P.auto = { id = "auto", title = "Trajet TreeAI", steps = {}, minutes = 0 }
  P.steps, P.key = nil, nil
  P:Evaluate()
  local zr = B.ZonePass()
  check(zr == 1 and Z.char.mode == "zones" and Z.char.zones[1416], "zone: nothing left on the route → Alterac picked (same continent ; Kalimdor never)")
  check(B.char.pick and B.char.pick.map == 1416, "zone: the pilot remembers its pick")
  B.UpdateNotice()
  local zn = noticeText()
  check(zn and zn:find("Zone choisie", 1, true), "zone: announced once on the card (" .. tostring(zn) .. ")")
  local ez
  for _, e in ipairs(B.Decisions(5)) do if e.u and e.u.kind == "zone" then ez = e end end
  check(ez and tostring(ez.w):find("Plus rien à faire sur ton trajet", 1, true), "zone: why = « Plus rien à faire sur ton trajet ; … » (" .. tostring(ez and ez.w) .. ")")
  check(ez and B.Undo(ez.id) and Z.char.mode == "auto", "zone undo: back to « auto »")
  check(B.ZonePass() == 0 and Z.char.mode == "auto", "zone undone: the player wins (no new pick this session)")
  -- le joueur choisit lui-même : jamais touché
  B.Overrides().zone = nil
  Z.char.mode, Z.char.zones = "zones", { [1424] = true }
  check(B.ZonePass() == 0 and Z.char.zones[1424] and not Z.char.zones[1416], "zone chosen by the player: never changed")
  Z.char.mode, Z.char.zones = "auto", {}
  -- choix du pilote changé à la main ensuite → il gagne
  B.ZonePass()
  check(B.char.pick, "zone picked again")
  Z.char.zones = { [1424] = true }
  B.ZonePass()
  check(not B.char.pick and B.Overrides().zone, "pilot's zone changed by hand: the player's choice wins (session)")
  Z.char.mode, Z.char.zones = "auto", {}
  P.auto = keepAuto
  P.steps, P.key = nil, nil
  P:Evaluate()
  -- C2. mode du trajet (modes de ZonePlan, ZONEFIX) : duo → « Groupe », groupe dissous → mode d'avant, le joueur gagne
  local real = type(Z.STYLES) == "table" and Z.SetStyle and Z.StyleIndex
  local saved = { Z.STYLES, Z.StyleIndex, Z.SetStyle, Z.StyleName }
  if not real then                                        -- ZonePlan sans modes : mêmes contrats simulés
    local styleI = 1
    Z.STYLES = { { id = "fast", name = "Le plus rapide" }, { id = "clear", name = "Finir la zone" }, { id = "group", name = "Groupe" } }
    Z.StyleIndex = function() return styleI end
    Z.SetStyle = function(_, i) styleI = i return true end
    Z.StyleName = function(_, i) return Z.STYLES[i or styleI].name end
  end
  local function idx(id) for i, e in ipairs(Z.STYLES) do if e.id == id then return i end end end
  local function cur() return Z.STYLES[Z:StyleIndex()].id end
  Z:SetStyle(idx("fast"))
  B.char.style = false
  B.Overrides().style = nil
  -- (4.0.3 integ, ZONEFIX by) « Le plus rapide » chosen explicitly by the player is kept even when a group forms
  if real then
    check(Z.char.styleBy == "player", "ZONEFIX: Z:SetStyle(i) without « by » = the player's choice (styleBy)")
    M.group = { { name = "Ami", class = "WARRIOR", level = 28, online = true } }
    check(B.StylePass() == 0 and cur() == "fast", "style: the player's explicit « Le plus rapide » is never replaced")
    M.group = {}
    Z.char.styleBy = nil                                  -- back to « never chosen » (the default mode)
  end
  check(B.StylePass() == 0 and cur() == "fast", "style: alone → nothing")
  M.group = { { name = "Ami", class = "WARRIOR", level = 28, online = true } }
  check(B.StylePass() == 1 and cur() == "group", "style: duo → « Groupe » (" .. cur() .. ")")
  check(not real or Z.char.styleBy == "brain", "ZONEFIX: the pilot's mode is marked styleBy = brain")
  M.group = {}
  check(B.StylePass() == 1 and cur() == "fast", "style: group disbanded → back to « Le plus rapide »")
  M.group = { { name = "Ami", class = "WARRIOR", level = 28, online = true } }
  B.StylePass()
  Z:SetStyle(idx("clear"))                                -- the player picks « Finir la zone » himself
  check(B.StylePass() == 0 and cur() == "clear" and B.Overrides().style, "style changed by hand: the player wins")
  M.group = {}
  check(B.StylePass() == 0 and cur() == "clear", "style: the player's choice kept when the group disbands")
  Z:SetStyle(idx("fast"))
  if real then Z.char.styleBy = nil end
  B.Overrides().style = nil
  if not real then Z.STYLES, Z.StyleIndex, Z.SetStyle, Z.StyleName = saved[1], saved[2], saved[3], saved[4] end
  -- C3. courses : équipement à 5 % → réparer, urgent ; un réparateur connu à 60 yd → un arrêt en tête du trajet
  M.inventory[5] = { id = 6085, dur = 3, durMax = 60 }
  local D = T.Detect
  local oldServices = D and D.Services
  if D then
    D.Services = function(kind)
      if kind == "repair" or kind == "vendor" then return { { kind = "repair", name = "Forgeron Testeur", mapID = 1424, x = 0.52, y = 0.41, dist = 60 } } end
      return {}
    end
  end
  B.ReadNeeds()
  local needs = B.Needs()
  check(needs[1] and needs[1].key == "repair" and needs[1].urgent, "needs: repair, urgent (5 %)")
  B.ErrandPass()
  local stop
  for _, s in ipairs(P.steps or {}) do if s.brain == "errand:repair" and not s._done then stop = s end end
  check(stop and tostring(stop.label):find("Répare", 1, true) and tostring(stop.label):find("Forgeron Testeur", 1, true),
    "errand: « Répare … (Forgeron Testeur) » inserted in the route (" .. tostring(stop and stop.label) .. ")")
  check(stop and P.steps[P.cursor] == stop, "urgent errand: first stop of the route")
  B.UpdateNotice()
  local un = noticeText()
  check(un and un:find("Répare", 1, true), "urgent need: one notice line (" .. tostring(un) .. ")")
  -- arrivée au réparateur : l'arrêt est fait
  M.pos = { mapID = 1424, x = 0.52, y = 0.41 }
  P:Evaluate()
  check(stop and stop._done, "errand stop done on arrival (≤ 40 yd)")
  M.pos = { mapID = 1424, x = 0.5, y = 0.4 }
  -- annuler une course : retirée, pas reproposée de la session
  B.char.stops = {}
  S.needsSig, S.readSig = "", ""
  B.ErrandPass()
  local ee
  for _, e in ipairs(B.Decisions(5)) do if e.u and e.u.kind == "errand" and not e.undone then ee = e end end
  check(ee and B.Undo(ee.id), "errand undo")
  B.ErrandPass()
  local again = false
  for _, s in ipairs(B.char.stops) do if not s.done then again = true end end
  check(not again, "errand undone: not added again this session")
  M.inventory[5] = { id = 6085, dur = 60, durMax = 60 }
  B.ReadNeeds()
  B.ErrandPass()
  if D then D.Services = oldServices end
  -- C4. pierre de foyer : 2 quêtes finies à rendre près de l'auberge, plus rien ici
  local R = T.Route
  local oldHR, oldBP = R.HearthReady, R.BindPlace
  R.HearthReady = function() return true end
  R.BindPlace = function() return { map = 1416, x = 40, y = 40, name = "Auberge-Test" } end
  M.quests = { { header = "Montagnes d'Alterac" },
    { id = 7101, title = "Rapport au capitaine", level = 27, xp = 1700, map = 1416, complete = true, objectives = {} },
    { id = 7102, title = "Colis pour l'auberge", level = 27, xp = 1600, map = 1416, complete = true, objectives = {} } }
  M.waypoints = { [7101] = { 1416, 0.41, 0.40 }, [7102] = { 1416, 0.40, 0.41 } }
  M.FireEvent("QUEST_LOG_UPDATE")
  P.auto = { id = "auto", title = "Trajet TreeAI", steps = {}, minutes = 0 }
  P.steps, P.key = nil, nil
  P:Evaluate()
  B.HearthPass()
  if modern then   -- the turn-in place comes from C_QuestLog.GetNextWaypoint (absent on classic clients: no advice)
    check(S.hearth and S.hearth.turns == 2, "hearth: 2 turn-ins near the inn, nothing left here (" .. tostring(S.hearth and S.hearth.turns) .. ")")
    S.announce = nil
    B.UpdateNotice()
    local hn = noticeText()
    check(hn and hn:find("Pierre de foyer", 1, true), "hearth: notice « Pierre de foyer : 2 quêtes à rendre à … » (" .. tostring(hn) .. ")")
  else
    check(not S.hearth, "hearth: no turn-in place known on a classic client → no advice (true data only)")
  end
  -- quelque chose à faire ici : pas de pierre de foyer
  P.auto = { id = "auto", title = "Trajet TreeAI", steps = { { k = "obj", q = 7004, o = 1, map = 1424, x = 55, y = 45 } }, minutes = 5 }
  P.steps, P.key = nil, nil
  setLog()
  M.FireEvent("QUEST_LOG_UPDATE")
  P:Evaluate()
  B.HearthPass()
  check(not S.hearth, "hearth: work left here → no advice")
  R.HearthReady, R.BindPlace = oldHR, oldBP
  -- C5. suivre le groupe (contrat Group.Intent) : coupé par défaut, puis une zone voulue par le groupe
  local Gm = T.modules.Group
  if Gm then
    local wasActive, realIntent = Gm._active, Gm.Intent
    Gm._active = true
    Gm.Intent = function() return { kind = "zone", map = 1416, label = "on va dans l'Alterac", by = "Ami", conf = 0.8, at = 42 } end
    check(B.FollowPass() == 0, "follow: off by default (opt-in)")
    B.SetRule("follow", true)
    S.lastIntent = nil
    check(B.FollowPass() == 1 and Z.char.mode == "zones" and Z.char.zones[1416], "follow on: the group's zone is followed")
    check(B.FollowPass() == 0, "follow: the same intent is applied once")
    local ef
    for _, e in ipairs(B.Decisions(3)) do if e.k == "follow" then ef = e end end
    check(ef and tostring(ef.t):find("Ami", 1, true), "follow: logged with who said it")
    check(Z.char.zoneBy == nil or Z.char.zoneBy == "brain", "ZONEFIX: the zone picked by the pilot is marked zoneBy = brain")
    Gm.Intent = realIntent
    -- C5b. (4.0.3 integ) the real GROUPJOIN adapter: GroupJoin's inferred intent → Group.Intent() + GROUP_INTENT
    local J = T.GroupJoin
    if type(realIntent) == "function" and J and J.BrainIntent and J.NoteIntent then
      Z.char.mode, Z.char.zones = "auto", {}
      B.char.pick = false
      local JS, oldList = J._state, Gm.List
      Gm.List = function() return { { me = true }, { name = "Ami" } } end
      check(J.BrainIntent({ key = "wait:Ami", kind = "wait", name = "Ami", conf = 0.9 }) == nil, "Group.Intent: a pause is not a destination (nil)")
      local dg = J.BrainIntent({ key = "dg:WC", kind = "dg", name = "Cavernes", conf = 0.7, dungeon = { key = "WC", n = "Cavernes" },
        target = { map = 1413, x = 46, y = 36.4 } })
      check(dg and dg.kind == "dungeon" and dg.dungeon == "WC" and dg.point and dg.point.x == 46 and dg.map, "Group.Intent: dungeon → key + entrance point (0–100)")
      local got = {}
      T:On("GROUP_INTENT", got, function(_, _, x) got[#got + 1] = x or false end)
      JS.intent = { key = "z:1413", kind = "z", name = "Les Tarides", zone = 1413, conf = 0.85, by = "Ami", src = "chat" }
      JS.inferAt = GetTime()                              -- fresh inference: Intent() does not re-read the group
      J.NoteIntent(JS.intent, GetTime())
      J.NoteIntent(JS.intent, GetTime())
      local it = Gm.Intent(Gm)
      check(#got == 1 and type(got[1]) == "table" and got[1].kind == "zone" and got[1].map == 1413,
        "GROUP_INTENT fired once when the intent key changes (" .. #got .. ")")
      check(it and it.kind == "zone" and it.map == 1413 and it.by == "Ami" and it.src == "chat" and it.at == got[1].at,
        "Group.Intent() → { kind = zone, map, by, src, at (stable) }")
      B.SetRule("follow", true)
      S.lastIntent = nil
      check(B.FollowPass() == 1 and Z.char.mode == "zones" and Z.char.zones[1413], "follow (real adapter): the group's zone is followed")
      check(B.FollowPass() == 0, "follow (real adapter): applied once")
      J.NoteIntent(nil)
      check(#got == 2 and got[2] == false, "GROUP_INTENT fired again when the intent is gone")
      T:Off("GROUP_INTENT", got)
      JS.intent, JS.inferAt = nil, nil
      Gm.List = oldList
    end
    B.SetRule("follow", false)
    Gm.Intent = realIntent
    Gm._active = wasActive
    Z.char.mode, Z.char.zones = "auto", {}
    B.char.pick = false
  end
  -- C6. interrupteur général : couper rend tout
  S.unparkedAt, S.progress = {}, {}                       -- (10 min plus tard : plus de période calme)
  pass()
  local np = #B.ParkedList()
  check(np >= 1, "before switching off: quests in pause (" .. np .. ")")
  T:SetModuleEnabled("Brain", false)
  check(not B._active and #B.ParkedList() == 0, "pilot off: every quest back, nothing in pause")
  check(B.Parked(7002) == nil, "pilot off: Parked() answers nil (ZonePlan keeps everything)")
  check(noticeText() == nil, "pilot off: no notice")
  T:SetModuleEnabled("Brain", true)
  settle(6)
  check(B._active, "pilot back on")

  -- ===================================================================== bilan
  check(B.errors == 0, "no error in the pilot's passes (" .. tostring(B.lastError) .. ")")
  check(#M.violations == v0, "zero violation / blocked action (" .. (#M.violations - v0) .. ")")
  local leaked = {}
  for i = chat0 + 1, #M.output do leaked[#leaked + 1] = M.output[i] end
  check(#leaked == 0, "nothing in the chat: " .. table.concat(leaked, " | "))
  if hasWatch then
    local combatWatch = false
    for _, w in ipairs(M.watchLog or {}) do if w.combat then combatWatch = true end end
    check(not combatWatch, "watch list never touched in combat")
  end
  UnitFactionGroup = oldFaction
end
