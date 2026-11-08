-- Scenario for Modules/Highlight.lua (quest item / mob highlighting), both API flavours. Fake names only.
-- Mock Blizzard bag buttons (Classic ContainerFrameNItemM + Mainline EnumerateValidItems), nameplates, target
-- frame, loot window, group loot roll, Questie DB, Bagnon / Baganator public hooks.
return function(M, ctx)
  local T = TreeAI
  local H = T and T.modules and T.modules.Highlight
  local Q = T and T:GetModule("Quests")
  ctx.check(H and H.db and H.db.enabled, "Highlight registered + enabled")
  ctx.check(Q, "Quests present")
  if not (H and Q) then return end
  M.LeaveCombat()

  -- items -----------------------------------------------------------------------------------------
  M.AddItem(9101, { name = "Sang de lion", type = "Quête", classID = 12, stack = 20 })
  M.AddItem(9102, { name = "Lettre tachée de sang", type = "Quête", classID = 12, questItem = true, startsQuest = 777 })
  M.AddItem(9103, { name = "Vieux trophée", type = "Quête", classID = 12 })
  M.AddItem(9104, { name = "Croc ancien", type = "Quête", classID = 12 })
  M.AddItem(9105, { name = "Galette", type = "Consommable", classID = 0 })
  M.AddItem(9106, { name = "Paquet scellé", type = "Quête", classID = 12 })
  M.bags = {}
  for b = 0, 4 do M.bags[b] = { size = 16, slots = {} } end
  M.SetBagItem(0, 1, 9101, 2)
  M.SetBagItem(0, 2, 9102, 1)
  M.SetBagItem(0, 3, 9103, 1)
  M.SetBagItem(0, 4, 9104, 1)
  M.SetBagItem(0, 5, 9105, 5)
  M.SetBagItem(0, 6, 9106, 1)
  M.SetBagItem(1, 1, 9101, 1)

  -- quest log: ids are 1000 + index (headers count) -------------------------------------------------
  M.quests = {
    { header = "Contreforts de Hautebrande" },
    { title = "Sang de lion", level = 26, objectives = { { text = "Sang de lion : 2/10", done = false, have = 2, need = 10, type = "item" } } },
    { title = "Les ours", level = 25, objectives = { { text = "Ours enragé tué : 7/8", done = false, have = 7, need = 8 } } },
    { title = "Le courrier", level = 24, complete = true,
      objectives = { { text = "Paquet scellé : 1/1", done = true, have = 1, need = 1 } } },
  }
  -- Questie (read-only DB): item 9101 dropped by npc 3002; chain 1002 -> 2001 needs item 9104
  local QDB = {
    QueryQuestSingle = function(id, key)
      if key == "objectives" and id == 1002 then return { nil, nil, { { 9101 } } } end
      if key == "objectives" and id == 2001 then return { nil, nil, { { 9104 } } } end
      if key == "nextQuestInChain" and id == 1002 then return 2001 end
      if key == "name" and id == 2001 then return "Les crocs du vieux lion" end
      if key == "name" and id == 777 then return "Une lettre inquiétante" end
      return nil
    end,
    QueryItemSingle = function(id, key)
      if key == "name" then local it = M.items[id] return it and it.name end
      if key == "npcDrops" and id == 9101 then return { 3002 } end
    end,
    QueryNPCSingle = function(id, key) if key == "name" and id == 3002 then return "Lion des collines" end end,
  }
  rawset(_G, "QuestieLoader", { _modules = { QuestieDB = QDB } })
  H.db.tooltipWithQuestie = true

  -- mock Blizzard bag frames ------------------------------------------------------------------------
  local cf = CreateFrame("Frame", "ContainerFrame1", UIParent)
  cf:SetID(0)
  local btns = {}
  for j = 1, 6 do
    local b = CreateFrame("Button", "ContainerFrame1Item" .. j, cf)
    b:SetID(j)
    btns[j] = b
  end
  local cf2 = CreateFrame("Frame", "ContainerFrame2", UIParent)  -- Mainline style
  cf2:SetID(1)
  local mb = CreateFrame("ItemButton", nil, cf2)
  mb:SetID(1)
  function mb:GetBagID() return 1 end
  function cf2:EnumerateValidItems() return ipairs({ mb }) end

  ctx.fire("QUEST_LOG_UPDATE")
  M.Advance(2)
  Q.Update()
  H.Rebuild()
  H.RefreshBags()
  local ov = H.Overlays()
  local function kind(b) return ov[b] and ov[b]:IsShown() and ov[b].kind end
  ctx.check(kind(btns[1]) == "need", "bag: objective item highlighted: " .. tostring(kind(btns[1])))
  ctx.check(ov[btns[1]] and ov[btns[1]].text:GetText() == "2/10", "bag: x/y count " .. tostring(ov[btns[1]] and ov[btns[1]].text:GetText()))
  ctx.check(kind(btns[2]) == "starter", "bag: starter « ! »: " .. tostring(kind(btns[2])))
  ctx.check(kind(btns[3]) == "useless", "bag: finished-quest item dimmed: " .. tostring(kind(btns[3])))
  ctx.check(ov[btns[3]] and ov[btns[3]].shade:IsShown(), "useless: shade shown")
  ctx.check(kind(btns[4]) == "upcoming", "bag: upcoming quest item (Questie chain): " .. tostring(kind(btns[4])))
  ctx.check(not kind(btns[5]), "bag: normal item untouched")
  ctx.check(kind(btns[6]) == "done", "bag: objective done → à rendre: " .. tostring(kind(btns[6])))
  ctx.check(kind(mb) == "need", "bag: Mainline EnumerateValidItems button")
  ctx.check(ov[btns[1]].__parent == UIParent and ov[btns[1]].owner == btns[1], "overlay is ours (UIParent), anchored to the button")
  ctx.check(ov[btns[1]].__points[1] and ov[btns[1]].__points[1][2] == btns[1], "overlay SetPoint on the Blizzard button")
  local s = H.Data()
  ctx.check(s.byID[9101] and s.mobNpc[3002] and s.mobNpc[3002].item == 9101, "Questie: drop source mapped")

  -- +1 popup when the count goes up
  M.quests[2].objectives[1] = { text = "Sang de lion : 3/10", done = false, have = 3, need = 10, type = "item" }
  Q.Update()
  H.Rebuild()
  H.RefreshBags()
  ctx.check(ov[btns[1]].text:GetText() == "3/10" and ov[btns[1]].pop:IsShown(), "« +1 » on progress")

  -- performance: ≤ 1 ms per refresh (host CPU, mock included)
  local c0 = os.clock()
  for _ = 1, 50 do H.RefreshBags() end
  local per = (os.clock() - c0) * 1000 / 50
  ctx.check(per < 1, ("bag refresh %.3f ms"):format(per))
  c0 = os.clock()
  for _ = 1, 20 do H.Rebuild() end
  per = (os.clock() - c0) * 1000 / 20
  ctx.check(per < 1, ("rebuild %.3f ms"):format(per))

  -- options: hide a kind
  H.db.bagUseless = false
  H.RefreshBags()
  ctx.check(not kind(btns[3]), "option bagUseless off hides the dimming")
  H.db.bagUseless = true

  -- tab summary
  local lines = H.PushTab()
  ctx.check(lines[1] and lines[1].text:find("Lettre tachée") and lines[1].text:find("Une lettre inquiétante"), "tab: starter line: " .. tostring(lines[1] and lines[1].text))

  -- tooltips (out of combat only)
  GameTooltip.__lines = {}
  GameTooltip.__item = 9101
  local tt = H.OnItemTooltip(GameTooltip)
  ctx.check(tt == "Quête : Sang de lion — encore 7 (3/10)", "item tooltip: " .. tostring(tt))
  ctx.check(GameTooltip.__lines[#GameTooltip.__lines] == tt, "tooltip line added")
  M.EnterCombat()
  ctx.check(H.OnItemTooltip(GameTooltip) == nil, "no tooltip line in combat")
  M.LeaveCombat()

  -- nameplates (own frames anchored on C_NamePlate frames) -------------------------------------------
  local plateFrames = {}
  rawset(_G, "C_NamePlate", { GetNamePlateForUnit = function(u)
    if not M.units[u] then return nil end
    plateFrames[u] = plateFrames[u] or CreateFrame("Frame", nil, UIParent)
    return plateFrames[u]
  end })
  M.units.nameplate1 = { name = "Ours enragé", guid = "Creature-0-0-0-0-3001-1", hostile = true, level = 25 }
  M.units.nameplate2 = { name = "Lion des collines", guid = "Creature-0-0-0-0-3002-7", hostile = true, level = 26 }
  M.units.nameplate3 = { name = "Lapin", guid = "Creature-0-0-0-0-721-2", hostile = false, level = 1 }
  -- our compass frame (Compass module owns it; created here if the module has not built it yet)
  local CF = rawget(_G, "TreeAICompassFrame") or CreateFrame("Button", "TreeAICompassFrame", UIParent)
  CF:Show()
  for i = 1, 3 do ctx.fire("NAME_PLATE_UNIT_ADDED", "nameplate" .. i) end
  local pl, info = H.Plates()
  ctx.check(pl.nameplate1 and pl.nameplate1:IsShown() and pl.nameplate1.text:GetText() == "1", "plate: kill objective, 1 left: "
    .. tostring(pl.nameplate1 and pl.nameplate1.text:GetText()))
  ctx.check(pl.nameplate1.__points[1][2] == plateFrames.nameplate1 and pl.nameplate1.__parent == UIParent, "plate icon anchored, not parented")
  ctx.check(info.nameplate2 and info.nameplate2.how == "drop", "plate: drop source (Questie npcDrops)")
  ctx.check(not (pl.nameplate3 and pl.nameplate3:IsShown()), "plate: unrelated mob untouched")
  ctx.check(H.nearCount == 2, "compass: 2 objectives near: " .. tostring(H.nearCount))
  ctx.fire("NAME_PLATE_UNIT_REMOVED", "nameplate1")
  ctx.check(not pl.nameplate1:IsShown() and H.nearCount == 1, "plate removed")

  -- target badge
  CreateFrame("Frame", "TargetFrame", UIParent)
  M.target = { exists = true, name = "Ours enragé", guid = "Creature-0-0-0-0-3001-9", hostile = true, level = 25 }
  ctx.fire("PLAYER_TARGET_CHANGED")
  ctx.check(H.targetText and H.targetText:find("Les ours — encore 1"), "target badge: " .. tostring(H.targetText))
  -- unit tooltip
  GameTooltip.GetUnit = function() return "Ours enragé", "target" end
  local ut = H.OnUnitTooltip(GameTooltip)
  ctx.check(ut and ut:find("Quête : Les ours — encore 1"), "unit tooltip: " .. tostring(ut))
  GameTooltip.GetUnit = function() return nil end

  -- loot window ------------------------------------------------------------------------------------
  local LF = CreateFrame("Frame", "LootFrame", UIParent)
  LF:Show()
  local lb = CreateFrame("Button", "LootButton1", LF)
  lb.slot = 1
  M.loot = { { name = "Sang de lion", questItem = true }, { name = "Lettre tachée de sang", questItem = true, questID = 777 } }
  ctx.fire("LOOT_OPENED")
  ctx.check(H.lootLines and H.lootLines[1] and H.lootLines[1]:find("%(3/10 → 4/10%)"), "loot: « pour la quête (3/10 → 4/10) »: "
    .. tostring(H.lootLines and H.lootLines[1]))
  ctx.check(kind(lb) == "need", "loot button glows")
  ctx.check(H.lootLines[2] and H.lootLines[2]:find("Lance la quête"), "loot: starter line: " .. tostring(H.lootLines[2]))
  ctx.fire("LOOT_CLOSED")
  M.loot = {}

  -- group loot roll
  local rf = CreateFrame("Frame", "GroupLootFrame1", UIParent)
  rf.rollID = 5
  rawset(_G, "GetLootRollItemLink", function(id) if id == 5 then return M.ItemLink(9101) end end)
  ctx.fire("START_LOOT_ROLL", 5, 60)
  ctx.check(H.lastRoll and H.lastRoll.kind == "need" and H.lastRoll.text:find("Sang de lion") and kind(rf) == "need",
    "roll frame: quest item glow + label")

  -- third-party bags (public hooks, read-only)
  local corner
  rawset(_G, "Baganator", { API = { RegisterCornerWidget = function(_, _, onUpdate, onInit) corner = { up = onUpdate, init = onInit } end } })
  local Item = { Update = function() end }
  rawset(_G, "Bagnon", { Item = Item })
  H.HookThirdParty()
  local w = corner and corner.init(CreateFrame("Button", nil, UIParent))
  ctx.check(w and corner.up(w, { itemID = 9101 }) == true and w.txt:GetText() == "3/10", "Baganator corner widget")
  ctx.check(corner and corner.up(w, { itemID = 9105 }) == false, "Baganator: normal item no widget")
  local bb = CreateFrame("Button", nil, UIParent)
  bb:SetID(2)
  function bb:GetBag() return 0 end
  Item.Update(bb)
  ctx.check(kind(bb) == "starter", "Bagnon hook: starter on its button")

  -- visibility driver: closing the bag hides our overlays
  for j = 1, 6 do btns[j]:Hide() end
  M.Advance(0.6, 0.1)
  ctx.check(not ov[btns[1]]:IsShown(), "overlay hidden when the bag button hides")

  -- disable module cleans up
  ctx.check(H.Cost().bags >= 0, "cost exposed")
  rawset(_G, "QuestieLoader", nil) -- (restored)
  rawset(_G, "Baganator", nil)
  rawset(_G, "Bagnon", nil)
  rawset(_G, "C_NamePlate", nil)
  rawset(_G, "GetLootRollItemLink", nil)
end
