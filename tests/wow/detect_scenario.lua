-- Scenario for Modules/Detect.lua (agent DETECT) + its adoption in Boost / Highlight / Compass / Predict / Radar.
-- Mocks: C_VignetteInfo (Forever 1.60.1 API), C_NamePlate, C_QuestLog.UnitIsRelatedToActiveQuest, C_TaxiMap,
-- C_AreaPoiInfo, merchant NPC. All three API flavours. Fake names only.
return function(M, ctx)
  local T = TreeAI
  local D = T and T.Detect
  ctx.check(D and D.db and D.db.enabled, "Detect registered + enabled")
  if not D then return end
  local C, B, H, R, P = T.Compass, T:GetModule("Boost"), T:GetModule("Highlight"), T:GetModule("Radar"), T:GetModule("Predict")
  M.LeaveCombat()
  M.pos = { mapID = 1424, x = 0.5, y = 0.4 }
  M.instance = false
  T:Suggest("x", nil) -- no-op, keeps the API warm

  local events = {}
  D:OnChange(function(kind, data) events[#events + 1] = { kind = kind, data = data } end)
  local function count(kind) local n = 0 for _, e in ipairs(events) do if e.kind == kind then n = n + 1 end end return n end
  local function last(kind) for i = #events, 1, -1 do if events[i].kind == kind then return events[i].data end end end
  local function sugg(id) for _, s in ipairs(T:GetSuggestions()) do if s.id == id then return s end end end

  -- 1. vignettes: rare + treasure + dead rare (ignored) ---------------------------------------------
  local VIG = {
    ["Vignette-1"] = { vignetteGUID = "Vignette-1", objectGUID = "Creature-0-0-0-0-5001-1", name = "Gueule-de-fer",
      isDead = false, atlasName = "VignetteKill", type = 0, vignetteID = 11, onMinimap = true, pos = { 0.55, 0.4 } },
    ["Vignette-2"] = { vignetteGUID = "Vignette-2", objectGUID = "GameObject-0-0-0-0-6001-1", name = "Coffre usé",
      isDead = false, atlasName = "VignetteLoot", type = 0, vignetteID = 12, onMinimap = true, pos = { 0.5, 0.3 } },
    ["Vignette-3"] = { vignetteGUID = "Vignette-3", objectGUID = "Creature-0-0-0-0-5002-1", name = "Mort-Vivant",
      isDead = true, atlasName = "VignetteKillElite", type = 0, vignetteID = 13, pos = { 0.6, 0.6 } },
  }
  rawset(_G, "C_VignetteInfo", {
    GetVignettes = function() local t = {} for k in pairs(VIG) do t[#t + 1] = k end table.sort(t) return t end,
    GetVignetteInfo = function(id) return VIG[id] end,
    GetVignettePosition = function(id, map)
      local v = VIG[id]
      if v and map == 1424 then return { x = v.pos[1], y = v.pos[2], GetXY = function(p) return p.x, p.y end } end
    end,
  })
  ctx.fire("VIGNETTES_UPDATED")
  M.Advance(1)
  local rares = D:Rares()
  ctx.check(#rares == 2, "2 vignettes kept (dead one skipped): " .. #rares)
  local rare = D:Rares("rare")[1]
  ctx.check(rare and rare.name == "Gueule-de-fer" and rare.kind == "rare" and rare.mapID == 1424, "rare classified")
  ctx.check(rare and rare.dist and math.abs(rare.dist - 150) < 1, "rare distance 150 yd: " .. tostring(rare and rare.dist))
  ctx.check(D.Describe(rare):find("Rare : Gueule%-de%-fer") and D.Describe(rare):find(" E$"), "describe: " .. tostring(D.Describe(rare)))
  local tr = D:Rares("treasure")[1]
  ctx.check(tr and tr.name == "Coffre usé", "treasure classified")
  ctx.check(count("rares") == 1 and #last("rares") == 2, "one 'rares' event with the 2 new points")
  ctx.fire("VIGNETTES_UPDATED")
  M.Advance(1)
  ctx.check(count("rares") == 1, "no repeat for already-seen vignettes")
  -- Boost: one line per kind, one click = compass target
  local s = sugg("Boost:rare")
  ctx.check(s and s.text:find("Gueule%-de%-fer") and s.onClick, "Boost rare line from vignette: " .. tostring(s and s.text))
  ctx.check(sugg("Boost:treasure"), "Boost treasure line")
  if s and s.onClick then
    s.onClick()
    ctx.check(C and C.char.custom and math.abs(C.char.custom.x - 0.55) < 1e-6 and C.char.custom.mapID == 1424,
      "rare click -> compass custom target")
  end
  -- Radar draws the exact vignette positions
  if R and R.DetectPins and R.Me then
    local me = R.Me()
    local pins = me and R.DetectPins(me) or {}
    local kinds = {}
    for _, p in ipairs(pins) do kinds[p.kind] = true end
    ctx.check(kinds.rare and kinds.treasure, "radar pins: rare + treasure (" .. #pins .. ")")
  end
  -- Compass one-click helper
  if C and C.TargetNearest then
    local t = C.TargetNearest("treasure")
    ctx.check(t and math.abs(t.y - 0.3) < 1e-6, "compass TargetNearest(treasure)")
  end
  -- nameplate fallback when no vignette exists: approximate, still announced once
  M.units.nameplate5 = { name = "Rôdeur rare", guid = "Creature-0-0-0-0-5005-1", hostile = true, level = 27,
    classification = "rareelite" }
  ctx.fire("NAME_PLATE_UNIT_ADDED", "nameplate5")
  local pr = nil
  for _, e in ipairs(D:Rares()) do if e.name == "Rôdeur rare" then pr = e end end
  ctx.check(pr and pr.approx and pr.kind == "rareelite", "plate rare recorded (approx)")
  ctx.check(count("rares") == 2, "plate rare announced once")
  ctx.fire("NAME_PLATE_UNIT_REMOVED", "nameplate5")
  M.units.nameplate5 = nil

  -- 2/3. quests + quest units on nameplates -------------------------------------------------------
  M.quests = {
    { header = "Contreforts" },
    { id = 2002, title = "Les ours", level = 25, objectives = { { text = "Ours enragé tué : 7/8", done = false, have = 7, need = 8 } } },
    { id = 2003, title = "Sang de lion", level = 26, objectives = { { text = "Sang de lion : 2/10", done = false, have = 2, need = 10 } } },
  }
  D.RefreshQuests()
  local ql = D:Quests()
  ctx.check(#ql == 2 and ql[1].objectives[1].name == "ours enragé" and ql[1].objectives[1].have == 7,
    "quest snapshot + objective parse: " .. tostring(ql[1] and ql[1].objectives[1] and ql[1].objectives[1].name))
  local plates = {}
  rawset(_G, "C_NamePlate", {
    GetNamePlateForUnit = function(u)
      if not M.units[u] then return nil end
      plates[u] = plates[u] or CreateFrame("Frame", nil, UIParent)
      plates[u].namePlateUnitToken = u
      return plates[u]
    end,
    GetNamePlates = function() local t = {} for u, f in pairs(plates) do if M.units[u] then t[#t + 1] = f end end return t end,
  })
  local QL = rawget(_G, "C_QuestLog") or {}
  rawset(_G, "C_QuestLog", QL)
  local oldRel = QL.UnitIsRelatedToActiveQuest
  -- the game's own signal wins: nameplate1 (bear) and nameplate2 (boar) are related; a same-named bear the
  -- game says is NOT related (nameplate4, once the API has proven it answers « true ») gets no badge (DATA)
  QL.UnitIsRelatedToActiveQuest = function(u)
    local bear = M.quests[2] and M.quests[2].id == 2002 and not M.quests[2].objectives[1].done
    return u == "nameplate2" or (u == "nameplate1" and bear)
  end
  M.units.nameplate1 = { name = "Ours enragé", guid = "Creature-0-0-0-0-3001-1", hostile = true, level = 25 }
  M.units.nameplate2 = { name = "Sanglier balafré", guid = "Creature-0-0-0-0-3010-1", hostile = true, level = 25 }
  M.units.nameplate3 = { name = "Lapin", guid = "Creature-0-0-0-0-721-2", hostile = false, level = 1 }
  for i = 1, 3 do ctx.fire("NAME_PLATE_UNIT_ADDED", "nameplate" .. i) end
  M.Advance(0.5)
  local qu = D:QuestUnits()
  ctx.check(#qu == 2, "2 quest units on plates: " .. #qu)
  ctx.check(qu.left == 1, "kills left on screen = 1: " .. tostring(qu.left))
  local q1 = D:QuestUnit("nameplate1")
  ctx.check(q1 and q1.quest == "Les ours" and q1.left == 1 and q1.hostile, "objective match by name")
  local q2 = D:QuestUnit("nameplate2")
  ctx.check(q2 and q2.related and not q2.quest, "UnitIsRelatedToActiveQuest unit (no name match)")
  ctx.check(D:QuestUnit("nameplate3") == nil, "unrelated critter ignored")
  M.units.nameplate4 = { name = "Ours enragé", guid = "Creature-0-0-0-0-3099-1", hostile = true, level = 25 }
  ctx.check(D:QuestUnit("nameplate4") == nil, "DATA: game says unrelated -> no name-match badge")
  M.units.nameplate4 = { name = "Ours", guid = "Creature-0-0-0-0-3098-1", hostile = true, level = 25 }
  ctx.check(D.ObjectiveFor("Ours") == nil and D.ObjectiveFor("Ours enragé") ~= nil, "DATA: exact names only (« Ours » ≠ « Ours enragé »)")
  M.units.nameplate4 = nil
  ctx.check(count("questunits") >= 1, "questunits event")
  if H and H.UpdatePlate then
    H.db.plates = true
    H.UpdatePlate("nameplate2")
    local _, info = H.Plates()
    ctx.check(info.nameplate2 and info.nameplate2.how == "kill", "Highlight plate via Detect.QuestUnit fallback")
  end
  -- Forever: in combat, names are secret -> nothing breaks, last info kept
  M.EnterCombat()
  ctx.fire("NAME_PLATE_UNIT_ADDED", "nameplate1")
  local c = D:Combat()
  ctx.check(c and c.inCombat, "combat snapshot in combat")
  M.LeaveCombat()

  -- XP chat kill counting (no combat log on Forever)
  ctx.fire("CHAT_MSG_COMBAT_XP_GAIN", "Ours enragé meurt, vous gagnez 120 points d'expérience.")
  ctx.check(D:Kills("Ours enragé") == 1, "kill counted from XP chat")
  local k = last("kill")
  ctx.check(k and k.objective and k.objective.quest == "Les ours", "kill linked to objective")
  -- progress (debounced): 7/8 -> 8/8
  M.quests[2].objectives[1] = { text = "Ours enragé tué : 8/8", done = true, have = 8, need = 8 }
  M.quests[2].complete = true
  local before = count("quests")
  ctx.fire("QUEST_LOG_UPDATE") ctx.fire("QUEST_WATCH_UPDATE") ctx.fire("UNIT_QUEST_LOG_CHANGED", "player")
  M.Advance(0.5)
  local p = last("progress")
  ctx.check(p and p.questID == 2002 and p.gained == 1 and p.have == 8, "progress diff +1")
  local diff = last("quests")
  ctx.check(diff and #diff.completed == 1, "quest completed diff")
  ctx.check(count("quests") == before + 1, "debounced: one diff for three events: " .. (count("quests") - before))
  ctx.check(D:QuestUnit("nameplate1") == nil, "finished objective: bear no longer a quest unit")
  -- turn in
  ctx.fire("QUEST_TURNED_IN", 2002, 500, 0)
  table.remove(M.quests, 2)
  ctx.fire("QUEST_REMOVED", 2002)
  M.Advance(0.5)
  diff = last("quests")
  ctx.check(diff and #diff.turnedIn == 1 and diff.turnedIn[1].id == 2002, "turned-in diff")
  QL.UnitIsRelatedToActiveQuest = oldRel

  -- 4. services ----------------------------------------------------------------------------------
  D.db.learned = {}   -- the harness event storm may already have taught a nameless vendor here
  M.units.npc = { name = "Marchand-Test", guid = "Creature-0-0-0-0-4001-1" }
  local oldRepair = rawget(_G, "CanMerchantRepair")
  rawset(_G, "CanMerchantRepair", function() return true end)
  M.pos = { mapID = 1424, x = 0.52, y = 0.4 }
  ctx.fire("MERCHANT_SHOW")
  rawset(_G, "CanMerchantRepair", oldRepair)
  M.units.npc = nil
  M.pos = { mapID = 1424, x = 0.5, y = 0.4 }
  local txt, e = D:NearestText("repair")
  ctx.check(txt and txt:find("Réparation : Marchand%-Test") and e.dist and math.abs(e.dist - 60) < 1, "learned repairer: " .. tostring(txt))
  ctx.check(D:Nearest("vendor") and D:Nearest("vendor").name == "Marchand-Test", "learned vendor")
  ctx.check(type(D.db.learned["1424"]) == "table" and #D.db.learned["1424"] == 2, "learned saved per map (account)")
  rawset(_G, "C_TaxiMap", { GetTaxiNodesForMap = function(map)
    if map == 1424 then return { { nodeID = 7, name = "Moulin-de-Tarren", position = { x = 0.6, y = 0.5 }, isUndiscovered = false } } end
    return {}
  end })
  rawset(_G, "C_AreaPoiInfo", {
    GetAreaPOIForMap = function(map) return map == 1424 and { 99 } or {} end,
    GetAreaPOIInfo = function(_, id) if id == 99 then return { areaPoiID = 99, name = "Ruines-Test", position = { x = 0.4, y = 0.4 } } end end,
  })
  ctx.check(D:Nearest("flight") and D:Nearest("flight").name == "Moulin-de-Tarren", "flight master from C_TaxiMap")
  ctx.check(#D:Services("poi") == 1, "area POI")
  ctx.check(#D:Services() >= 4, "all services: " .. #D:Services())
  -- Predict « courses »: nearest repairer appended
  if P and P.Update then
    local old = P.Forecasts
    P.Forecasts = function() return { { id = "repair", text = "Réparation urgente : un objet à 10 %", prio = 3 } } end
    local okU, list = pcall(P.Update)
    P.Forecasts = old
    ctx.check(okU and list and list[1].text:find("Réparation : Marchand%-Test"), "Predict appends nearest repairer: "
      .. tostring(okU and list and list[1] and list[1].text))
  end
  M.RunSlash("/tai detect reparation")
  ctx.check(C and C.char.custom and math.abs(C.char.custom.x - 0.52) < 1e-6, "/tai detect reparation -> compass")
  M.RunSlash("/tai detect")

  -- 5. group -------------------------------------------------------------------------------------
  M.group = { { name = "Ami-Un", class = "MAGE", level = 26, online = true } }
  D.CheckGroup()
  local g = D:Group()
  ctx.check(#g == 1 and g[1].online and not g[1].dead, "group member read")
  M.group[1].online = false
  local ch = D.CheckGroup()
  ctx.check(#ch == 1 and ch[1].what == "est hors ligne", "offline change: " .. tostring(ch[1] and ch[1].what))
  M.group[1].online, M.group[1].dead = true, true
  ch = D.CheckGroup()
  local whats = {}
  for _, x in ipairs(ch) do whats[x.what] = true end
  ctx.check(whats["est revenu"] and whats["est mort"], "back online + dead")
  M.group = {}

  -- 6. combat without combat log -----------------------------------------------------------------
  M.EnterCombat()
  ctx.fire("UNIT_COMBAT", "player", "WOUND", "", 120, 1)
  ctx.fire("UNIT_COMBAT", "player", "DODGE", "", 0, 1)
  ctx.fire("UNIT_COMBAT", "pet", "WOUND", "", 40, 1)
  c = D:Combat()
  ctx.check(c.hits5s == 1 and c.dmg5s == 120 and c.petAttacked, "UNIT_COMBAT hits: " .. tostring(c.hits5s) .. "/" .. tostring(c.dmg5s))
  M.LeaveCombat()
  ctx.fire("PLAYER_REGEN_ENABLED")

  -- summary / instance safety -------------------------------------------------------------------
  D.WriteSummary()
  local sm = T:Summary()
  ctx.check(sm.detect and sm.detect.rares >= 1 and sm.detect.treasures == 1 and sm.detect.learnedServices == 2, "summary.detect")
  M.instance = true
  ctx.fire("VIGNETTES_UPDATED")
  M.Advance(1)
  ctx.check(#D:Services() == 0, "no services / positions in instance")
  M.instance = false
  for i = 1, 3 do M.units["nameplate" .. i] = nil end
  rawset(_G, "C_VignetteInfo", nil)
  rawset(_G, "C_NamePlate", nil)
  rawset(_G, "C_TaxiMap", nil)
  rawset(_G, "C_AreaPoiInfo", nil)
  if not next(QL) then rawset(_G, "C_QuestLog", nil) end
end
