-- crowd_phases.lua — (5.0 STABLE) the player's three real-game complaints, replayed as phases of
-- `session_sim.lua --crowd` (4 TreeAI clients, the main one = level-30 Horde hunter in a group of 4):
--   « ça bugue quand il y a beaucoup de joueurs »  → ville (capital, 40 nameplates streaming in / out, mouseover +
--     unit tooltips, target changes, chat, other addons' messages, roster bursts), hub (quest hub: quest mobs on the
--     nameplates, party progress), bg (battleground-like: in combat, 40 enemy players, UNIT_* storms, raid of 10)
--   « quand les quêtes s'actualisent »             → quetes (kills, party progress, accept / turn-in / watch bursts)
--   « quand je loot »                              → butin (auto-loot, AoE loot, group loot, vendor, craft, item info)
--   + login (the storm after the loading screen) and repos (nothing happens: TreeAI must do nothing).
-- Event volumes are those of a real Classic-family client (one image = every event the game sends in it, then the
-- timers and OnUpdate): NAME_PLATE_UNIT_ADDED / REMOVED ≈ 8 / s each while walking through a capital, each new unit
-- with its UNIT_AURA / UNIT_TARGET / UNIT_NAME_UPDATE; UNIT_HEALTH / UNIT_POWER_UPDATE / UNIT_AURA up to ≈ 100 / s in a
-- 40-player fight; one mob looted = LOOT_READY, LOOT_OPENED, per item LOOT_SLOT_CLEARED + CHAT_MSG_LOOT + ITEM_PUSH +
-- BAG_UPDATE (+ quest events for a quest item), BAG_UPDATE_DELAYED per image, LOOT_CLOSED, PLAYER_MONEY; one kill =
-- 3-4 QUEST_LOG_UPDATE + UNIT_QUEST_LOG_CHANGED + QUEST_WATCH_UPDATE; CHAT_MSG_ADDON of every other addon's prefix.
-- Measured per phase: TreeAI work of every image (kilo-instructions of addon code and of the API it calls; 50 = 1 ms),
-- who pays the worst image (dispatch context + function), totals by context. Gate (session_sim): no image over 4 ms,
-- nothing at rest. Fake names only.
return function(X)
  local opts, main, order, fire, tick, FR, STEP = X.opts, X.main, X.order, X.fire, X.tick, X.FR, X.STEP
  local M = main.M
  local env = main.env
  local FPS = math.max(1, math.floor(1 / STEP + 0.5))
  local LIMIT = 200          -- kinstr of TreeAI work in one image = 4 ms
  local REST_KINSTR_S = 8    -- « rien au repos » : ≤ 8 kinstr / s (0,16 ms per second, group of 4 kept in sync) for the whole addon

  -- deterministic random (never the addon's math.random stream)
  local seed = 20261113
  local function rnd(n) seed = (seed * 1103515245 + 12345) % 2147483648 return (math.floor(seed / 65536) % n) + 1 end
  local acc = {}
  local function rate(key, perSec)   -- how many of these events fall in this image
    local a = (acc[key] or 0) + perSec * STEP
    local n = math.floor(a)
    acc[key] = a - n
    return n
  end
  local function ev(...) return fire(main, ...) end

  -- -v: who reads the whole quest log (C_QuestLog.GetInfo: a fresh table of ~30 fields per quest, every time)
  local logReads = {}
  if opts.verbose then
    local QL = rawget(env, "C_QuestLog")
    for _, fname in ipairs({ "GetInfo", "GetQuestObjectives" }) do
      local f = QL and QL[fname]
      if f then
        QL[fname] = function(...)
          local T1 = rawget(env, "TreeAI")
          local who = T1 and rawget(T1, "_ctx") or "-"
          local key = fname .. "  " .. tostring(who)
          logReads[key] = (logReads[key] or 0) + 1
          return f(...)
        end
      end
    end
  end
  -- ------------------------------------------------------------------ phases + statistics
  local phases, cur = {}, nil
  -- -v: memory allocated per dispatched call (exclusive), by module + job — who feeds the garbage collector
  local allocBy, aStack = {}, {}
  if opts.verbose and main.T and main.T._SetRunProbe then
    main.T._SetRunProbe(function(owner, ctx, begin)
      if not FR.open then return end
      local kb = collectgarbage("count") - (FR.hookKB or 0)
      if begin then
        local top = aStack[#aStack]
        if top then top.own = top.own + (kb - top.at) end
        aStack[#aStack + 1] = { at = kb, own = 0, key = (cur and cur.name or "-") .. "  " .. tostring(type(owner) == "table" and owner.name or "Core") .. "  " .. tostring(ctx or "-") }
      else
        local e = table.remove(aStack)
        if e then
          allocBy[e.key] = (allocBy[e.key] or 0) + e.own + (kb - e.at)
          local top = aStack[#aStack]
          if top then top.at = kb end
        end
      end
    end)
  end
  local scripts = {}
  local function spawn(fn) scripts[#scripts + 1] = coroutine.create(fn) end
  local function wait(n) for _ = 1, (n or 1) do coroutine.yield() end end
  local perFrame
  FR.inbox = {}
  FR.onFrame = function(fr, kb)
    local p = cur
    if not p then fr.dbN = 0 for k in pairs(fr.dbBy or {}) do fr.dbBy[k] = nil end return end
    local api = math.floor((fr.apiUs or 0) * 0.05 + 0.5)   -- widget work of the game (µs → kinstr)
    local c = fr.own + api
    p.api, p.apiN = p.api + api, p.apiN + (fr.apiN or 0)
    p.dbN = (p.dbN or 0) + (fr.dbN or 0)
    if (fr.dbN or 0) > (p.dbMax or 0) then p.dbMax = fr.dbN end
    fr.dbN = 0
    local dbTop
    for k, v in pairs(fr.dbBy or {}) do
      p.dbBy = p.dbBy or {}
      p.dbBy[k] = (p.dbBy[k] or 0) + v
      if c > p.worst then dbTop = dbTop or {} dbTop[#dbTop + 1] = { k = k, n = v } end
      fr.dbBy[k] = nil
    end
    if dbTop then table.sort(dbTop, function(a, b) return a.n > b.n end) p.worstDb = dbTop elseif c > p.worst then p.worstDb = nil end
    p.akb = p.akb + (fr.addonKB or 0)
    p.n = p.n + 1
    p.sum = p.sum + c
    p.costs[#p.costs + 1] = c
    p.kb = p.kb + math.max(0, kb or 0)
    if p.restFrom and p.n > p.restFrom then
      p.restSum = (p.restSum or 0) + c p.restN = (p.restN or 0) + 1
      p.restApi = (p.restApi or 0) + (fr.apiN or 0) p.restKB = (p.restKB or 0) + (fr.addonKB or 0)
      p.restCtx = p.restCtx or {}
      for k, v in pairs(fr.ctx) do p.restCtx[k] = (p.restCtx[k] or 0) + v end
      if c > 0 then p.restBusy = (p.restBusy or 0) + 1 end
      p.restLine = p.restLine or {}
      for mk, byLine in pairs(fr.mods) do
        if mk ~= "(mock/sim)" then for line, v in pairs(byLine) do if v > 0 then local k = mk .. ":" .. line p.restLine[k] = (p.restLine[k] or 0) + v end end end
      end
    end
    for k, v in pairs(fr.ctx) do p.byCtx[k] = (p.byCtx[k] or 0) + v end
    for mk, byLine in pairs(fr.mods) do
      if mk ~= "(mock/sim)" then
        local s = 0
        for _, v in pairs(byLine) do s = s + v end
        if s > 0 then p.byMod[mk] = (p.byMod[mk] or 0) + s end
      end
    end
    if c > LIMIT then p.over = p.over + 1 end
    if c > p.worst then
      p.worst = c
      local top, ct = {}, {}
      for mk, byLine in pairs(fr.mods) do
        if mk ~= "(mock/sim)" then for line, v in pairs(byLine) do if v > 0 then top[#top + 1] = { k = mk .. ":" .. line, n = v } end end end
      end
      for k, v in pairs(fr.ctx) do ct[#ct + 1] = { k = k, n = v } end
      table.sort(top, function(a, b) return a.n > b.n end)
      table.sort(ct, function(a, b) return a.n > b.n end)
      for i = #top, 7, -1 do top[i] = nil end
      for i = #ct, 6, -1 do ct[i] = nil end
      p.worstTop, p.worstCtx, p.worstAt, p.worstApi = top, ct, p.n, api
    end
  end
  FR.driver = function()
    -- TreeAI messages of the 3 friends, delivered inside an image (as the game does)
    local inbox = FR.inbox
    if #inbox > 0 then
      FR.inbox = {}
      for _, m in ipairs(inbox) do X.setCur(main.name) M.DeliverAddon(m[1], m[2], m[3], m[4]) end
    end
    if perFrame then
      local ok, err = X.BASE.pcall(perFrame)
      if not ok then X.addErr(X.R.errors, "sim", "crowd " .. tostring(cur and cur.name), err) end
    end
    local i = 1
    while i <= #scripts do
      local co = scripts[i]
      X.setCur(main.name)
      local ok, err = coroutine.resume(co)
      if not ok then X.addErr(X.R.errors, "sim", "crowd script " .. tostring(cur and cur.name), err) end
      if coroutine.status(co) == "dead" then table.remove(scripts, i) else i = i + 1 end
    end
  end
  local only
  if opts.phases then only = {} for w in tostring(opts.phases):gmatch("[^,]+") do only[w] = true end end
  local function phase(name, seconds, setup, frameFn, teardown, rest)
    if only and not only[name] then return end
    local p = { name = name, n = 0, sum = 0, worst = 0, over = 0, costs = {}, byCtx = {}, byMod = {}, kb = 0, sec = seconds, api = 0, apiN = 0, akb = 0,
      restFrom = rest and math.floor(rest * FPS) or nil }
    phases[#phases + 1] = p
    X.setCur(main.name)
    if setup then
      -- the setup's own events belong to the first image of the phase
      local s = setup
      spawn(function() s() end)
    end
    perFrame = frameFn
    cur = p
    for s = 1, seconds do
      if X.friendLife and not rest then X.friendLife(math.floor(X.simSeconds())) end
      tick(1)
    end
    perFrame = nil
    for i = #scripts, 1, -1 do scripts[i] = nil end
    cur = nil
    if teardown then X.setCur(main.name) teardown() tick(1) end
  end

  -- ------------------------------------------------------------------ world: level-30 hunter, 14 quests, QuestieDB
  local AREA, MAP = 267, 1424
  local function obj(text, have, need, typ) return { text = ("%s : %d/%d"):format(text, have, need), label = text, have = have, need = need, done = have >= need, type = typ or "monster" } end
  local LOG = {
    { id = 5201, title = "Les ours des contreforts", level = 29, xp = 2500, mob = "Ours gris des contreforts", npc = 7201, objectives = { obj("Ours gris des contreforts tué", 3, 12) } },
    { id = 5202, title = "Les couguars affamés", level = 30, xp = 2700, mob = "Couguar affamé", npc = 7202, objectives = { obj("Couguar affamé tué", 0, 10) } },
    { id = 5203, title = "Soupe de tortue", level = 28, xp = 2300, mob = "Tortue hargneuse", npc = 7203, item = 20101, objectives = { obj("Viande de tortue", 2, 10, "item") } },
    { id = 5204, title = "Le syndicat doit payer", level = 30, xp = 2900, mob = "Voleur du Syndicat", npc = 7204, objectives = { obj("Voleur du Syndicat tué", 5, 15), obj("Guetteur du Syndicat tué", 1, 8) } },
    { id = 5205, title = "Les mines d'Azurelode", level = 29, xp = 2600, mob = "Mineur de Hautebrande", npc = 7205, item = 20102, objectives = { obj("Minerai marqué", 0, 8, "item") } },
    { id = 5206, title = "Un sergent disparu", level = 31, xp = 3000, complete = true, objectives = { obj("Insigne du sergent", 1, 1, "item") } },
    { id = 5207, title = "Le pont de Thoradin", level = 31, xp = 3100, objectives = { obj("Mur de Thoradin exploré", 0, 1, "event") } },
    { id = 5208, title = "Potions pour le front", level = 28, xp = 2200, item = 20103, objectives = { obj("Mousse des marais", 4, 6, "item") } },
    { id = 5209, title = "Le fermier inquiet", level = 27, xp = 2000, complete = true, objectives = { obj("Paysan interrogé", 4, 4) } },
    { id = 5210, title = "Les murlocs de la côte", level = 30, xp = 2800, mob = "Murloc de la côte", npc = 7210, item = 20104, objectives = { obj("Nageoire de murloc", 1, 9, "item"), obj("Murloc de la côte tué", 2, 10) } },
    { id = 5211, title = "Le chef des ogres", level = 32, xp = 3400, elite = true, mob = "Chef ogre Muk", npc = 7211, objectives = { obj("Chef ogre Muk tué", 0, 1) } },
  }
  local function quest(name, ql, rl, giver, ender, kills, items, extra)
    local q = { name = name, questLevel = ql, requiredLevel = rl, zoneOrSort = AREA, startedBy = { { giver } }, finishedBy = { { ender } },
      objectives = { kills and kills or nil, nil, items and items or nil } }
    for k, v in pairs(extra or {}) do q[k] = v end
    return q
  end
  local function camp(name, lv, cx, cy, rank)
    local s = {}
    for i = 0, 11 do s[#s + 1] = { cx + (i % 4) * 1.2 - 1.8, cy + math.floor(i / 4) * 1.5 - 1.5 } end
    return { name = name, minLevel = lv, maxLevel = lv + 1, rank = rank or 0, spawns = { [AREA] = s } }
  end
  local function npc1(name, lv, x, y, rank) return { name = name, minLevel = lv, maxLevel = lv, rank = rank or 0, spawns = { [AREA] = { { x, y } } } } end
  local QDB = { quests = {}, npcs = {}, items = {}, areaToMap = { [AREA] = MAP, [36] = 1416 }, xp = {} }
  do
    local givers = { 7001, 7002, 7003, 7004 }
    QDB.npcs[7001] = npc1("Apothicaire Lydon", 35, 61.5, 19.2) QDB.npcs[7002] = npc1("Grand exécuteur Darthalia", 40, 62.3, 20.3)
    QDB.npcs[7003] = npc1("Gardien de la mort Samsa", 35, 62.1, 19.7) QDB.npcs[7004] = npc1("Marchand de bétail", 30, 60.2, 20.9)
    -- the sim's own five quests (session_sim QUESTS)
    QDB.npcs[7101] = camp("Loup-garou galeux", 26, 40, 46) QDB.npcs[7102] = camp("Sanglier des collines", 25, 44, 50)
    QDB.npcs[7103] = camp("Bandit de Syndicat", 27, 48, 40) QDB.npcs[7104] = camp("Araignée des bois", 28, 52, 56)
    QDB.npcs[7105] = camp("Ogre d'Alterac", 29, 56, 30)
    QDB.items[20001] = { name = "Croc de loup-garou", npcDrops = { 7101 } }
    QDB.quests[5101] = quest("Les loups-garous de la forêt", 26, 22, 7001, 7001, nil, { 20001 })
    QDB.quests[5102] = quest("Sangliers des collines", 25, 21, 7002, 7002, { 7102 })
    QDB.quests[5103] = quest("Le marchand disparu", 27, 23, 7003, 7003, { 7103 })
    QDB.quests[5104] = quest("Les nids d'araignées", 28, 24, 7003, 7003, { 7104 })
    QDB.quests[5105] = quest("Le courrier d'Alterac", 29, 25, 7004, 7004, { 7105 })
    for i, q in ipairs(LOG) do
      if q.npc then QDB.npcs[q.npc] = camp(q.mob, q.level - 1, 30 + (i * 7) % 40, 25 + (i * 11) % 50, q.elite and 1 or 0) end
      if q.item then QDB.items[q.item] = { name = q.objectives[1].label, npcDrops = q.npc and { q.npc } or nil } end
      QDB.quests[q.id] = quest(q.title, q.level, q.level - 5, givers[1 + i % 4], givers[1 + (i + 1) % 4],
        (q.npc and not q.item) and { q.npc } or nil, q.item and { q.item } or nil)
    end
    -- 24 quests to take around (some grey, too high, chains)
    for i = 1, 24 do
      local id = 5300 + i
      local n = 7300 + i
      QDB.npcs[n] = camp("Rôdeur n°" .. i, 24 + i % 9, 20 + (i * 13) % 60, 15 + (i * 17) % 70)
      QDB.quests[id] = quest("Mission de test " .. i, 24 + i % 10, 18 + i % 12, givers[1 + i % 4], givers[1 + i % 4], { n }, nil,
        (i % 6 == 0) and { preQuestSingle = { id - 1 } } or nil)
    end
    -- the rest of the world, as the real database has it (thousands of quests and creatures elsewhere)
    for a = 1, 40 do QDB.areaToMap[3000 + a] = 1500 + a end
    for i = 1, 3200 do
      local area = 3000 + 1 + i % 40
      local n = 30000 + i
      QDB.npcs[n] = { name = "Créature lointaine " .. i, minLevel = 1 + i % 60, maxLevel = 2 + i % 60, rank = 0,
        spawns = { [area] = { { 10 + i % 80, 10 + (i * 7) % 80 }, { 12 + i % 80, 14 + (i * 7) % 80 } } } }
      local q = quest("Quête lointaine " .. i, 1 + i % 60, math.max(1, i % 60 - 4), n, n, { n })
      q.zoneOrSort = area
      QDB.quests[40000 + i] = q
    end
    for id, q in pairs(QDB.quests) do QDB.xp[id] = 55 * q.questLevel + q.questLevel * q.questLevel end
  end
  local EXTRA_ITEMS = {
    [20101] = { name = "Viande de tortue", questItem = true, stack = 20, classID = 12 },
    [20102] = { name = "Minerai marqué", questItem = true, stack = 20, classID = 12 },
    [20103] = { name = "Mousse des marais", questItem = true, stack = 20, classID = 12 },
    [20104] = { name = "Nageoire de murloc", questItem = true, stack = 20, classID = 12 },
    [20110] = { name = "Étoffe de laine", stack = 20, sellPrice = 33, classID = 7, subclassID = 5 },
    [20111] = { name = "Cuir moyen", stack = 20, sellPrice = 50, classID = 7, subclassID = 6 },
    [20112] = { name = "Minerai de fer", stack = 10, sellPrice = 150, classID = 7, subclassID = 7 },
    [20113] = { name = "Ceinture de bandit", quality = 2, sellPrice = 410, classID = 4, subclassID = 2, equipLoc = "INVTYPE_WAIST", ilvl = 28, minLevel = 23 },
    [20114] = { name = "Arc long du rôdeur", quality = 2, sellPrice = 1210, classID = 2, subclassID = 2, equipLoc = "INVTYPE_RANGED", ilvl = 30, minLevel = 25 },
    [20115] = { name = "Gants de mailles usés", quality = 0, sellPrice = 91, classID = 4, subclassID = 3, equipLoc = "INVTYPE_HAND", ilvl = 24 },
    [20116] = { name = "Dent fendue", quality = 0, stack = 5, sellPrice = 22, classID = 15 },
    [20117] = { name = "Plume ébouriffée", quality = 0, stack = 5, sellPrice = 9, classID = 15 },
    [20118] = { name = "Recette : ragoût de tortue", quality = 1, sellPrice = 100, classID = 9, subclassID = 5 },
    [20119] = { name = "Potion de soins inférieure", stack = 5, sellPrice = 25, classID = 0, subclassID = 1, minLevel = 13 },
    [20120] = { name = "Épaulières du trappeur", quality = 3, sellPrice = 2100, classID = 4, subclassID = 3, equipLoc = "INVTYPE_SHOULDER", ilvl = 31, minLevel = 26 },
  }
  local function world(c, isMain)
    local W = c.M
    for id, t in pairs(EXTRA_ITEMS) do
      local copy = {}
      for k, v in pairs(t) do copy[k] = v end
      W.AddItem(id, copy)
    end
    X.setCur(c.name)
    local lib = W.InstallQuestieDB(QDB)
    if isMain then
      -- the real QuestieDB library decodes its compressed rows and returns COPIES: a read costs far more than a table
      -- lookup. Estimated 12 µs per read, 30 µs when it returns a table (spawns, objectives) — counted as TreeAI work.
      for _, ent in ipairs({ lib.Quest, lib.Npc, lib.Object, lib.Item }) do
        local get = ent.Get
        ent.Get = function(id, key)
          local v = get(id, key)
          if FR.open and (FR.depth or 0) > 0 then
            local us = type(v) == "table" and 30 or 12
            FR.apiUs = (FR.apiUs or 0) + us
            FR.virtUs = (FR.virtUs or 0) + us
            FR.dbN = (FR.dbN or 0) + 1
            -- who reads: the dispatch context, else the addon function that asked (through Compass's adapter)
            local T1 = rawget(env, "TreeAI")
            local who = T1 and rawget(T1, "_ctx")
            if type(who) ~= "string" or who:find("après 0 s", 1, true) then
              local lvl, last = 3, nil
              while lvl < 14 do
                local inf = debug.getinfo(lvl, "S")
                if not inf then break end
                local m = inf.source:find("TreeAI", 1, true) and inf.source:match("([%w_]+)%.lua$")
                if m and m ~= "Core" and m ~= "Compass" then last = m .. ":" .. inf.linedefined break end
                lvl = lvl + 1
              end
              who = (type(who) == "string" and (who .. " ") or "") .. (last or "?")
            end
            FR.dbBy = FR.dbBy or {}
            FR.dbBy[who] = (FR.dbBy[who] or 0) + 1
          end
          return v
        end
      end
    end
    for _, q in ipairs(LOG) do
      local e = { id = q.id, title = q.title, level = q.level, xp = q.xp, complete = q.complete or false, objectives = {}, elite = q.elite }
      for i, o in ipairs(q.objectives) do e.objectives[i] = { text = o.text, label = o.label, have = o.have, need = o.need, done = o.done, type = o.type } end
      if isMain or (q.id % 2 == c.idx % 2) then W.quests[#W.quests + 1] = e end
    end
    if isMain then
      -- bags of a level-30 hunter: 5 bags mostly used (ammo, food, potions, cloth, leather, junk, quest items, greens)
      local fill = { 20110, 20111, 20112, 20116, 20117, 20119, 4540, 1179, 929, 3530, 2672, 2287, 4865, 20113, 20115, 20118 }
      local k = 0
      for b = 0, 4 do
        local bag = W.bags[b]
        if bag then
          if b >= 1 then bag.size = 14 end
          for s = 1, bag.size - 3 do
            if not bag.slots[s] then k = k + 1 local id = fill[1 + k % #fill] W.SetBagItem(b, s, id, 1 + (k % (W.items[id].stack or 1))) end
          end
        end
      end
      X.addToBags(W, 20101, 2) X.addToBags(W, 20103, 4) X.addToBags(W, 20104, 1)
    end
    W.FireEvent("QUEST_LOG_UPDATE")
  end
  for _, c in ipairs(order) do world(c, c.main) end

  -- ------------------------------------------------------------------ units
  local CLS = { "WARRIOR", "PRIEST", "MAGE", "ROGUE", "HUNTER", "SHAMAN", "DRUID", "WARLOCK", "PALADIN" }
  local uSeq = 0
  local function playerUnit(hostile)
    uSeq = uSeq + 1
    return { name = "Joueur" .. uSeq, isPlayer = true, hostile = hostile and true or false, class = CLS[1 + uSeq % #CLS], level = 18 + uSeq % 42,
      guid = ("Player-1-%08X"):format(40000 + uSeq), health = 1800 + uSeq % 900, healthMax = 2700, pvp = hostile and true or (uSeq % 3 == 0),
      targetsPlayer = hostile and uSeq % 9 == 0 }
  end
  local function npcUnit(name, npcID, hostile, lvl)
    uSeq = uSeq + 1
    return { name = name, guid = ("Creature-0-1-1-0-%d-%010X"):format(npcID, uSeq), hostile = hostile and true or false, level = lvl or 30,
      health = 900, healthMax = 900, classification = "normal", creatureType = hostile and "Bête" or "Humanoïde" }
  end
  local CITY_NPC = { { "Garde d'Orgrimmar", 3296 }, { "Commissaire-priseur", 8673 }, { "Banquier", 3309 }, { "Maître des coursiers", 3310 },
    { "Marchande de fruits", 3342 }, { "Maître des chasseurs", 3352 }, { "Tavernière", 6929 } }
  local QUEST_MOBS = { { "Loup-garou galeux", 7101 }, { "Sanglier des collines", 7102 }, { "Bandit de Syndicat", 7103 },
    { "Ours gris des contreforts", 7201 }, { "Couguar affamé", 7202 }, { "Tortue hargneuse", 7203 }, { "Voleur du Syndicat", 7204 },
    { "Murloc de la côte", 7210 }, { "Lapin", 721 }, { "Cerf", 883 } }
  local plates, nPlates, MAXP = {}, 0, 40
  local function plateOn(d)
    local slot
    for i = 1, MAXP do if not plates[i] then slot = i break end end
    if not slot then return end
    local u = "nameplate" .. slot
    plates[slot] = d
    nPlates = nPlates + 1
    M.units[u] = d
    ev("NAME_PLATE_CREATED", u)
    ev("NAME_PLATE_UNIT_ADDED", u)
    ev("UNIT_AURA", u, { isFullUpdate = true })
    if uSeq % 2 == 0 then ev("UNIT_TARGET", u) end
    if uSeq % 5 == 0 then ev("UNIT_NAME_UPDATE", u) ev("UNIT_FACTION", u) end
    return u
  end
  local function plateOff(slot)
    if not plates[slot] then return end
    local u = "nameplate" .. slot
    ev("NAME_PLATE_UNIT_REMOVED", u)
    M.units[u] = nil
    plates[slot] = nil
    nPlates = nPlates - 1
  end
  local function anyPlate()
    if nPlates == 0 then return nil end
    local s = rnd(MAXP)
    for i = 0, MAXP - 1 do
      local k = (s + i - 1) % MAXP + 1
      if plates[k] then return k, plates[k] end
    end
  end
  local function clearPlates() for i = 1, MAXP do plateOff(i) end end
  local GT = rawget(env, "GameTooltip")
  local function tipUnit(d)
    if not (GT and d) then return end
    local fn = GT:GetScript("OnTooltipSetUnit")
    if not fn then return end
    GT.GetUnit = function() return d.name, "mouseover" end
    GT.__lines = {}
    X.setCur(main.name)
    M.SafeCall("OnTooltipSetUnit", fn, GT)
  end
  local hovered
  local function hover(d)
    hovered = d
    M.units.mouseover = d
    ev("UPDATE_MOUSEOVER_UNIT")
    if d then tipUnit(d) end
    ev("CURSOR_CHANGED", false, d and 5 or 0, 0, 0)
  end
  local function target(d)
    if d then
      local t = {}
      for k, v in pairs(d) do t[k] = v end
      t.exists = true
      M.target = t
    else
      M.target = { exists = false }
    end
    ev("PLAYER_TARGET_CHANGED") ev("UNIT_TARGET", "player")
  end
  local walkT = 0
  local function walk()
    walkT = walkT + 1
    local p = M.pos
    if p then M.pos = { mapID = p.mapID, x = p.x + 0.00025 * ((walkT % 400 < 200) and 1 or -1), y = p.y + 0.00012 * ((walkT % 260 < 130) and 1 or -1) } end
    M.speed = 7
  end
  local CHAT = { "CHAT_MSG_CHANNEL", "CHAT_MSG_SAY", "CHAT_MSG_YELL", "CHAT_MSG_GUILD", "CHAT_MSG_EMOTE", "CHAT_MSG_TEXT_EMOTE", "CHAT_MSG_SYSTEM" }
  local FOREIGN = { "questie", "D4C", "BigWigs", "Auctionator", "LibDBIcon", "SPF", "WeakAuras" }
  local function chatter(perSec, addonPerSec)
    for _ = 1, rate("chat", perSec) do
      local e = CHAT[rnd(#CHAT)]
      ev(e, "VDS [Épée longue] 2po /w moi, LFM donjon besoin soigneur", "Joueur" .. rnd(900) .. "-Royaume", "", "2. Commerce", "", "", 2, 2, "Commerce", 0, 1000 + rnd(9000), ("Player-1-%08X"):format(rnd(90000)))
    end
    for _ = 1, rate("addon", addonPerSec) do
      local k = rnd(40)
      if k == 1 then      -- an old / broken TreeAI client in the channel: garbage on our own prefix
        ev("CHAT_MSG_ADDON", "TREEAI", "1~" .. string.rep("x", 200), "PARTY", "Brindille-Royaume-Test", "", 0, 0, "", 0)
      elseif k == 2 then
        ev("CHAT_MSG_ADDON", "TREEAI", "9~ZZZ~{{{", "GUILD", "Inconnu-Royaume", "", 0, 0, "", 0)
      else
        ev("CHAT_MSG_ADDON", FOREIGN[rnd(#FOREIGN)], "v=1;q=" .. rnd(9999) .. ";p=" .. rnd(100), (k % 2 == 0) and "GUILD" or "PARTY", "Joueur" .. rnd(900) .. "-Royaume", "", 0, 0, "", 0)
      end
    end
  end
  local function partyNoise(perSec)
    for _ = 1, rate("party", perSec) do
      local i = rnd(#M.group)
      local g = M.group[i]
      local u = (#M.group > 4 and "raid" or "party") .. i
      local k = rnd(4)
      if g then
        if k == 1 then g.health = math.max(1, (g.health or 800) - 40 + rnd(80)) ev("UNIT_HEALTH", u)
        elseif k == 2 then ev("UNIT_POWER_UPDATE", u, "MANA")
        elseif k == 3 then ev("UNIT_AURA", u, { isFullUpdate = false })
        else ev("UNIT_SPELLCAST_SUCCEEDED", u, "Cast-3-0-0-0-" .. rnd(9999), 100 + rnd(900)) end
      end
    end
  end
  local function rosterBurst(change)
    if change then change() end
    ev("GROUP_ROSTER_UPDATE") ev("PARTY_MEMBERS_CHANGED") ev("GROUP_ROSTER_UPDATE") ev("PARTY_LEADER_CHANGED") ev("UNIT_CONNECTION", "party1", true)
  end
  local function selfNoise()   -- what the client sends about the player himself, any time
    for _ = 1, rate("cd", 2) do ev("SPELL_UPDATE_COOLDOWN") ev("ACTIONBAR_UPDATE_COOLDOWN") ev("BAG_UPDATE_COOLDOWN") end
    for _ = 1, rate("usable", 1) do ev("SPELL_UPDATE_USABLE") ev("ACTIONBAR_UPDATE_USABLE") end
    for _ = 1, rate("mod", 0.5) do ev("MODIFIER_STATE_CHANGED", "LSHIFT", 1) ev("MODIFIER_STATE_CHANGED", "LSHIFT", 0) end
  end

  -- ------------------------------------------------------------------ 1. login : the storm after the loading screen
  phase("login", 12, function()
    for b = 0, 4 do ev("BAG_UPDATE", b) end
    ev("BAG_UPDATE_DELAYED") ev("UNIT_INVENTORY_CHANGED", "player")
    for s = 1, 19 do ev("PLAYER_EQUIPMENT_CHANGED", s, false) end
    ev("SPELLS_CHANGED") ev("QUEST_LOG_UPDATE") ev("UNIT_AURA", "player", { isFullUpdate = true }) ev("UPDATE_FACTION")
    ev("ZONE_CHANGED_NEW_AREA") ev("ZONE_CHANGED") ev("GROUP_ROSTER_UPDATE") ev("PLAYER_ALIVE") ev("UNIT_PET", "player")
    ev("PET_BAR_UPDATE") ev("UPDATE_PENDING_MAIL") ev("PLAYER_MONEY") ev("UPDATE_INVENTORY_DURABILITY")
    wait(1)
    for s = 1, 60 do ev("ACTIONBAR_SLOT_CHANGED", s) end
    ev("SPELLS_CHANGED") ev("QUEST_LOG_UPDATE") ev("UPDATE_BINDINGS") ev("PLAYER_UPDATE_RESTING")
    wait(1)
    for i = 1, 12 do plateOn((i % 3 == 0) and npcUnit("Garde d'Orgrimmar", 3296, false, 55) or playerUnit(false)) end
    for i = 1, 40 do ev("GET_ITEM_INFO_RECEIVED", 20100 + i % 20, true) if i % 10 == 0 then wait(1) end end
    ev("QUEST_LOG_UPDATE") ev("UNIT_QUEST_LOG_CHANGED", "player") ev("BAG_UPDATE", 0) ev("BAG_UPDATE_DELAYED")
    wait(12)
    ev("QUEST_LOG_UPDATE") ev("GROUP_ROSTER_UPDATE") ev("SPELLS_CHANGED") ev("CHAT_MSG_CHANNEL_NOTICE", "YOU_CHANGED", "", "", "1. Général")
  end, function() chatter(1, 6) selfNoise() end, clearPlates)

  -- ------------------------------------------------------------------ 2. ville : a capital, 40 nameplates in / out
  phase("ville", 45, function()
    M.player.resting = true
    ev("PLAYER_UPDATE_RESTING") ev("ZONE_CHANGED_NEW_AREA") ev("PLAYER_STARTED_MOVING")
    for _ = 1, 30 do plateOn(playerUnit(false)) end
  end, function()
    walk()
    for _ = 1, rate("in", 8) do
      if nPlates >= MAXP then local s = anyPlate() plateOff(s) end
      local k = rnd(10)
      if k <= 7 then plateOn(playerUnit(k == 1)) else local c = CITY_NPC[rnd(#CITY_NPC)] plateOn(npcUnit(c[1], c[2], false, 50)) end
    end
    for _ = 1, rate("out", 7) do local s = anyPlate() if s then plateOff(s) end end
    for _ = 1, rate("hover", 4) do local _, d = anyPlate() hover(d) end
    if hovered and rate("tip", 5) > 0 then tipUnit(hovered) end         -- the game re-sets a unit tooltip ~5×/s
    for _ = 1, rate("tgt", 0.7) do local _, d = anyPlate() target(d) end
    for _ = 1, rate("ua", 25) do local s = anyPlate() if s then ev("UNIT_AURA", "nameplate" .. s, { isFullUpdate = false }) end end
    for _ = 1, rate("ut", 8) do local s = anyPlate() if s then ev("UNIT_TARGET", "nameplate" .. s) end end
    for _ = 1, rate("uh", 6) do local s = anyPlate() if s then ev("UNIT_HEALTH", "nameplate" .. s) end end
    for _ = 1, rate("cast", 6) do local s = anyPlate() if s then ev("UNIT_SPELLCAST_START", "nameplate" .. s, "Cast-x", 100 + rnd(500)) ev("UNIT_SPELLCAST_STOP", "nameplate" .. s, "Cast-x", 100) end end
    for _ = 1, rate("gii", 12) do ev("GET_ITEM_INFO_RECEIVED", 20100 + rnd(20), true) end   -- other addons / inspect
    for _ = 1, rate("roster", 0.15) do rosterBurst(function() local g = M.group[1 + rnd(#M.group) - 1] if g then g.online = not (g.online == false) end end) end
    for _ = 1, rate("zone", 0.1) do ev("ZONE_CHANGED") ev("ZONE_CHANGED_INDOORS") ev("MINIMAP_UPDATE_ZOOM") end
    partyNoise(6) chatter(4, 18) selfNoise()
  end, function() hover(nil) target(nil) clearPlates() M.player.resting = false ev("PLAYER_UPDATE_RESTING") end)

  -- ------------------------------------------------------------------ 3. hub : quest hub, quest mobs + players
  phase("hub", 40, function()
    ev("ZONE_CHANGED")
    for _ = 1, 10 do plateOn(playerUnit(false)) end
    for _ = 1, 14 do local q = QUEST_MOBS[rnd(#QUEST_MOBS)] plateOn(npcUnit(q[1], q[2], true, 27 + rnd(4))) end
  end, function()
    walk()
    for _ = 1, rate("in", 5) do
      if nPlates >= 30 then local s = anyPlate() plateOff(s) end
      if rnd(3) == 1 then plateOn(playerUnit(false)) else local q = QUEST_MOBS[rnd(#QUEST_MOBS)] plateOn(npcUnit(q[1], q[2], true, 27 + rnd(4))) end
    end
    for _ = 1, rate("out", 4) do local s = anyPlate() if s then plateOff(s) end end
    for _ = 1, rate("hover", 3) do local _, d = anyPlate() hover(d) end
    if hovered and rate("tip", 5) > 0 then tipUnit(hovered) end
    for _ = 1, rate("tgt", 0.5) do local _, d = anyPlate() target(d) end
    for _ = 1, rate("uh", 20) do local s, d = anyPlate() if s then d.health = math.max(0, d.health - 90) ev("UNIT_HEALTH", "nameplate" .. s) end end
    for _ = 1, rate("ua", 12) do local s = anyPlate() if s then ev("UNIT_AURA", "nameplate" .. s, { isFullUpdate = false }) end end
    for _ = 1, rate("qn", 1.5) do ev("QUEST_LOG_UPDATE") end   -- the client's own noise (POI, party sync)
    for _ = 1, rate("pq", 0.4) do ev("QUEST_LOG_UPDATE") ev("UNIT_QUEST_LOG_CHANGED", "party" .. rnd(3)) end
    partyNoise(8) chatter(1.5, 14) selfNoise()
  end, function() hover(nil) target(nil) clearPlates() end)

  -- ------------------------------------------------------------------ 4. bg : battleground-like fight (in combat)
  local group4
  phase("bg", 40, function()
    group4 = M.group
    local raid = {}
    for i, g in ipairs(group4) do raid[i] = g end
    for i = #raid + 1, 9 do raid[i] = { name = "Allié" .. i, class = CLS[1 + i % #CLS], level = 30, health = 1500, healthMax = 1900, online = true, guid = ("Player-1-%08X"):format(70000 + i) } end
    M.group = raid
    rosterBurst() ev("RAID_ROSTER_UPDATE") ev("ZONE_CHANGED_NEW_AREA") ev("UPDATE_BATTLEFIELD_STATUS", 1)
    for i = 1, 26 do plateOn(playerUnit(i % 4 ~= 0)) end
    wait(2)
    M.EnterCombat()
    target(plates[2])
  end, function()
    walk()
    for _ = 1, rate("in", 6) do if nPlates >= MAXP then local s = anyPlate() plateOff(s) end plateOn(playerUnit(rnd(4) ~= 1)) end
    for _ = 1, rate("out", 5) do local s = anyPlate() if s then plateOff(s) end end
    for _ = 1, rate("uh", 60) do
      local k = rnd(10)
      if k == 1 then M.player.health = math.max(200, M.player.health - 60 + rnd(110)) ev("UNIT_HEALTH", "player")
      elseif k == 2 then ev("UNIT_HEALTH", "pet") elseif k == 3 then ev("UNIT_HEALTH", "target")
      else local s, d = anyPlate() if s then d.health = math.max(1, d.health - 120 + rnd(200)) ev("UNIT_HEALTH", "nameplate" .. s) end end
    end
    for _ = 1, rate("ua", 45) do
      local k = rnd(8)
      if k == 1 then ev("UNIT_AURA", "player", { isFullUpdate = false }) elseif k == 2 then ev("UNIT_AURA", "target", { isFullUpdate = false })
      elseif k == 3 then ev("UNIT_AURA", "pet", { isFullUpdate = false })
      else local s = anyPlate() if s then ev("UNIT_AURA", "nameplate" .. s, { isFullUpdate = false }) end end
    end
    for _ = 1, rate("up", 40) do
      if rnd(6) == 1 then ev("UNIT_POWER_UPDATE", "player", "MANA") ev("UNIT_POWER_FREQUENT", "player", "MANA")
      else local s = anyPlate() if s then ev("UNIT_POWER_UPDATE", "nameplate" .. s, "RAGE") end end
    end
    for _ = 1, rate("ut", 12) do local s = anyPlate() if s then ev("UNIT_TARGET", "nameplate" .. s) end end
    for _ = 1, rate("cast", 20) do local s = anyPlate() if s then ev("UNIT_SPELLCAST_START", "nameplate" .. s, "Cast-y", 100 + rnd(500)) ev("UNIT_SPELLCAST_SUCCEEDED", "nameplate" .. s, "Cast-y", 100 + rnd(500)) end end
    for _ = 1, rate("shot", 0.5) do ev("UNIT_SPELLCAST_SUCCEEDED", "player", "Cast-3-0-0-0-75-" .. rnd(9999), 75) ev("UNIT_INVENTORY_CHANGED", "player") end
    for _ = 1, rate("uc", 10) do ev("UNIT_COMBAT", (rnd(3) == 1) and "player" or "target", "WOUND", "", 60 + rnd(90), 1) end
    for _ = 1, rate("thr", 5) do ev("UNIT_THREAT_LIST_UPDATE", "target") ev("UNIT_THREAT_SITUATION_UPDATE", "player") end
    for _ = 1, rate("tgt", 1) do local _, d = anyPlate() target(d) end
    for _ = 1, rate("hover", 3) do local _, d = anyPlate() hover(d) end
    for _ = 1, rate("score", 1) do ev("UPDATE_BATTLEFIELD_SCORE") ev("CHAT_MSG_BG_SYSTEM_NEUTRAL", "L'Alliance prend la scierie !") end
    for _ = 1, rate("roster", 0.2) do rosterBurst() ev("RAID_ROSTER_UPDATE") end
    partyNoise(30) chatter(2, 25) selfNoise()
  end, function()
    hover(nil) target(nil)
    M.LeaveCombat()
    clearPlates()
    M.group = group4 or M.group
    rosterBurst() ev("ZONE_CHANGED_NEW_AREA")
  end)

  -- ------------------------------------------------------------------ 5. butin : loot bursts
  local function bagOf(id)
    for b = 0, 4 do local bag = M.bags[b] if bag then for s = 1, bag.size do local sl = bag.slots[s] if sl and sl.id == id then return b, s end end end end
    return 0, 1
  end
  local function lootMsg(id, n)
    local link = M.ItemLink(id)
    if n and n > 1 then return (env.LOOT_ITEM_SELF_MULTIPLE):format(link, n) end
    return (env.LOOT_ITEM_SELF):format(link)
  end
  local function questItemGain(id)
    for _, e in ipairs(M.quests) do
      local o = e.objectives and e.objectives[1]
      if o and o.type == "item" and EXTRA_ITEMS[id] and o.label == EXTRA_ITEMS[id].name and not o.done then
        o.have = math.min(o.need, o.have + 1)
        o.done = o.have >= o.need
        o.text = ("%s : %d/%d"):format(o.label, o.have, o.need)
        ev("UI_INFO_MESSAGE", 0, o.text)
        ev("QUEST_WATCH_UPDATE", e.id) ev("QUEST_LOG_UPDATE") ev("UNIT_QUEST_LOG_CHANGED", "player")
        return
      end
    end
  end
  local function lootCorpse(items, perFrameItems)
    local list = {}
    for i, it in ipairs(items) do list[i] = { name = M.items[it[1]].name, count = it[2] or 1, id = it[1], questItem = M.items[it[1]].questItem } end
    M.loot = list
    ev("LOOT_READY", true) ev("LOOT_OPENED", true)
    wait(1)
    for i, it in ipairs(items) do
      local id, n = it[1], it[2] or 1
      if X.addToBags(M, id, n) then
        local b = bagOf(id)
        ev("LOOT_SLOT_CLEARED", i)
        ev("CHAT_MSG_LOOT", lootMsg(id, n), main.name, "", "", main.name)
        ev("ITEM_PUSH", b, 134400)
        ev("BAG_UPDATE", b)
        if M.items[id].questItem then questItemGain(id) end
      else
        ev("UI_ERROR_MESSAGE", 3, "Vos sacs sont pleins.")
      end
      if i % (perFrameItems or 2) == 0 then ev("BAG_UPDATE_DELAYED") wait(1) end
    end
    ev("BAG_UPDATE_DELAYED")
    M.loot = nil
    ev("LOOT_CLOSED")
    M.player.money = M.player.money + 40 + rnd(60)
    ev("PLAYER_MONEY") ev("CHAT_MSG_MONEY", "Vous ramassez 43 Cuivre.")
    wait(1)
    ev("BAG_UPDATE", 0) ev("BAG_UPDATE_DELAYED") ev("UNIT_INVENTORY_CHANGED", "player")
    for _, it in ipairs(items) do ev("GET_ITEM_INFO_RECEIVED", it[1], true) end
    wait(2)
    ev("QUEST_LOG_UPDATE")
  end
  local DROPS = { { 20110, 2 }, { 20111, 1 }, { 20116, 1 }, { 20117, 1 }, { 20101, 1 }, { 20104, 1 }, { 20112, 1 }, { 20115, 1 }, { 20113, 1 },
    { 20119, 1 }, { 20102, 1 }, { 20118, 1 } }
  local function drops(n) local t = {} for i = 1, n do t[i] = DROPS[rnd(#DROPS)] end return t end
  local function sellJunk(max)
    local sold = 0
    for b = 0, 4 do
      local bag = M.bags[b]
      for s = 1, bag and bag.size or 0 do
        local sl = bag.slots[s]
        local it = sl and M.items[sl.id]
        if it and sold < max and (it.quality == 0 or sl.id == 20110 or sl.id == 20111 or sl.id == 20112) then
          bag.slots[s] = nil
          sold = sold + 1
          M.player.money = M.player.money + (it.sellPrice or 1)
          ev("BAG_UPDATE", b) ev("PLAYER_MONEY") ev("BAG_UPDATE_DELAYED")
          wait(1 + sold % 2)
        end
      end
    end
  end
  local bagF = rawget(env, "ContainerFrame1")
  phase("butin", 55, function()
    ev("ZONE_CHANGED")
    for k = 1, 5 do      -- five solo kills, 1-4 items each, hunter shots in between
      for _ = 1, 3 do ev("UNIT_SPELLCAST_SUCCEEDED", "player", "Cast-3-0-0-0-75-" .. rnd(9999), 75) ev("UNIT_INVENTORY_CHANGED", "player") wait(12) end
      ev("CHAT_MSG_COMBAT_XP_GAIN", (env.COMBATLOG_XPGAIN_FIRSTPERSON):format("Tortue hargneuse", 310)) ev("PLAYER_XP_UPDATE", "player")
      lootCorpse(drops(1 + k % 4))
      for i = 1, 3 do ev("CHAT_MSG_LOOT", ("%s reçoit le butin : %s."):format(order[1 + i].name, M.ItemLink(20110)), order[1 + i].name, "", "", order[1 + i].name) end
      wait(20)
    end
    -- bags open: every bag change redraws the item overlays
    if bagF then bagF:Show() ev("BAG_OPEN", 0) if rawget(env, "ToggleBackpack") then X.BASE.pcall(env.ToggleBackpack) end end
    wait(10)
    -- AoE loot: 6 corpses, 14 items in a few images
    lootCorpse(drops(14), 4)
    wait(15)
    if X.hoverBags then X.hoverBags(main, 8) X.setCur(main.name) end
    wait(10)
    -- item info storm (bank / auction scan by another addon, inspect)
    for i = 1, 90 do ev("GET_ITEM_INFO_RECEIVED", 20100 + i % 21, true) if i % 6 == 0 then wait(1) end end
    wait(10)
    -- vendor: junk sold fast (auto-sell addon / shift-clicks)
    M.SetMerchant(true)
    wait(10)
    sellJunk(18)
    for _, it in pairs(M.inventory) do if it.durMax then it.dur = it.durMax end end
    ev("UPDATE_INVENTORY_DURABILITY")
    X.addToBags(M, 2515, 200) ev("BAG_UPDATE", 1) ev("BAG_UPDATE_DELAYED") ev("UNIT_INVENTORY_CHANGED", "player")
    wait(10)
    M.SetMerchant(false)
    wait(10)
    -- ten bandages crafted in a row
    for i = 1, 10 do
      ev("UNIT_SPELLCAST_START", "player", "Cast-craft" .. i, 3277) wait(6)
      X.addToBags(M, 3530, 1)
      ev("UNIT_SPELLCAST_SUCCEEDED", "player", "Cast-craft" .. i, 3277) ev("UNIT_SPELLCAST_STOP", "player", "Cast-craft" .. i, 3277)
      ev("BAG_UPDATE", 0) ev("BAG_UPDATE", 1) ev("CHAT_MSG_LOOT", "Vous créez : " .. M.ItemLink(3530) .. ".", main.name, "", "", main.name)
      ev("BAG_UPDATE_DELAYED") ev("TRADE_SKILL_UPDATE") ev("CHAT_MSG_SKILL", "Votre compétence en Secourisme est maintenant de " .. (100 + i) .. ".")
    end
    wait(10)
    if bagF then bagF:Hide() ev("BAG_CLOSED", 0) end
    -- three more kills with the bags closed, quest items
    for _ = 1, 3 do lootCorpse({ { 20101, 1 }, { 20104, 1 }, DROPS[rnd(#DROPS)] }) wait(25) end
  end, function() partyNoise(4) chatter(1, 10) selfNoise() end, function() if bagF and bagF:IsShown() then bagF:Hide() ev("BAG_CLOSED", 0) end end)

  -- ------------------------------------------------------------------ 6. quetes : quest-log bursts
  local function kill(e, oi)
    local o = e.objectives[oi or 1]
    if not o or o.done then return end
    o.have = o.have + 1
    o.done = o.have >= o.need
    o.text = ("%s : %d/%d"):format(o.label or "Objectif", o.have, o.need)
    local all = true
    for _, x in ipairs(e.objectives) do if not x.done then all = false end end
    e.complete = all
    ev("CHAT_MSG_COMBAT_XP_GAIN", (env.COMBATLOG_XPGAIN_FIRSTPERSON):format("Monstre", 300)) ev("PLAYER_XP_UPDATE", "player")
    ev("UI_INFO_MESSAGE", 0, o.text)
    ev("QUEST_WATCH_UPDATE", e.id) ev("QUEST_LOG_UPDATE") ev("UNIT_QUEST_LOG_CHANGED", "player")
    wait(1)
    ev("QUEST_LOG_UPDATE")
    wait(4)
    ev("QUEST_LOG_UPDATE") ev("QUEST_POI_UPDATE")
  end
  local function entry(id) for _, e in ipairs(M.quests) do if e.id == id then return e end end end
  local function acceptQ(id, title, level)
    ev("GOSSIP_SHOW") ev("QUEST_DETAIL")
    wait(8)
    M.quests[#M.quests + 1] = { id = id, title = title, level = level, xp = 2500, complete = false,
      objectives = { { text = "Rôdeur tué : 0/8", label = "Rôdeur tué", have = 0, need = 8, done = false, type = "monster" } } }
    ev("QUEST_ACCEPTED", #M.quests, id) ev("QUEST_LOG_UPDATE") ev("UNIT_QUEST_LOG_CHANGED", "player")
    M.WatchQuest(id, true, 25)
    ev("QUEST_LOG_UPDATE") ev("QUEST_FINISHED") ev("GOSSIP_CLOSED") ev("QUEST_POI_UPDATE") ev("CHAT_MSG_SYSTEM", "Quête acceptée : " .. title)
    wait(1)
    ev("QUEST_LOG_UPDATE")
  end
  local function turnInQ(id)
    for i, e in ipairs(M.quests) do
      if e.id == id then
        ev("QUEST_PROGRESS") wait(5) ev("QUEST_COMPLETE") wait(8)
        table.remove(M.quests, i)
        M.player.xp = M.player.xp + 900
        M.player.money = M.player.money + 2000
        ev("QUEST_TURNED_IN", id, 2500, 2000) ev("QUEST_REMOVED", id) ev("QUEST_LOG_UPDATE") ev("UNIT_QUEST_LOG_CHANGED", "player")
        ev("PLAYER_XP_UPDATE", "player") ev("PLAYER_MONEY") ev("BAG_UPDATE", 0) ev("BAG_UPDATE_DELAYED")
        ev("CHAT_MSG_SYSTEM", e.title .. " terminée.") ev("QUEST_FINISHED")
        wait(1)
        ev("QUEST_LOG_UPDATE") ev("QUEST_POI_UPDATE")
        return
      end
    end
  end
  phase("quetes", 55, function()
    -- eight kills, 3 s apart, alternating quests (the last ones fill an objective)
    local ids = { 5201, 5204, 5202, 5210, 5201, 5204, 5210, 5202 }
    for i, id in ipairs(ids) do
      local e = entry(id)
      if e then kill(e, (id == 5204 or id == 5210) and (1 + i % 2) or 1) end
      wait(FPS * 2)
    end
    -- an objective filled: 5208 (Mousse des marais 4/6 → 6/6)
    local e = entry(5208)
    if e then kill(e) wait(10) kill(e) end
    wait(FPS)
    -- back at the hub: 2 turn-ins then 3 accepts within ~4 s
    turnInQ(5206) wait(10) turnInQ(5209) wait(10)
    acceptQ(5301, "Mission de test 1", 25) wait(6) acceptQ(5302, "Mission de test 2", 26) wait(6) acceptQ(5303, "Mission de test 3", 27)
    wait(FPS * 2)
    -- the player (or another addon) tracks / untracks six quests in six images
    for i = 1, 6 do M.WatchQuest(5200 + i, i % 2 == 1, 25) ev("QUEST_LOG_UPDATE") wait(1) end
    wait(FPS * 2)
    for i = 1, 6 do M.WatchQuest(5200 + i, i % 2 == 0, 25) wait(1) end
    wait(FPS * 2)
    -- abandon one, then kills again with the party progressing at the same time
    if M.AbandonServer then M.AbandonServer(5303) end
    wait(FPS)
    for i = 1, 6 do
      local q = entry((i % 2 == 0) and 5202 or 5201)
      if q then kill(q) end
      ev("QUEST_LOG_UPDATE") ev("UNIT_QUEST_LOG_CHANGED", "party" .. (1 + i % 3))
      wait(FPS)
    end
  end, function()
    walk()
    for _ = 1, rate("qn", 2) do ev("QUEST_LOG_UPDATE") end
    for _ = 1, rate("pq", 0.5) do ev("QUEST_LOG_UPDATE") ev("UNIT_QUEST_LOG_CHANGED", "party" .. rnd(3)) ev("QUEST_LOG_UPDATE") end
    partyNoise(4) chatter(1, 10) selfNoise()
  end)

  -- ------------------------------------------------------------------ 7. repos : nothing happens
  -- (measured after 40 s: the last catch-ups of what moved during the phases before are done ; one full « tout
  -- relire » cycle of the core — every 30 s — falls inside the measure)
  phase("repos", 80, function() M.speed = 0 ev("PLAYER_STOPPED_MOVING") end, nil, nil, 40)

  -- (5.1 AH) « rends le truc bcp plus réactif … achat-revente en masse » : only with `--phases hv` (not part of the
  -- default crowd run). A session at the auction house: open, full scan of ~2 600 listings (sliced), 30 purchases
  -- (one key press each, live re-check in between), 30 stacks posted, AH closed, then rest. Gate: no image over 4 ms,
  -- nothing once the AH is closed; the scripted presses are the player's hardware events.
  if only and only.hv then
    local hv = {}
    function hv.work()                                   -- kinstr spent so far by the two AH modules in this phase
      local n = 0
      for k, v in pairs(phases[#phases] and phases[#phases].byCtx or {}) do if k:find("^AHDesk:") or k:find("^Auction:") then n = n + v end end
      return n
    end
    -- the world before the session (not measured: the player arrives with his bags full and the HV switched on)
    local Tm = main.T
    local A, D = Tm:GetModule("Auction"), Tm.AHDesk
    X.setCur(main.name)
    if not (A._active and D._active) then   -- profile « sur »: the HV is a beta function the player switched on
      Tm:SetModuleEnabled("Auction", true)
      Tm:SetModuleEnabled("AHDesk", true)
    end
    hv.D = D
    A.db.autoReserve, A.db.keepGold, A.db.mountGold = false, 0, 0
    M.player.money = 5000000
    M.ah.real = true
    do
      local list, aid = {}, 400000
      local function L(id, count, unit) aid = aid + 1 list[#list + 1] = { id = id, count = count, buyout = unit * count, owner = "Vendeur" .. (aid % 9), auctionID = aid } end
      for i = 1, 800 do                                  -- the server's market: 800 items, 3 listings each
        local id = 300000 + i
        M.AddItem(id, { name = "Marchandise-" .. i, stack = 20, sellPrice = 10 + i % 40, type = "Artisanat", classID = 7 })
        for k = 1, 3 do L(id, 5 + (i + k) % 16, 200 + (i * 7) % 300 + k * 3) end
      end
      for i = 1, 30 do                                   -- 30 single items listed under their vendor price
        local id = 301000 + i
        M.AddItem(id, { name = "Babiole-" .. i, stack = 1, sellPrice = 900 + i, type = "Divers", classID = 15 })
        L(id, 1, 300 + i * 5) L(id, 1, 2000 + i)
      end
      for i = 1, 6 do                                    -- what the player farmed: 6 trade goods, 5 stacks each
        local id = 302000 + i
        M.AddItem(id, { name = "Butin-" .. i, stack = 20, sellPrice = 20, type = "Artisanat", classID = 7 })
        for k = 1, 4 do L(id, 20, 800 + i * 10 + k) end
      end
      M.ah.list = list
      for b = 1, 3 do for sl = 1, 10 do M.SetBagItem(b, sl, 302000 + 1 + ((b * 10 + sl) % 6), 20) end ev("BAG_UPDATE", b) end
      ev("BAG_UPDATE_DELAYED")
    end
    tick(20)
    phase("hv", 170, function()
      M.speed = 0 ev("PLAYER_STOPPED_MOVING")            -- standing at the auctioneer
      wait(25)
      M.SetAuctionHouse(true)
      wait(25)
      M.Hw(D.frame.scan.Click, D.frame.scan)
      for _ = 1, 600 do wait(1) if A.lastScan and A.lastScan.full and not A.job and not D.build and #D.all > 0 then break end end
      hv.queue = #D.buyQ
      if opts.verbose then io.write(("  hv dbg: list %d, cur %d, all %d, lastScan %s, msg %s, status %s\n"):format(#M.ah.list, (function() local n = 0 for _ in pairs(A.cur) do n = n + 1 end return n end)(), #D.all, tostring(A.lastScan and A.lastScan.rows), tostring(A.msg), tostring(D.status))) end
      for _ = 1, 400 do                                  -- hammer the key: 4 presses / second
        if not D.BuyHead() then break end
        M.Hw(Tm.AuctionNext)
        wait(6)
      end
      hv.buys = D.sess.buys
      wait(25)
      for _ = 1, 400 do
        if D.sess.posts >= 30 or not (D.PostHead() or D.pb or A.mini) then break end
        if D.PostHead() then M.Hw(Tm.AuctionNext) end
        wait(6)
      end
      hv.posts = D.sess.posts
      hv.wait = D.waitQ
      -- (5.1 AH, tranche 2) the next day: the loot sells ~12 / day (scans of the previous days), and a competitor
      -- undercuts two of the player's items by 2 pa — the « Annuler » queue, hammered like the others: 10 cancels
      do
        local day = math.floor(M.serverTime / 86400)
        for i = 1, 6 do
          local id, h, sl = 302000 + i, {}, {}
          for k = 5, 2, -1 do
            for _, v in ipairs({ day - k, 820 + i * 10, 830 + i * 10, 80, 2, 10, 12 }) do h[#h + 1] = v end
            sl[#sl + 1] = day - k sl[#sl + 1] = 6
          end
          local rec = A.RealmData().items[id] or {}
          for _, v in ipairs(rec.h or {}) do h[#h + 1] = v end
          rec.h, rec.sl = h, sl
          A.RealmData().items[id] = rec
        end
        local aid = 490000
        for i = 1, 2 do aid = aid + 1 M.ah.list[#M.ah.list + 1] = { id = 302000 + i, count = 20, buyout = (790 + i * 10) * 20, owner = "Rival", auctionID = aid } end
      end
      wait(75)                                           -- my auctions are re-read (2 s after the last post, throttle permitting)
      M.Hw(D.frame.scan.Click, D.frame.scan)            -- full scan on cooldown: targeted re-read of what I trade
      for _ = 1, 600 do wait(1) if not A.mini and not D.cb and D.CancelHead() then break end end
      hv.cxl = #D.cxlQ
      if opts.verbose then
        io.write(("  hv cancel dbg: owned %d (mock %d), cxlQ %d\n"):format(#(A.owned or {}), #M.ah.owned, #D.cxlQ))
        for i, a in ipairs(A.owned or {}) do
          if i <= 30 and (a.id == 302001 or a.id == 302002) then
            local e, st, why = D._CancelEntry(A, a, D.Rules())
            local it = A.cur[a.id]
            io.write(("  hv owned %s x%s @%s: %s %s %s (cur t-%s, low %s)\n"):format(tostring(a.id), tostring(a.count), tostring(a.unit),
              tostring(e and e.gain), tostring(st), tostring(why), tostring(it and (M.serverTime - it.t)), tostring(it and it.listings[1] and it.listings[1].unit)))
          end
        end
      end
      for _ = 1, 400 do
        if D.sess.cancels >= 10 or not (D.CancelHead() or D.cb or A.mini) then break end
        if D.CancelHead() then M.Hw(Tm.AuctionNext) end
        wait(6)
      end
      hv.cancels = D.sess.cancels
      if opts.verbose then
        local nb = 0
        for b = 1, 3 do for sl = 1, 10 do if M.bags[b] and M.bags[b].slots[sl] then nb = nb + 1 io.write(("  hv left in bags: %d/%d id %s x%s\n"):format(b, sl, tostring(M.bags[b].slots[sl].id), tostring(M.bags[b].slots[sl].count))) end end end
        io.write(("  hv end: postQ %d pb %s mini %s head %s\n"):format(#D.postQ, tostring(D.pb ~= nil), tostring(A.mini ~= nil), tostring(D.PostHead() ~= nil)))
      end
      wait(25)
      M.SetAuctionHouse(false)
      wait(50)                                           -- 2 s for the last debounced handlers
      hv.closedAt, hv.atClose = X.simSeconds(), hv.work()
    end)
    hv.atEnd = hv.work()
    if hv.D then
      X.note = X.note or function() end
      io.write(("hv session: queue %s, buys %s, posts %s, cancels %s (queue %s), skipped %s\n"):format(tostring(hv.queue), tostring(hv.buys),
        tostring(hv.posts), tostring(hv.cancels), tostring(hv.cxl), tostring(hv.D.sess.skipped)))
      if opts.verbose then for _, l in ipairs(hv.D.log) do io.write("  hv log: ", l, "\n") end end
      if opts.verbose then for _, w in ipairs(hv.wait or {}) do io.write("  hv wait: ", tostring(w.name), " ", tostring(w.why), "\n") end end
      if not hv.closedAt then X.fail("hv: the session did not finish")
      else
        io.write(("hv closed at %d s: AH modules' work in the %d s that follow: %d kinstr\n"):format(hv.closedAt, X.simSeconds() - hv.closedAt, hv.atEnd - hv.atClose))
        if hv.atEnd - hv.atClose > 0 then X.fail(("hv: %d kinstr of AH work with the auction house closed"):format(hv.atEnd - hv.atClose)) end
      end
      if (hv.buys or 0) < 30 then X.fail(("hv: only %s purchases out of 30"):format(tostring(hv.buys))) end
      if (hv.posts or 0) < 30 then X.fail(("hv: only %s posts out of 30"):format(tostring(hv.posts))) end
      if (hv.cancels or 0) < 10 then X.fail(("hv: only %s cancels out of 10"):format(tostring(hv.cancels))) end
    end
  end

  FR.driver, FR.onFrame, FR.inbox = nil, nil, nil

  -- ------------------------------------------------------------------ report + gates
  local out = {}
  local function P(s) out[#out + 1] = s end
  P(("crowd: image = %.0f ms (%d / s), TreeAI work per image in kinstr (50 = 1 ms), limit %d (4 ms)"):format(STEP * 1000, FPS, LIMIT))
  for _, p in ipairs(phases) do
    table.sort(p.costs)
    local n = #p.costs
    local function pct(q) return n > 0 and p.costs[math.max(1, math.ceil(n * q))] or 0 end
    P(("phase %-7s %3d s  images %5d  worst %4d kinstr (%.2f ms)  p99 %3d  p50 %2d  mean %5.1f  over 4 ms: %d  total %6d kinstr (%.0f / s)  widgets %.0f / s  QuestieDB %.0f / s (max %d in one image)  alloc %.0f KB/s")
      :format(p.name, p.sec, n, p.worst, p.worst / 50, pct(0.99), pct(0.5), n > 0 and p.sum / n or 0, p.over, p.sum, p.sum / math.max(1, p.sec),
        p.apiN / math.max(1, p.sec), (p.dbN or 0) / math.max(1, p.sec), p.dbMax or 0, p.akb / math.max(1, p.sec)))
    local a, b = {}, {}
    for _, e in ipairs(p.worstCtx or {}) do a[#a + 1] = ("%s=%d"):format(e.k, e.n) end
    for _, e in ipairs(p.worstTop or {}) do b[#b + 1] = ("%s=%d"):format(e.k, e.n) end
    P(("  worst image #%d : %s | %s | widgets=%d"):format(p.worstAt or 0, table.concat(a, " "), table.concat(b, " "), p.worstApi or 0))
    local cs, ms = {}, {}
    for k, v in pairs(p.byCtx) do cs[#cs + 1] = { k = k, n = v } end
    for k, v in pairs(p.byMod) do ms[#ms + 1] = { k = k, n = v } end
    table.sort(cs, function(x, y) return x.n > y.n end)
    table.sort(ms, function(x, y) return x.n > y.n end)
    local c2, m2 = {}, {}
    for i = 1, math.min(opts.verbose and 30 or 8, #cs) do c2[#c2 + 1] = ("%s=%d"):format(cs[i].k, cs[i].n) end
    for i = 1, math.min(opts.verbose and 30 or 10, #ms) do m2[#m2 + 1] = ("%s=%d"):format(ms[i].k, ms[i].n) end
    local d1, d2 = {}, {}
    for i = 1, math.min(4, #(p.worstDb or {})) do d1[#d1 + 1] = ("%s=%d"):format(p.worstDb[i].k, p.worstDb[i].n) end
    for k, v in pairs(p.dbBy or {}) do d2[#d2 + 1] = { k = k, n = v } end
    table.sort(d2, function(x, y) return x.n > y.n end)
    local d3 = {}
    for i = 1, math.min(opts.verbose and 30 or 8, #d2) do d3[#d3 + 1] = ("%s=%d"):format(d2[i].k, d2[i].n) end
    if #d1 > 0 then P("  worst image QuestieDB reads : " .. table.concat(d1, " ")) end
    if #d3 > 0 then P("  QuestieDB reads by : " .. table.concat(d3, " ")) end
    P("  by context : " .. table.concat(c2, " "))
    P("  by module  : " .. table.concat(m2, " "))
    if p.worst > LIMIT then X.fail(("crowd %s: worst image %d kinstr (%.1f ms) > %d (4 ms)"):format(p.name, p.worst, p.worst / 50, LIMIT)) end
    if p.restFrom then
      local perS = (p.restSum or 0) / math.max(1, (p.restN or 0) / FPS)
      local secs = math.max(1, (p.restN or 0) / FPS)
      P(("  rest: %.1f kinstr / s of TreeAI work after %d s (limit %d), %.1f widget calls / s, %.1f KB / s")
        :format(perS, p.restFrom / FPS, REST_KINSTR_S, (p.restApi or 0) / secs, (p.restKB or 0) / secs))
      local rc, r2 = {}, {}
      for k, v in pairs(p.restCtx or {}) do rc[#rc + 1] = { k = k, n = v } end
      table.sort(rc, function(x, y) return x.n > y.n end)
      for i = 1, math.min(14, #rc) do r2[#r2 + 1] = ("%s=%d"):format(rc[i].k, rc[i].n) end
      P(("  rest: images with any TreeAI work %d / %d ; who : %s"):format(p.restBusy or 0, p.restN or 0, table.concat(r2, " ")))
      local rl, r3 = {}, {}
      for k, v in pairs(p.restLine or {}) do rl[#rl + 1] = { k = k, n = v } end
      table.sort(rl, function(x, y) return x.n > y.n end)
      for i = 1, math.min(22, #rl) do r3[#r3 + 1] = ("%s=%d"):format(rl[i].k, rl[i].n) end
      P("  rest: by function : " .. table.concat(r3, " "))
      if perS > REST_KINSTR_S then X.fail(("crowd rest: %.1f kinstr / s > %d (rien au repos)"):format(perS, REST_KINSTR_S)) end
    end
  end
  if next(logReads) then
    local lr = {}
    for k, v in pairs(logReads) do lr[#lr + 1] = { k = k, n = v } end
    table.sort(lr, function(x, y) return x.n > y.n end)
    P("quest-log table reads over the run (API  context):")
    for i = 1, math.min(25, #lr) do P(("  %6d  %s"):format(lr[i].n, lr[i].k)) end
  end
  if next(allocBy) then
    local al = {}
    for k, v in pairs(allocBy) do al[#al + 1] = { k = k, n = v } end
    table.sort(al, function(x, y) return x.n > y.n end)
    P("memory allocated by dispatched call (KB over the run, phase  module  job):")
    for i = 1, math.min(45, #al) do P(("  %8.0f KB  %s"):format(al[i].n, al[i].k)) end
  end
  -- what the addon itself measured (T.Spikes: a call over 8 ms, an image over 12 ms on its own clock) and the watchdog
  local Tm = main.T
  local sp = Tm and Tm.Spikes and Tm.Spikes.Store and Tm.Spikes.Store()
  local nSp = sp and ((tonumber(sp.calls) or 0) + (tonumber(sp.frames) or 0)) or 0
  P(("spikes recorded by the addon: %d (calls over 8 ms: %d, images over 12 ms: %d)"):format(nSp, sp and sp.calls or 0, sp and sp.frames or 0))
  if nSp > 0 then
    for _, l in ipairs(Tm.Spikes.Lines(8, 0)) do P("  " .. l) end
    X.fail(("crowd: %d spike(s) recorded by T.Spikes"):format(nSp))
  end
  local asleep = Tm and Tm.Watchdog and Tm.Watchdog.Asleep and Tm.Watchdog.Asleep() or {}
  if #asleep > 0 then X.fail("crowd: module(s) put to sleep by the watchdog: " .. table.concat(asleep, ", ")) end
  -- the compass: every change of target is logged by the addon (NavTrace) — a target that flaps between two quests
  -- (A → B → A within a few seconds) is the « boussole qui hésite » complaint
  local NTr = Tm and Tm.NavTrace
  local nav = NTr and NTr.Lines and NTr.Lines(300) or {}
  local changes, flaps, prevPair = 0, 0, nil
  local seq = {}
  for i = #nav, 1, -1 do
    local a, b = tostring(nav[i]):match("cible · (.-) → (.-) %(")
    if a and b then
      changes = changes + 1
      seq[#seq + 1] = { a, b }
      if prevPair and prevPair[1] == b and prevPair[2] == a then flaps = flaps + 1 end
      prevPair = { a, b }
    end
  end
  P(("compass: %d target changes over the run, %d immediate back-and-forth (A → B → A)"):format(changes, flaps))
  if flaps > 0 then X.fail(("crowd: the compass flapped %d time(s) between two targets"):format(flaps)) end
  if opts.verbose then for i = 1, math.min(40, #nav) do P("  nav " .. tostring(nav[i])) end end
  local qb, hold = Tm and Tm._questBurst, Tm and Tm.HOLD and Tm.HOLD.GROUP_ROSTER_UPDATE
  P(("noise dropped by the core: %d quest-log events, %d roster events (given: %d)"):format(qb and qb.noise or 0, hold and hold.noise or 0, hold and hold.n or 0))
  return out
end
