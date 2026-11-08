-- Harness scenario (4.1 BUGHUNT): « réduis la dépendance de TreeAI aux autres addons ». Without Questie / SPF:
--  1. the NPC book (Finder.lua, T.NpcBook) learns the quest giver / turn-in NPC / gossip NPC the player talks to,
--     at the player's place, and gives them back in the compass point shape (mapID, x, y in 0..1);
--  2. the quest tracker (Tracker.lua) points a finished quest at its learned turn-in NPC when neither the game
--     (GetNextWaypoint) nor Questie knows it;
--  3. the highlight (Highlight.lua) marks the monsters that REALLY dropped an objective item (Stats' loot record)
--     on their name plates without Questie.
-- Fake names only. lua5.1 tests/wow/harness.lua -q --no-storm --flavor forever --scenario tests/wow/deps_scenario.lua wow_addon/TreeAI
return function(M, ctx)
  local T = TreeAI
  local check = ctx.check
  check(rawget(_G, "QuestieLoader") == nil and rawget(_G, "ShortestPathForever") == nil, "no Questie / SPF here")
  local NB = T.NpcBook
  if not check(NB, "T.NpcBook present") then return end
  M.LeaveCombat()

  -- 1) talking to NPCs ------------------------------------------------------------------------------------------
  local qid = 4321
  rawset(_G, "GetQuestID", function() return qid end)
  M.units.npc = { name = "Gardienne Ombrefeuille", guid = "Creature-0-1-2-3-3388-0000AAAA" }
  ctx.fire("QUEST_DETAIL")
  local s = NB.Starter(qid)
  check(s and s.name == "Gardienne Ombrefeuille" and s.id == 3388 and s.roles:find("q"), "donneur appris : " .. tostring(s and s.name))
  check(s and s.mapID and s.x and s.x >= 0 and s.x <= 1 and s.y >= 0 and s.y <= 1, "donneur : carte + x, y en 0..1")
  M.units.npc = { name = "Éclaireur Brisevent", guid = "Creature-0-1-2-3-3390-0000BBBB" }
  ctx.fire("QUEST_COMPLETE")
  local f = NB.Finisher(qid)
  check(f and f.name == "Éclaireur Brisevent", "destinataire appris : " .. tostring(f and f.name))
  local pts = NB.Points(qid, "end")
  check(#pts == 1 and pts[1].src == "appris" and pts[1].mapID == f.mapID, "points du destinataire (forme boussole)")
  M.units.npc = { name = "Marchande Cuivrette", guid = "Creature-0-1-2-3-3410-0000CCCC" }
  ctx.fire("GOSSIP_SHOW")
  local v = NB.Near("g")
  check(#v >= 1 and v[1].name == "Marchande Cuivrette", "PNJ à dialogue appris (Near g)")
  check(NB.ByName("marchande cuivrette") and NB.ByName("marchande cuivrette").id == 3410, "ByName (casse ignorée)")
  M.units.npc = { name = "Tableau des avis", guid = "GameObject-0-1-2-3-1700-0000DDDD" }
  ctx.fire("QUEST_DETAIL")
  local o = NB.Get(-1700)
  check(o and o.roles:find("o"), "objet donneur : clé négative, rôle o")
  -- in combat: nothing learned
  M.units.npc = { name = "Garde du combat", guid = "Creature-0-1-2-3-3999-0000EEEE" }
  M.EnterCombat()
  ctx.fire("GOSSIP_SHOW")
  M.LeaveCombat()
  check(NB.Get(3999) == nil, "rien appris en combat")
  M.units.npc = nil
  rawset(_G, "GetQuestID", nil)

  -- 1b) quests the GAME offers on the map (C_QuestLine, the world map « ! »), without Questie
  local GQ = T.GameQuests
  local hadQL = rawget(_G, "C_QuestLine")
  local requested
  rawset(_G, "C_QuestLine", {
    RequestQuestLinesForMap = function(m) requested = m end,
    GetAvailableQuestLines = function(m)
      return { { questID = 881, questName = "La revanche de Gann", questLineName = "Les Tarides", x = 0.52, y = 0.31, startMapID = m },
        { questID = 882, questName = "Cachée", x = 0.1, y = 0.1, isHidden = true } }
    end,
  })
  local av = GQ and GQ.Available(1413) or {}
  check(requested == 1413 and #av == 1 and av[1].questID == 881 and av[1].name == "La revanche de Gann" and av[1].x == 0.52
    and av[1].mapID == 1413 and av[1].src == "jeu", "quêtes à prendre données par le jeu (C_QuestLine), cachées exclues")
  rawset(_G, "C_QuestLine", hadQL)

  -- 2) the tracker: a finished quest with no game / Questie point → the learned turn-in NPC ----------------------
  local Tr = T.modules and T.modules.Tracker
  local Q = T.modules and T.modules.Quests
  check(Tr and Q and Tr.Update, "Tracker + Quests present")
  if Tr and Q and Tr.Update then
    local list = { { id = qid, title = "La revanche de Gann", complete = true, level = 26, obj = {} } }
    local oldList = Q.lastList
    Q.lastList = list
    local CQ = rawget(_G, "C_QuestLog")
    local wp = CQ and CQ.GetNextWaypoint
    if CQ then CQ.GetNextWaypoint = function() return nil end end
    pcall(Tr.Update, true)
    local it
    for _, x in ipairs(Tr.items or {}) do if x.key == qid then it = x end end
    check(it and it.p and it.p.src == "appris" and it.p.name == "Éclaireur Brisevent",
      "suivi : quête finie → destinataire appris (" .. tostring(it and it.p and it.p.name) .. ")")
    if CQ then CQ.GetNextWaypoint = wp end
    Q.lastList = oldList
  end

  -- 3) the highlight: monsters that really dropped the objective item (Stats) -----------------------------------
  local H = T.modules and T.modules.Highlight
  local St = T.Stats
  local A = St and St.Account and St.Account()
  check(H and A, "Highlight + Stats present")
  if H and A then
    A.names["pulpe de bois"] = 5170
    A.drops[3120] = { n = 4, nm = "Sylvain flétri", i = { [5170] = { 4, 2, 1 } } }
    A.drops[3121] = { n = 3, nm = "Loup gris", i = { [2672] = { 3, 3 } } }
    M.quests = {
      { header = "Les Tarides" },
      { title = "La revanche de Gann", level = 26,
        objectives = { { text = "Pulpe de bois : 1/6", done = false, have = 1, need = 6, type = "item" } } },
    }
    ctx.fire("QUEST_LOG_UPDATE")
    M.Advance(2)
    if Q and Q.Update then pcall(Q.Update) end
    H.Rebuild()
    local mn = H.Data().mobNpc
    check(mn[3120] and mn[3120].learned and mn[3120].obj == "Pulpe de bois", "surbrillance : monstre appris au butin (sans Questie)")
    check(mn[3121] == nil, "surbrillance : un monstre qui lâche autre chose n'est pas marqué")
  end
end
