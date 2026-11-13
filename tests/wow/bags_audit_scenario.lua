-- Harness scenario (5.1, agent BAGS) — THE AUDIT: « proof, not hope ». Run by tests/wow/test_bags_predict.py once per
-- class on the faithful WoW: Forever mock. For the WHOLE item universe TreeAI knows —
--   every reagent of every recipe (Data/Recipes, 12 profession lines) and every recipe item,
--   every consumable tier (Data/Consumables: food, water, healing / mana potions, bandages), every ammunition,
--   every pet food (Data/PetFood), the class reagents,
--   and a QuestieDB with the real schema whose quests ask for those same items (objectives, source items, quest
--   starters; class / faction / profession / repeatable / exclusive quests, every level 1–60; the real Questie database
--   is not in this repository — it is read in the game only — so the quests are generated here, the items are real) —
-- × this class × levels 10–60 × profession pairs × « quests done » variants, it asserts with an ORACLE written here
-- from the raw tables (not from the predictors' code):
--   A. an item with a live predicted use (a quest he can still take, a recipe of HIS professions that still gives
--      points, the best consumable tier he can use / a higher one he will use, his ammunition, his pet's food, a recipe
--      he can learn) is NEVER « sell » / « AH » / « throw away », never even « to confirm », never in a sell list;
--   B. an item with no possible use for this character is never kept without a stated reason;
--   C. every verdict is well formed (action in the contract, keep + surplus = what he has, a sentence to show);
--   D. without Questie, nothing but greys can reach the one-click list.
-- Prints « AUDIT … » lines with the counts.
return function(M, ctx)
  local T = TreeAI
  local check = ctx.check
  local B = T.Bags
  local P = B and B.Predict
  local function out(s) io.write("AUDIT ", tostring(s), "\n") end
  if not check(B and B.Verdict and P and P.SetOverride and P.RX, "Bags + predictors loaded") then return end
  M.LeaveCombat()
  B.UnregisterKeeper("Gear")       -- the audit is about the predictors of Bags: the keepers of GEAR / PROF read the game
  B.UnregisterKeeper("Prof")       -- (they can only ADD claims; they are exercised by bags_scenario and the replay)
  local _, CLASS = UnitClass("player")
  local CLASS_LIST = { "WARRIOR", "PALADIN", "HUNTER", "ROGUE", "PRIEST", "SHAMAN", "MAGE", "WARLOCK", "DRUID" }
  local CLASS_ID = { WARRIOR = 1, PALADIN = 2, HUNTER = 3, ROGUE = 4, PRIEST = 5, SHAMAN = 7, MAGE = 8, WARLOCK = 9, DRUID = 11 }
  local classIdx = 1
  for i, c in ipairs(CLASS_LIST) do if c == CLASS then classIdx = i end end
  local floor, max, min = math.floor, math.max, math.min

  -- ------------------------------------------------------------------ the universe (raw tables)
  local RD, CD, PD = T.Data.Recipes, T.Data.Consumables, T.Data.PetFood
  local U, order = {}, {}          -- id → { mat, cat, req, val, label, pet = { diet, lvl }, recipe = { line, learn }, reagentOf }
  local function ent(id)
    local e = U[id]
    if not e then e = { id = id } U[id] = e order[#order + 1] = id end
    return e
  end
  local usesOf = {}                -- id → { { line, grey }, … }
  local nRecipes = 0
  for line, rows in pairs(RD.r) do
    for _, r in ipairs(rows) do
      nRecipes = nRecipes + 1
      for k = 1, #r[10], 2 do
        local id = r[10][k]
        ent(id).mat = true
        usesOf[id] = usesOf[id] or {}
        usesOf[id][#usesOf[id] + 1] = { line, r[6] }
      end
      if (tonumber(r[8]) or 0) > 0 then ent(r[8]).recipe = { line = line, learn = tonumber(r[4]) or 1 } end
    end
  end
  for _, cat in ipairs({ "food", "water", "hpot", "mpot", "bandage", "ammo" }) do
    for _, r in ipairs(CD[cat] or {}) do
      local e = ent(r[1])
      if not e.cat then e.cat, e.req, e.val, e.label = cat, r[2], r[3], r[4] end
    end
  end
  for _, r in ipairs(PD) do ent(r[1]).pet = { r[2], r[3] } end
  local REAGENT = { [17031] = "MAGE", [17032] = "MAGE", [17020] = "MAGE", [17056] = "MAGE PRIEST", [17028] = "PRIEST", [17029] = "PRIEST",
    [21177] = "PALADIN", [17033] = "PALADIN", [17030] = "SHAMAN", [17057] = "SHAMAN", [17058] = "SHAMAN", [17034] = "DRUID", [17035] = "DRUID",
    [17036] = "DRUID", [17037] = "DRUID", [17038] = "DRUID", [17021] = "DRUID", [17026] = "DRUID", [6265] = "WARLOCK", [5565] = "WARLOCK",
    [16583] = "WARLOCK", [5140] = "ROGUE", [5530] = "ROGUE" }
  for id, who in pairs(REAGENT) do ent(id).reagentOf = who end
  table.sort(order)
  -- the game's item data for each of them (names are not in the repository: the mock gives « objet N »)
  for _, id in ipairs(order) do
    local e = U[id]
    local t = { name = "objet " .. id, stack = 20, quality = 1, sellPrice = 10 + id % 90 }
    if e.cat == "ammo" then t.classID, t.subclassID, t.stack, t.minLevel = 6, (e.label == "bullet") and 3 or 2, 200, e.req
    elseif e.cat then t.classID, t.subclassID, t.minLevel = 0, 1, (e.cat ~= "bandage") and e.req or 0
    elseif e.recipe then t.classID, t.stack = 9, 1
    elseif e.reagentOf then t.classID = 5
    else t.classID = 7 end
    if id % 41 == 0 then t.sellPrice = 0 end                 -- some have no vendor price at all
    M.AddItem(id, t)
  end
  local lsU = {}                   -- level-sensitive part of the universe (swept over every level 10–60)
  for _, id in ipairs(order) do local e = U[id] if e.cat or e.pet or e.reagentOf then lsU[#lsU + 1] = id end end

  -- ------------------------------------------------------------------ QuestieDB (real schema), quests on the real items
  local CRAFT = { 164, 165, 171, 197, 202, 333 }
  local QDB = { quests = {}, items = {} }
  local questsOf = {}              -- id → { quest ids } (oracle)
  local nQ = 0
  local function addQuest(k, item, salt)
    local qid = 50000 + nQ
    nQ = nQ + 1
    local rl = 1 + (k * 7 + salt * 13) % 58
    local q = { name = "Quête d'audit " .. qid, requiredLevel = rl, questLevel = min(60, rl + 2 + k % 4), requiredRaces = 0, requiredClasses = 0 }
    if (k + salt) % 5 == 0 then q.requiredClasses = 2 ^ (CLASS_ID[CLASS_LIST[1 + floor(k / 5) % 9]] - 1) end
    if (k + salt) % 7 == 0 then q.requiredRaces = 178 elseif (k + salt) % 7 == 1 then q.requiredRaces = 77 end
    if (k + salt) % 11 == 0 then q.requiredSkill = { CRAFT[1 + k % #CRAFT], 1 } end
    if (k + salt) % 13 == 0 then q.specialFlags = 1 end
    if (k + salt) % 17 == 0 then q.exclusiveTo = { qid + 1 } end
    local row = QDB.items[item] or { name = "objet " .. item }
    QDB.items[item] = row
    if (k + salt) % 23 == 0 then
      row.startQuest = qid                                   -- the item STARTS the quest
      q.starter = item
    else
      if (k + salt) % 9 == 0 then q.requiredSourceItems = { item } else q.objectives = { nil, nil, { { item, nil } } } end
      row.relatedQuests = row.relatedQuests or {}
      row.relatedQuests[#row.relatedQuests + 1] = qid
    end
    QDB.quests[qid] = q
    questsOf[item] = questsOf[item] or {}
    questsOf[item][#questsOf[item] + 1] = qid
  end
  for k, id in ipairs(order) do
    if k % 4 == 0 then addQuest(k, id, 0) end
    if k % 24 == 0 then addQuest(k, id, 3) end               -- some items serve two quests
  end
  M.InstallQuestieDB(QDB)
  P.ResetQuestie()
  P.QuestState()
  M.Advance(2, 0.05)
  check(P.QuestState() == "fast", "Questie's item → quests link verified on the fixture (" .. tostring(P.QuestState()) .. ")")
  P.RX.BuildNow()
  out(("univers : %d objets (%d composants, %d recettes-objets, %d consommables, %d munitions, %d nourritures de familier, %d composants de classe) ; %d recettes ; %d quêtes sur %d objets")
    :format(#order, (function() local n = 0 for _, e in pairs(U) do if e.mat then n = n + 1 end end return n end)(),
      (function() local n = 0 for _, e in pairs(U) do if e.recipe then n = n + 1 end end return n end)(),
      (function() local n = 0 for _, e in pairs(U) do if e.cat and e.cat ~= "ammo" then n = n + 1 end end return n end)(),
      (function() local n = 0 for _, e in pairs(U) do if e.cat == "ammo" then n = n + 1 end end return n end)(),
      (function() local n = 0 for _, e in pairs(U) do if e.pet then n = n + 1 end end return n end)(),
      (function() local n = 0 for _, e in pairs(U) do if e.reagentOf then n = n + 1 end end return n end)(),
      nRecipes, nQ, (function() local n = 0 for _ in pairs(questsOf) do n = n + 1 end return n end)()))

  -- ------------------------------------------------------------------ the character of this run
  local MANA = { MAGE = true, PRIEST = true, WARLOCK = true, DRUID = true, SHAMAN = true, PALADIN = true, HUNTER = true }
  local HOLD = { hpot = 5, mpot = 5, food = 20, water = 20, bandage = 20 }
  local DIET = { meat = true, fish = true }
  if CLASS == "HUNTER" then
    M.AddItem(2504, { name = "Arc d'audit", stack = 1, sellPrice = 50, classID = 2, subclassID = 2, equipLoc = "INVTYPE_RANGED" })
    M.inventory[18] = { id = 2504, count = 1 }
    M.pet.exists = false                                     -- pet put away: its last known diet is used, its level = his
    B.char.diet = { meat = true, fish = true }
  else
    M.inventory[18] = nil
  end
  local myRace, mySide = 1, 77     -- the mock's character: human, Alliance
  local function grey(L)
    if L <= 5 then return 0 end
    if L <= 39 then return L - floor(L / 10) - 5 end
    if L <= 59 then return L - floor(L / 5) - 1 end
    return L - 9
  end
  local function bit(mask, b) return floor(mask / b) % 2 == 1 end
  -- ORACLE: can this quest still ask him for something? → "now" | "later" | nil
  local function questLive(qid, c)
    local q = QDB.quests[qid]
    local rep = (q.specialFlags or 0) % 2 == 1
    if c.done[qid] and not rep then return nil end
    if q.requiredRaces ~= 0 and not bit(q.requiredRaces, myRace) then return nil end
    if q.requiredClasses ~= 0 and not bit(q.requiredClasses, 2 ^ (CLASS_ID[CLASS] - 1)) then return nil end
    if q.requiredSkill and c.profs and not c.profs[q.requiredSkill[1]] then return nil end
    for _, x in ipairs(q.exclusiveTo or {}) do if c.done[x] then return nil end end
    if q.requiredLevel > c.level + 8 then return "later" end
    if q.questLevel <= grey(c.level) and not rep then return nil end
    return "now"
  end
  local SECOND = { 185, 129, 356 }
  local PRIMARY = { 164, 165, 171, 182, 186, 197, 202, 333, 393 }
  local PAIRS = {}
  for i = 1, #PRIMARY do for j = i + 1, #PRIMARY do PAIRS[#PAIRS + 1] = { PRIMARY[i], PRIMARY[j] } end end
  local function profsFor(L, pair, variant)
    local p = {}
    if pair then
      p[pair[1]] = min(300, (variant % 2 == 0) and L * 5 or floor(L * 2.5))
      p[pair[2]] = min(300, (variant % 2 == 0) and floor(L * 2.5) or L * 5)
    end
    p[185], p[129], p[356] = min(300, L * 4), min(300, L * 3), min(300, L * 2)
    if variant % 5 == 4 then p[129] = nil end               -- sometimes no First Aid at all
    return p
  end
  local doneSets = { {}, {}, {} }
  for qid in pairs(QDB.quests) do
    if qid % 2 == 0 then doneSets[2][qid] = true end
    doneSets[3][qid] = true
  end

  -- ------------------------------------------------------------------ the sweep
  local stats = { verdicts = 0, live = 0, liveBy = { quest = 0, questLater = 0, recipe = 0, consumable = 0, ammo = 0, pet = 0, learn = 0 },
    noUse = 0, noUseBy = {}, bad = 0, keptNoReason = 0, contexts = 0, sellListed = 0 }
  local ALLOWED = { keep = true, sell = true, ah = true, bank = true, use = true, ["destroy?"] = true }
  local GENERIC = "je ne sais pas s'il te sert : je garde"
  local fails, quiet = 0, false
  local function fail(msg)
    fails = fails + 1
    if not quiet and fails <= 12 then check(false, msg) end
  end
  local BATCH = 80
  local function setBatch(ids, from, count)
    for b = 0, 4 do M.bags[b].size = 16 M.bags[b].slots = {} end
    local n = 0
    for i = from, min(#ids, from + BATCH - 1) do
      M.SetBagItem(floor(n / 16), n % 16 + 1, ids[i], count(ids[i]))
      n = n + 1
    end
    B.Rescan()
    return n
  end
  local function sweep(ids, contexts, count)
    for from = 1, #ids, BATCH do
      local n = setBatch(ids, from, count)
      local last = from + n - 1
      for _, c in ipairs(contexts) do
        P.SetOverride({ level = c.level, profs = c.profs, sig = c.sig, done = c.done, log = {} })
        B.Invalidate()
        stats.contexts = stats.contexts + 1
        -- consumables of the batch, per category, best first (the oracle of « best tier / stock »)
        local live = {}
        local byCat = {}
        local pets = {}
        for i = from, last do
          local id = ids[i]
          local e = U[id]
          if e.cat and e.cat ~= "ammo" then
            local usesIt = (e.cat ~= "mpot" and e.cat ~= "water") or MANA[CLASS]
            if usesIt then
              if e.label == "conj" then live[id] = "consumable"
              elseif e.cat == "bandage" then
                local fa = c.profs and c.profs[129]
                if fa and e.req > fa then live[id] = "consumable" else byCat[e.cat] = byCat[e.cat] or {} table.insert(byCat[e.cat], id) end
              elseif e.req > c.level then live[id] = "consumable"
              else byCat[e.cat] = byCat[e.cat] or {} table.insert(byCat[e.cat], id) end
            end
          end
          if e.cat == "ammo" and CLASS == "HUNTER" and e.label == "arrow" then live[id] = "ammo" end
          if e.pet and CLASS == "HUNTER" and DIET[e.pet[1]] and e.pet[2] >= c.level - 30 then pets[#pets + 1] = id end
          if e.recipe and c.profs and c.profs[e.recipe.line] then live[id] = live[id] or "learn" end
          for _, u in ipairs(usesOf[id] or {}) do
            local rank = c.profs and c.profs[u[1]]
            if rank and u[2] > rank then live[id] = live[id] or "recipe" end
          end
          if not c.profs and usesOf[id] then live[id] = live[id] or "recipe" end      -- professions unknown: no blind sale
          for _, qid in ipairs(questsOf[id] or {}) do
            local s = questLive(qid, c)
            if s == "now" then live[id] = "quest" elseif s == "later" and live[id] ~= "quest" then live[id] = live[id] or "questLater" end
          end
        end
        for cat, list in pairs(byCat) do
          table.sort(list, function(a, b2) if U[a].val ~= U[b2].val then return U[a].val > U[b2].val end return a < b2 end)
          local better = 0
          for _, id in ipairs(list) do
            if better < HOLD[cat] then live[id] = live[id] or "consumable" end
            better = better + count(id)
          end
        end
        table.sort(pets, function(a, b2) if U[a].pet[2] ~= U[b2].pet[2] then return U[a].pet[2] > U[b2].pet[2] end return a < b2 end)
        local pb = 0
        for _, id in ipairs(pets) do
          if pb < 40 then live[id] = live[id] or "pet" end
          pb = pb + count(id)
        end
        -- the verdicts
        for i = from, last do
          local id = ids[i]
          local v = B.Verdict(id)
          stats.verdicts = stats.verdicts + 1
          local have = count(id)
          if not ALLOWED[v.action] or v.have ~= have or (v.keep + v.surplus ~= have) or (v.confirm and v.action ~= "keep")
            or type(B.Line(v)) ~= "string" or B.Line(v) == "" then
            stats.bad = stats.bad + 1
            fail(("malformed verdict for %d at %d: %s keep %s surplus %s"):format(id, c.level, tostring(v.action), tostring(v.keep), tostring(v.surplus)))
          end
          local sellish = v.action == "sell" or v.action == "ah" or v.action == "destroy?" or v.confirm ~= nil or v.surplus > 0
          local why = live[id]
          if why then
            stats.live = stats.live + 1
            stats.liveBy[why] = (stats.liveBy[why] or 0) + 1
            if sellish then
              fail(("LIVE USE SOLD: %d (%s) %s level %d %s → %s : %s"):format(id, why, CLASS, c.level, c.sig, tostring(v.action), tostring(B.Line(v))))
            end
          else
            stats.noUse = stats.noUse + 1
            local kind = (sellish and (v.confirm and "confirm" or v.action)) or (v.claimed and "kept: " .. tostring(v.keeper)) or "kept, reason stated"
            if not sellish and (v.why == nil or v.why == "" or v.why == GENERIC) then
              stats.keptNoReason = stats.keptNoReason + 1
              kind = "KEPT FOR NOTHING"
              fail(("kept without a reason: %d %s level %d %s : %s"):format(id, CLASS, c.level, c.sig, tostring(v.why)))
            end
            stats.noUseBy[kind] = (stats.noUseBy[kind] or 0) + 1
          end
        end
        -- no live item in what a click can sell
        if c.lists then
          local sl = B.SellList()
          for _, l in ipairs({ sl.list, sl.apart }) do
            for _, e in ipairs(l) do
              stats.sellListed = stats.sellListed + 1
              if live[e.id] then fail(("live item %d in a sell list (%s level %d)"):format(e.id, CLASS, c.level)) end
            end
          end
        end
      end
    end
  end
  local function ahStub(mode)
    rawset(_G, "Auctionator", { API = { v1 = { GetAuctionPriceByItemID = function(_, id)
      if mode == 0 or id % 3 == 2 then return nil end
      return (id % 3 == 0) and 5000 or 3
    end } } })
  end
  -- sweep 1 — level-sensitive items (consumables, ammunition, pet food, class reagents) × EVERY level 10–60 × 3 profession sets
  local myPairs = {}
  for i = 0, 11 do myPairs[#myPairs + 1] = PAIRS[1 + (classIdx - 1 + i * 3) % #PAIRS] end
  local ctx1 = {}
  for L = 10, 60 do
    for k = 1, 3 do
      local pair = myPairs[k]
      ctx1[#ctx1 + 1] = { level = L, profs = profsFor(L, pair, L + k), sig = ("%d+%d#%d"):format(pair[1], pair[2], (L + k) % 5),
        done = doneSets[1 + (L + k) % 3], lists = (L % 10 == 0) }
    end
  end
  ahStub(1)
  sweep(lsU, ctx1, function(id) return (id % 4 == 0) and 3 or 20 end)
  local v1 = stats.verdicts
  -- sweep 2 — the WHOLE universe × levels 10, 20, … 60 × 12 profession pairs (36 pairs over the 9 class runs, each 3 times),
  -- + « professions not read yet » and « no profession »
  local ctx2 = {}
  for li, L in ipairs({ 10, 20, 30, 40, 50, 60 }) do
    for k, pair in ipairs(myPairs) do
      ctx2[#ctx2 + 1] = { level = L, profs = profsFor(L, pair, li + k), sig = ("%d+%d#%d"):format(pair[1], pair[2], (li + k) % 5),
        done = doneSets[1 + (li + k) % 3], lists = (k == 1) }
    end
  end
  ctx2[#ctx2 + 1] = { level = 30, profs = false, sig = "inconnus", done = doneSets[1], lists = true }
  ctx2[#ctx2 + 1] = { level = 40, profs = profsFor(40, nil, 0), sig = "aucun", done = doneSets[2], lists = true }
  sweep(order, ctx2, function() return 20 end)
  out(("%s : %d avis (%d sur les objets sensibles au niveau × niveaux 10–60, %d sur tout l'univers) ; %d personnages différents (niveau × métiers × quêtes faites), paires de métiers : %s")
    :format(CLASS, stats.verdicts, v1, stats.verdicts - v1, #ctx1 + #ctx2,
      (function() local t = {} for _, pr in ipairs(myPairs) do t[#t + 1] = pr[1] .. "+" .. pr[2] end return table.concat(t, " ") end)()))
  out(("A. usage prévu vivant : %d cas (quête %d, quête plus tard %d, recette %d, consommable %d, munitions %d, familier %d, recette à apprendre %d) — vendus / à confirmer : %d")
    :format(stats.live, stats.liveBy.quest, stats.liveBy.questLater, stats.liveBy.recipe, stats.liveBy.consumable, stats.liveBy.ammo,
      stats.liveBy.pet, stats.liveBy.learn, fails - stats.keptNoReason - stats.bad))
  local kinds = {}
  for k, n in pairs(stats.noUseBy) do kinds[#kinds + 1] = { k, n } end
  table.sort(kinds, function(a, b2) return a[2] > b2[2] end)
  local parts = {}
  for i, k in ipairs(kinds) do if i <= 9 then parts[#parts + 1] = k[1] .. " " .. k[2] end end
  out(("B. aucun usage possible : %d cas — %s — gardés sans raison : %d"):format(stats.noUse, table.concat(parts, ", "), stats.keptNoReason))
  out(("C. avis mal formés : %d ; entrées de listes de vente contrôlées : %d"):format(stats.bad, stats.sellListed))
  check(stats.live > 1000 and stats.noUse > 1000, "the audit saw both live uses and useless items")
  check(fails == 0, ("audit: %d violation(s)"):format(fails))

  -- ------------------------------------------------------------------ D. the same items with the whole base re-read (« index »)
  -- and without Questie: the quest oracle must hold with the index; without Questie no non-grey reaches the one-click
  local qItems = {}
  for id in pairs(questsOf) do qItems[#qItems + 1] = id end
  table.sort(qItems)
  B.db.qidx = false
  local QDB2 = { quests = QDB.quests, items = {} }
  for id, row in pairs(QDB.items) do QDB2.items[id] = { name = row.name, startQuest = row.startQuest } end
  M.InstallQuestieDB(QDB2)
  P.ResetQuestie()
  P.SetOverride(nil)
  P.QuestState()
  M.Advance(2, 0.05)
  check(P.QuestState() == "unverified", "without the item → quests link: unverified")
  M.Hw(function() T:UserAction(function() P.QuestScan() end) end)
  local frames = 0
  while P.QuestState() == "scan" and frames < 4000 do M.Advance(0.05, 0.05) frames = frames + 1 end
  check(P.QuestState() == "index", "whole base re-read in " .. frames .. " images (20 quests per image)")
  local f0, live0 = fails, stats.live
  local ctx3 = {}
  for li, L in ipairs({ 12, 27, 33, 41, 58 }) do
    ctx3[#ctx3 + 1] = { level = L, profs = profsFor(L, myPairs[li], li), sig = "index" .. li, done = doneSets[1 + li % 3], lists = true }
  end
  sweep(qItems, ctx3, function() return 20 end)
  out(("D. base relue en entier (%d quêtes, %d images) : %d usages vivants de plus contrôlés, %d violation(s)"):format(nQ, frames, stats.live - live0, fails - f0))
  check(fails == f0, "index path: same guarantees")
  -- ------------------------------------------------------------------ E. is the audit able to FAIL? (three deliberately broken predictors)
  -- the same sweep must catch: the quest veto switched off, the profession predictor answering « not yours » for
  -- everything, the consumable plan calling the best tier surplus — otherwise the zeros above prove nothing
  quiet = true
  local caught = {}
  local ctxE = { { level = 30, profs = profsFor(30, myPairs[1], 0), sig = "mutation", done = doneSets[1], lists = true } }
  local function mutation(name, ids, breakIt, repair)
    local fBefore = fails
    breakIt()
    sweep(ids, ctxE, function() return 20 end)
    repair()
    caught[#caught + 1] = name .. " " .. (fails - fBefore)
    check(fails > fBefore, "the audit catches a broken predictor: " .. name .. " (" .. (fails - fBefore) .. " violations)")
  end
  local veto = P.Veto
  mutation("veto des quêtes coupé", qItems, function() P.Veto = function() return nil end end, function() P.Veto = veto end)
  local mats = {}
  for _, id in ipairs(order) do if U[id].mat and not U[id].cat then mats[#mats + 1] = id end end
  B.RegisterKeeper("mutation", function(id) if U[id] and U[id].mat then return 0, "mutation : pas ton métier" end end)
  local profUse = P.ProfUse
  local I = B._int
  local pre = I.PRE["métiers"]
  mutation("prédicteur de métier muet", mats, function() I.PRE["métiers"] = function() return false end end, function() I.PRE["métiers"] = pre end)
  B.UnregisterKeeper("mutation")
  local cons = {}
  for _, id in ipairs(order) do if U[id].cat and U[id].cat ~= "ammo" then cons[#cons + 1] = id end end
  local preC = I.PRE["consommable"]
  B.RegisterKeeper("mutation", function(id) if U[id] and U[id].cat then return 0, "mutation : en trop" end end)
  mutation("plan des consommables coupé", cons, function() I.PRE["consommable"] = function() return false end end, function() I.PRE["consommable"] = preC end)
  B.UnregisterKeeper("mutation")
  quiet = false
  out("E. l'audit sait échouer (prédicteur cassé exprès → violations) : " .. table.concat(caught, " ; "))
  local _ = profUse
  for _, n in ipairs({ "LibQuestieDB", "QuestDB", "NpcDB", "ObjectDB", "ItemDB" }) do rawset(_G, n, nil) end
  P.ResetQuestie()
  local nonGrey, apart = 0, 0
  for from = 1, #qItems, BATCH do
    setBatch(qItems, from, function() return 20 end)
    P.SetOverride({ level = 30, profs = profsFor(30, myPairs[1], 1), sig = "sans", done = {}, log = {} })
    B.Invalidate()
    local sl = B.SellList()
    nonGrey = nonGrey + #sl.list
    apart = apart + #sl.apart
  end
  out(("D. sans Questie : %d objet(s) non gris dans le clic unique, %d listés « à confirmer »"):format(nonGrey, apart))
  check(nonGrey == 0 and apart > 0, "no Questie: nothing but greys in the one-click list")
  P.SetOverride(nil)
  B.db.qidx = false
  rawset(_G, "Auctionator", nil)
  for b = 0, 4 do M.bags[b].slots = {} end
  B.Rescan()
  B.Invalidate()
end
