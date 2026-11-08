-- Scenario (agent DATA): quest data truth + corpse run. Fake names only.
-- lua5.1 tests/wow/harness.lua --allow C_DeathInfo --allow C_TooltipInfo --scenario tests/wow/data_scenario.lua wow_addon/TreeAI
--  1. a mob that only serves a quest NOT in the log (Questie knows it) -> no badge / no Detect unit / no drop line / no point
--  2. same-titled quest variants: only the questID in the log counts; party member's tooltip lines are labelled
--  3. objective finished -> its badge, drop line and map points disappear (no « all objectives » fallback)
--  4. corpse run: « Cadavre : … m au NE (≈ … s) » on top of « Maintenant », compass aims at the corpse even
--     when the player is a ghost, safe-spot hint, everything cleared on revive (custom point restored)
return function(M, ctx)
  local check = ctx.check
  local T = TreeAI
  local D, C, H, S = T.Detect, T.Compass, T:GetModule("Highlight"), T.Speed
  local Tr = T:GetModule("Tracker")
  check(D and C and H and S and Tr, "Detect + Compass + Highlight + Speed + Tracker loaded")
  if not (D and C and H and S and Tr) then return end
  M.LeaveCombat()
  M.instance = false
  M.pos = { mapID = 1424, x = 0.5, y = 0.5 }
  H.db.plates, H.db.plateDrops, H.db.plateTooltipScan = true, true, true

  -- quest log: « Les ours » (2010) = kill 3001 + loot 9201 (dropped by 3005) ; « Le courrier » (2020)
  M.AddItem(9201, { name = "Griffe d'ours", type = "Quête", classID = 12, stack = 20 })
  M.quests = {
    { header = "Contreforts" },
    { id = 2010, title = "Les ours", level = 25, objectives = {
      { text = "Ours enragé tué : 2/8", done = false, have = 2, need = 8, type = "monster" },
      { text = "Griffe d'ours : 1/5", done = false, have = 1, need = 5, type = "item" } } },
    { id = 2020, title = "Le courrier", level = 24, objectives = { { text = "Paquet scellé : 0/1", done = false, have = 0, need = 1, type = "item" } } },
  }
  -- Questie: 2002 = the OTHER faction's « Les ours » (Ours sombre 3060), 5555 = a quest you do not have (Loup gris 4001)
  local NPC = { [3001] = "Ours enragé", [3005] = "Ours noir", [3060] = "Ours sombre", [4001] = "Loup gris" }
  local QDB = {
    QueryQuestSingle = function(id, key)
      if key == "objectives" then
        if id == 2010 then return { { { 3001 } }, nil, { { 9201 } } } end
        if id == 2002 then return { { { 3060 } } } end
        if id == 5555 then return { { { 4001 } } } end
      end
      if key == "name" then return ({ [2010] = "Les ours", [2002] = "Les ours", [5555] = "La meute" })[id] end
    end,
    QueryItemSingle = function(id, key)
      if key == "name" then local it = M.items[id] return it and it.name end
      if key == "npcDrops" and id == 9201 then return { 3005 } end
    end,
    QueryNPCSingle = function(id, key)
      if key == "name" then return NPC[id] end
      if key == "spawns" then
        local p = ({ [3001] = { 40, 40 }, [3005] = { 60, 60 }, [3060] = { 70, 30 }, [4001] = { 20, 20 } })[id]
        return p and { [267] = { p } } or nil
      end
    end,
  }
  local ZDB = { GetUiMapIdByAreaId = function(_, area) if area == 267 then return 1424 end end }
  local oldQL = rawget(_G, "QuestieLoader")
  rawset(_G, "QuestieLoader", { _modules = { QuestieDB = QDB, ZoneDB = ZDB } })
  local Q = T:GetModule("Quests")
  if Q and Q.Update then Q.Update() end
  D.RefreshQuests()
  H.Rebuild()

  -- nameplates
  local plates = {}
  local oldNP = rawget(_G, "C_NamePlate")
  rawset(_G, "C_NamePlate", {
    GetNamePlateForUnit = function(u)
      if not M.units[u] then return nil end
      plates[u] = plates[u] or CreateFrame("Frame", nil, UIParent)
      plates[u].namePlateUnitToken = u
      return plates[u]
    end,
    GetNamePlates = function() local t = {} for u, f in pairs(plates) do if M.units[u] then t[#t + 1] = f end end return t end,
  })
  local hadQL = rawget(_G, "C_QuestLog")
  local QLg = hadQL or {}
  rawset(_G, "C_QuestLog", QLg)
  local oldRel = QLg.UnitIsRelatedToActiveQuest
  QLg.UnitIsRelatedToActiveQuest = nil   -- API absent: only IDs / exact names may decide
  M.units.nameplate1 = { name = "Ours enragé", guid = "Creature-0-0-0-0-3001-1", hostile = true, level = 25 }
  M.units.nameplate2 = { name = "Ours noir", guid = "Creature-0-0-0-0-3005-1", hostile = true, level = 25 }
  M.units.nameplate3 = { name = "Ours sombre", guid = "Creature-0-0-0-0-3060-1", hostile = true, level = 25 }
  M.units.nameplate4 = { name = "Loup gris", guid = "Creature-0-0-0-0-4001-1", hostile = true, level = 25 }
  M.units.nameplate5 = { name = "Ours", guid = "Creature-0-0-0-0-3099-1", hostile = true, level = 25 }
  for i = 1, 5 do ctx.fire("NAME_PLATE_UNIT_ADDED", "nameplate" .. i) end
  M.Advance(0.5)
  local _, info = H.Plates()
  check(info.nameplate1 and info.nameplate1.how == "kill", "kill objective (in log, by NPC id): badge")
  check(info.nameplate2 and info.nameplate2.how == "drop", "drop source of an unfinished item objective: badge")
  check(not info.nameplate3, "same-titled quest variant NOT in log (Ours sombre): no badge")
  check(not info.nameplate4, "quest not in the log (Loup gris): no badge")
  check(not info.nameplate5, "« Ours » is not « Ours enragé »: no fuzzy badge")
  check(D.QuestUnit("nameplate4") == nil and D.QuestUnit("nameplate3") == nil, "Detect: no quest unit for quests not in log")
  local q1 = D.QuestUnit("nameplate1")
  check(q1 and q1.questID == 2010 and q1.how == "kill" and q1.left == 6, "Detect: unit keyed by questID 2010, 6 left")
  local q2 = D.QuestUnit("nameplate2")
  check(q2 and q2.how == "drop" and q2.item == 9201 and q2.left == 4, "Detect: drop unit via Questie npcDrops (item 9201)")
  local drops = Tr.NeededDrops()
  check(drops["ours noir"] and drops["ours noir"].questID == 2010 and not drops["loup gris"] and not drops["ours sombre"],
    "Tracker drop lines only for in-log unfinished item objectives")
  local qlog
  for _, q in ipairs((Q and Q.lastList) or {}) do if q.id == 2010 then qlog = q end end
  local pts = qlog and C.QuestPoints(qlog, { mapID = 1424, x = 0.5, y = 0.5 }) or {}
  local names = {}
  for _, p in ipairs(pts) do names[p.name or "?"] = true end
  check(names["Ours enragé"] and names["Ours noir"] and not names["Ours sombre"] and not names["Loup gris"],
    "compass/radar points: only this questID's objectives (" .. #pts .. ")")

  -- party member's quest in the game tooltip: labelled with the member, never « my » quest
  rawset(_G, "C_TooltipInfo", { GetUnit = function(u)
    if u == "nameplate6" then
      return { lines = { { type = 0, leftText = "Rôdeur des collines" }, { type = 17, leftText = "La traque" },
        { type = 18, leftText = "Amie" }, { type = 8, leftText = "Rôdeur des collines tué : 1/6", completed = false } } }
    end
  end })
  M.units.nameplate6 = { name = "Rôdeur des collines", guid = "Creature-0-0-0-0-4100-1", hostile = true, level = 25 }
  ctx.fire("NAME_PLATE_UNIT_ADDED", "nameplate6")
  _, info = H.Plates()
  check(info.nameplate6 and info.nameplate6.how == "party" and info.nameplate6.e.who == "Amie",
    "party member's objective labelled with the member")
  H.UpdateCompass()
  check(H.nearCount == 2, "compass « objectif proche » ignores the member's mob: " .. tostring(H.nearCount))
  rawset(_G, "C_TooltipInfo", nil)

  -- objective finished: bear kills 8/8 -> bear badge gone, its points gone, claws still shown
  M.quests[2].objectives[1] = { text = "Ours enragé tué : 8/8", done = true, have = 8, need = 8, type = "monster" }
  ctx.fire("QUEST_LOG_UPDATE")
  M.Advance(1)
  if Q and Q.Update then Q.Update() end
  _, info = H.Plates()
  check(not info.nameplate1, "finished objective: kill badge gone " .. (info.nameplate1 and (tostring(info.nameplate1.how) .. " " .. tostring(info.nameplate1.e.quest) .. " " .. tostring(info.nameplate1.e.obj) .. " " .. tostring(info.nameplate1.e.done)) or ""))
  check(info.nameplate2 and info.nameplate2.how == "drop", "unfinished item objective: drop badge stays")
  check(D.QuestUnit("nameplate1") == nil, "finished objective: Detect unit gone")
  for _, q in ipairs((Q and Q.lastList) or {}) do if q.id == 2010 then qlog = q end end
  pts = qlog and C.QuestPoints(qlog, { mapID = 1424, x = 0.5, y = 0.5 }) or {}
  names = {}
  for _, p in ipairs(pts) do names[p.name or "?"] = true end
  check(names["Ours noir"] and not names["Ours enragé"], "finished objective: its map points gone (no fallback)")
  -- claws done too -> quest complete: no drop line anywhere
  M.quests[2].objectives[2] = { text = "Griffe d'ours : 5/5", done = true, have = 5, need = 5, type = "item" }
  M.quests[2].complete = true
  ctx.fire("QUEST_LOG_UPDATE")
  M.Advance(1)
  if Q and Q.Update then Q.Update() end
  _, info = H.Plates()
  check(not info.nameplate2 and not Tr.NeededDrops()["ours noir"], "quest complete: drop badge + drop line gone")
  -- quest removed from the log
  table.remove(M.quests, 2)
  ctx.fire("QUEST_REMOVED", 2010)
  M.Advance(1)
  check(D.InLog(2010) == nil and D.NpcObjective(3005) == nil, "removed quest: nothing keyed by its ID")
  for i = 1, 6 do M.units["nameplate" .. i] = nil end
  QLg.UnitIsRelatedToActiveQuest = oldRel
  if not hadQL then rawset(_G, "C_QuestLog", nil) end
  rawset(_G, "C_NamePlate", oldNP)
  rawset(_G, "QuestieLoader", oldQL)

  -- 4. corpse run ------------------------------------------------------------------------------------
  local function sugg(id) for _, e in ipairs(T:GetSuggestions() or {}) do if e.id == id then return e end end end
  C.char.custom = { mapID = 1424, x = 0.3, y = 0.3, name = "Mon camp" }
  M.pos = { mapID = 1424, x = 0.5, y = 0.5 }
  M.player.dead, M.player.ghost = true, false
  T:InvalidatePlayerState()
  ctx.fire("PLAYER_DEAD")
  check(S.dead and S.dead.map == 1424, "death spot noted at PLAYER_DEAD")
  rawset(_G, "C_DeathInfo", { GetCorpseMapPosition = function(map) if map == 1424 then return { x = 0.55, y = 0.45 } end end })
  M.player.ghost = true
  T:InvalidatePlayerState()
  ctx.fire("PLAYER_ALIVE")   -- released: ghost at the graveyard
  local c = sugg("Speed:corpse")
  check(c and c.text:find("^Cadavre : %d+ m au NE %(≈ %d+ s%)"), "corpse line: " .. tostring(c and c.text))
  local top = (T:GetSuggestions() or {})[1]
  check(top and top.id == "Speed:corpse", "corpse line on top of « Maintenant »: " .. tostring(top and top.id))
  check(C.char.custom and C.char.custom.source == "corpse" and math.abs(C.char.custom.x - 0.55) < 1e-6,
    "compass target = corpse (shared Compass:SetTarget)")
  C.Rebuild("force")
  local st = C.Refresh()
  check(st and st.corpse and not st.hiddenDead and not st.dup, "compass still shown while ghost: corpse beats route")
  check(not sugg("Radar:corpse"), "one owner: no second corpse line from Radar")
  -- at the corpse with two hostile plates within range: rez away from them
  M.pos = { mapID = 1424, x = 0.545, y = 0.455 }
  plates = {}
  rawset(_G, "C_NamePlate", { GetNamePlates = function() local t = {} for u, f in pairs(plates) do if M.units[u] then t[#t + 1] = f end end return t end,
    GetNamePlateForUnit = function(u) return plates[u] end })
  for i = 1, 2 do
    local u = "nameplate" .. i
    M.units[u] = { name = "Ours noir", guid = "Creature-0-0-0-0-3005-" .. i, hostile = true, level = 25 }
    plates[u] = CreateFrame("Frame", nil, UIParent)
    plates[u].namePlateUnitToken = u
  end
  -- (4.0, audit) un PNJ n'a pas de position lisible dans le vrai client : distance inconnue → ni « 2 ennemis tout
  -- près » ni « coin sûr » (rien d'inventé) ; la ligne du cadavre (distance réelle au corps) reste
  S:Tick()
  c = sugg("Speed:corpse")
  check(c and c.text:find("^Cadavre") and not c.text:find("ennemi") and not c.text:find("coin sûr"),
    "foes at unknown distance: no safe-spot claim: " .. tostring(c and c.text))
  for i = 1, 2 do M.units["nameplate" .. i] = nil end
  S:Tick()
  c = sugg("Speed:corpse")
  check(c and not c.text:find("coin sûr"), "no plate read: no « coin sûr » claim: " .. tostring(c and c.text))
  -- revive: line gone, compass back to the player's own point
  M.player.dead, M.player.ghost = false, false
  T:InvalidatePlayerState()
  ctx.fire("PLAYER_UNGHOST")
  check(not sugg("Speed:corpse"), "revive: corpse line cleared")
  check(C.char.custom and C.char.custom.name == "Mon camp" and C.char.custom.source == nil, "revive: player's own compass point restored")
  rawset(_G, "C_NamePlate", oldNP)
  rawset(_G, "C_DeathInfo", nil)
end
