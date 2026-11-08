-- Scenario for the Quests module (agent Q), run by tests/wow/test_quests.py on both API flavours.
-- Fake names only.
return function(M, ctx)
  local T = TreeAI
  local Q = T and T:GetModule("Quests")
  ctx.check(Q, "Quests module registered")
  if not Q then return end
  -- (QUICK1) the « Raccourcis » bar carries the quest item when on: this scenario tests the Quests button itself
  if T.Quick and T.Quick.db then T.Quick.db.btnItem = false end
  local ZONE = "Contreforts de Hautebrande"
  M.player.zone = ZONE
  M.AddItem(9001, { name = "Totem de purification", stack = 1, type = "Quête", classID = 12 })
  M.AddItem(9002, { name = "Lettre tachée de sang", stack = 1, type = "Quête", classID = 12, questItem = true, startsQuest = 777 })
  M.SetBagItem(2, 1, 9002, 1)
  M.quests = {
    { header = ZONE },
    { title = "Sang de lion", level = 26, complete = false, xp = 2400, item = 9001,
      objectives = { { text = "Sang de lion : 2/10", done = false, have = 2, need = 10, type = "item" } } },
    { title = "Les ours de Hautebrande", level = 25, complete = false, xp = 2000,
      objectives = { { text = "Ours enragé tué : 7/8", done = false, have = 7, need = 8 } } },
    { title = "Le courrier", level = 24, complete = true, xp = 1500, objectives = {} },
    { title = "Vieille affaire", level = 15, complete = false, xp = 100,
      objectives = { { text = "Gnoll tué : 0/5", done = false, have = 0, need = 5 } } },
    { header = "Les Hinterlands" },
    { title = "Le chef ogre", level = 32, complete = false, elite = true, group = 4, xp = 4000,
      objectives = { { text = "Chef ogre tué : 0/1", done = false, have = 0, need = 1 } } },
    { header = "Orneval" },
    { title = "Retour à Astranaar", level = 22, complete = true, xp = 900, objectives = {} },
  }
  M.group = {}
  ctx.fire("QUEST_LOG_UPDATE")
  M.Advance(2)
  local a = Q.Update()
  ctx.check(a.count == 6, "6 quests scanned (headers skipped): " .. tostring(a.count))
  ctx.check(#a.turnIn == 2, "2 to turn in")
  ctx.check(a.turnInByZone[ZONE] and a.turnInByZone["Orneval"], "turn-ins grouped by zone")
  ctx.check(a.here[1] and a.here[1].title == "Les ours de Hautebrande", "closest to completion first: " .. tostring(a.here[1] and a.here[1].title))
  ctx.check(a.grey[1] and a.grey[1].title == "Vieille affaire", "grey quest detected")
  ctx.check(a.hard[1] and a.hard[1].title == "Le chef ogre", "elite/red quest flagged")
  ctx.check(Q.Remaining(a.here[1]) == "Ours enragé tué 7/8", "compact objectives: " .. Q.Remaining(a.here[1]))
  ctx.check(Q.nextText and Q.nextText:find("^Étape : rendre « Le courrier »"), "next step = turn in here: " .. tostring(Q.nextText))
  local e = Q.xp
  ctx.check(e and e.turnIn == 2400 and e.quests and e.quests > 0, "xp estimate: " .. tostring(e and e.turnIn))
  -- parse both objective formats
  local n, h, nn = Q.ParseObjective("2/10 Sang de lion")
  ctx.check(n == "Sang de lion" and h == 2 and nn == 10, "modern objective format")
  -- progress toast
  M.quests[2].objectives[1] = { text = "Sang de lion : 3/10", done = false, have = 3, need = 10, type = "item" }
  Q.Update()
  local toast
  for _, s in ipairs(T:GetSuggestions()) do if s.text == "Sang de lion : 3/10" then toast = s end end
  ctx.check(toast, "progress toast « Sang de lion : 3/10 »")
  -- quest item button (secure, out of combat only)
  local b = Q.GetItemButton()
  ctx.check(b and b:GetAttribute("type") == "item" and b:GetAttribute("item") == "item:9001", "secure item button set")
  ctx.check(b and b:IsProtected(), "button uses SecureActionButtonTemplate")
  -- in combat: changing the item must be deferred (no SetAttribute on a secure frame in combat)
  M.EnterCombat()
  M.quests[2].item = nil
  Q.Update()
  ctx.check(b:GetAttribute("item") == "item:9001", "no attribute change in combat")
  M.LeaveCombat()
  ctx.fire("PLAYER_REGEN_ENABLED")
  M.Advance(2)
  ctx.check(b:GetAttribute("item") == nil, "button cleared after combat")
  -- leaving the zone with unfinished objectives ((QUESTQA) without a route: with one, its stops say it already)
  local PL = T.Planner
  local oldAS = PL and PL.GetActiveSteps
  if PL then PL.GetActiveSteps = function() return nil end end
  M.player.zone = "Orneval"
  ctx.fire("ZONE_CHANGED_NEW_AREA")
  M.Advance(2)
  local left, turnin
  for _, s in ipairs(T:GetSuggestions()) do
    if s.text:find("^Finis « ") and s.text:find("avant de quitter " .. ZONE, 1, true) then left = s end
    if s.text:find("^À rendre ici : « ") or s.text:find("^%d+ quêtes à rendre ici : « ") then turnin = s end
  end
  if PL then PL.GetActiveSteps = oldAS end
  ctx.check(left, "« finis ici avant de partir » on leaving")
  ctx.check(turnin, "« à rendre ici » on entering")
  -- loot that matters for a quest
  M.player.zone = ZONE
  Q.Update()
  M.loot = { { name = "Sang de lion", count = 1, questItem = true }, { name = "Griffe cassée" } }
  local useful = Q.CheckLoot()
  ctx.check(#useful == 1 and useful[1]:find("Sang de lion"), "quest loot noted")
  -- quest starter item in bags
  local starters = Q.CheckBags()
  ctx.check(#starters == 1, "quest-starting item found in bags: " .. #starters)
  -- report + summary
  M.output = {}
  M.RunSlash("/tai quetes")
  ctx.check(ctx.out("À rendre : Le courrier"), "report: turn-in line")
  ctx.check(ctx.out("À faire à 4 : Le chef ogre"), "report: group flag")
  ctx.check(ctx.out("Grise"), "report: grey line")
  ctx.check(ctx.out("Étape : "), "report: next step line")
  Q.WriteSummary()
  local s = T:Summary().quests
  ctx.check(s and s.count == 6 and s.complete == 2 and #s.list == 6 and s.next, "summary.quests written")
  ctx.check(s and s.list[1].total == 1 and s.list[1].done == 0, "summary objectives done/total")
  -- Questie absent: features silently off
  ctx.check(Q.QuestieScan() == nil, "no Questie → nil")
  -- fake Questie DB (read-only use): available quests here + next level + chain + drops
  local fakeDB = {
    QuestPointers = { [501] = true, [502] = true, [503] = true, [1002] = true },
    QueryQuestSingle = function(id, key)
      local d = {
        [501] = { name = "Aide au fermier", zoneOrSort = 267, requiredLevel = 24, questLevel = 26, startedBy = { { 9 } } },
        [502] = { name = "Bientôt dispo", zoneOrSort = 267, requiredLevel = 28, questLevel = 30 },
        [503] = { name = "Grise ailleurs", zoneOrSort = 1, requiredLevel = 5, questLevel = 8 },
        [1002] = { name = "Lion", objectives = { nil, nil, { { 9001 } } }, nextQuestInChain = 501 },
      }
      return d[id] and d[id][key]
    end,
    QueryNPCSingle = function(id, key) if key == "name" then return "PNJ" .. id end end,
    QueryItemSingle = function(id, key) if key == "npcDrops" then return { 7, 8 } end end,
    IsDoable = function() return true end,
  }
  M.player.level = 27
  rawset(_G, "QuestieLoader", { _modules = { QuestieDB = fakeDB } })
  local mapAPI = C_Map
  local oldArea = mapAPI and mapAPI.GetAreaInfo
  if mapAPI then mapAPI.GetAreaInfo = function(id) return id == 267 and ZONE or "Ailleurs" end end
  local qs = Q.QuestieScan(true)
  ctx.check(mapAPI == nil or (qs and qs.hereCount == 1 and qs.here[1].title == "Aide au fermier" and qs.here[1].giver == "PNJ9"),
    "Questie: available quest here")
  ctx.check(mapAPI == nil or (qs and qs.nextLevelCount == 1), "Questie: unlocks at next level")
  ctx.check(Q.QuestieNext(1002) == "Suite : « Aide au fermier » chez PNJ9", "Questie: chain next: " .. tostring(Q.QuestieNext(1002)))
  ctx.check(Q.QuestieDrops(1002) and Q.QuestieDrops(1002):find("PNJ7, PNJ8"), "Questie: drops")
  if mapAPI then mapAPI.GetAreaInfo = oldArea end
  rawset(_G, "QuestieLoader", nil)
  M.player.level = 26
end
