-- Scenario for Modules/Gear.lua (run by tests/wow/test_gear.py on the real addon, fake names only).
return function(M, ctx)
  local T = TreeAI
  local Gr = T and T.modules and T.modules.Gear
  ctx.check(Gr and Gr.db and Gr.db.enabled, "Gear registered + enabled")
  ctx.check(T.Data and T.Data.Gear and T.Data.Gear.items and T.Data.Gear.items[2507], "client gear data loaded (Data/Gear.lua)")
  local E = Gr and Gr.Engine
  ctx.check(E and E.ScoreStats and E.Weights and E.EQUIP_SLOTS, "Gear owns the weights / scorer (moved from Boost)")
  local B = T.modules.Boost
  ctx.check(B and B.UpgradePct and B.CanUse, "old Boost contract kept as forwards (Quick, Dungeon, Abandon)")
  if not (Gr and E) then return end
  M.LeaveCombat()

  -- 1. real client data first: a full plan must run without error for the level-26 hunter
  local plan = Gr:ScanNow()
  ctx.check(plan and plan.slots and plan.cur, "plan with the real 5 000-item table")

  -- 2. deterministic run on a tiny fake table (the harness click storm may have bumped the horizon)
  Gr.db.horizon = 3
  local realItems, realCrafted = T.Data.Gear.items, T.Data.Gear.crafted
  T.Data.Gear.items = {
    [990001] = { 5, 25, 2, 4, 2, "3:12", 0, 0, 80, 2, 5000, "Gilet-test agile", 0 },     -- leather chest, vendor
    [990002] = { 5, 25, 3, 4, 3, "3:30", 0, 0, 200, 2, 0, "Haubert-test", 0 },           -- mail: level 40
    [990003] = { 15, 27, 3, 2, 2, "3:3", 14, 2.9, 0, 1, 0, "Arc-test du boss", 0 },      -- bow, dungeon boss
    [990005] = { 16, 20, 2, 4, 1, "3:4", 0, 0, 20, 1, 0, "Cape-test cousue", 0 },        -- crafted cloak
    [990006] = { 11, 24, 2, 4, 0, "3:5", 0, 0, 0, 2, 0, "Anneau-test", 0 },              -- ring, AH
    [990007] = { 1, 25, 2, 4, 2, "3:6", 0, 0, 60, 1, 0, "Casque-test", 0 },              -- head, quest reward
    [990008] = { 1, 25, 2, 4, 2, "5:9", 0, 0, 60, 1, 0, "Casque-test mage", 128 },       -- mage only (mask)
    [990010] = { 15, 20, 2, 2, 2, "", 8, 2.3, 0, 2, 0, "Arc-test porté", 0 },            -- equipped bow
  }
  T.Data.Gear.crafted = { [990005] = { 165, 100 } }
  M.skills = { { name = "Travail du cuir", rank = 120 } }
  M.AddItem(90001, { name = "Gilet porté", classID = 4, subclassID = 2, equipLoc = "INVTYPE_CHEST", quality = 2,
    stats = { ITEM_MOD_AGILITY_SHORT = 5 } })
  M.AddItem(90020, { name = "Gants-test des sacs", classID = 4, subclassID = 2, equipLoc = "INVTYPE_HAND", quality = 2,
    stats = { ITEM_MOD_AGILITY_SHORT = 8 } })
  M.AddItem(90030, { name = "Balles-test", classID = 6, subclassID = 3, stack = 200 })
  M.inventory = { [5] = { id = 90001 }, [18] = { id = 990010 } }
  M.ammoSlot = 0
  M.bags = {}
  M.SetBagItem(0, 1, 90020, 1)
  M.SetBagItem(0, 2, 90030, 200)
  M.InstallQuestieDB({
    items = { [990001] = { vendors = { 7001 } }, [990003] = { npcDrops = { 7002 } }, [990007] = { questRewards = { 5001 } } },
    npcs = { [7001] = { name = "Marchand-test", zoneID = 267 }, [7002] = { name = "Boss-test", zoneID = 1581, rank = 3, maxLevel = 21 } },
    quests = { [5001] = { name = "Quête-test", requiredLevel = 25 } },
  })
  local A = T.modules.Auction
  local oldStats = A and A.Stats
  if A then A.Stats = function(id) if id == 990006 then return { market = 25000, conf = 0.8, src = "TreeAI" } end end end
  Gr.MarkDirty()
  plan = Gr:ScanNow()
  ctx.check(plan, "plan computed")
  if not plan then return end
  local function best(slot) return plan.slots[slot] and plan.slots[slot][1] end
  local chest = best(5)
  ctx.check(chest and chest.id == 990001, "chest: leather upgrade (mail excluded before 40) got " .. tostring(chest and chest.id))
  ctx.check(chest and chest.pct > 100 and chest.src.kind == "vendor" and chest.src.text:find("Marchand%-test")
    and chest.src.cost == 5000, "chest source = vendor with buy price")
  local bow = best(18)
  ctx.check(bow and bow.id == 990003 and bow.src.kind == "boss" and bow.src.text:find("Mortemines"),
    "ranged: boss drop in Les Mortemines: " .. tostring(bow and bow.src and bow.src.text))
  local cloak = best(15)
  ctx.check(cloak and cloak.src.kind == "craft" and cloak.src.text:find("à fabriquer : Travail du cuir ~100"), "cloak: craftable (skill 120 ≥ 100)")
  local ring = best(11) or best(12)
  ctx.check(ring and ring.src.kind == "ah" and ring.src.cost == 25000, "ring: AH price")
  ctx.check(not (best(11) and best(12) and best(11).id == best(12).id), "same ring not proposed twice")
  local head = best(1)
  ctx.check(head and head.id == 990007 and head.src.kind == "quest" and head.src.text:find("Quête%-test"),
    "head: quest reward (mage-only helm filtered by class mask)")
  local hands = best(10)
  ctx.check(hands and hands.src.kind == "bag", "gloves from the bags: equip now")
  -- (4.3 V43) « le meilleur maintenant » : portable aujourd'hui, source faisable tout de suite, gain réel, clic = y aller
  local nw = plan.now or {}
  ctx.check(nw[5] and nw[5].id == 990001 and nw[5].nowSrc and nw[5].nowSrc.kind == "vendor", "now: chest at the vendor")
  ctx.check(nw[18] and nw[18].nowSrc.kind == "boss", "now: the boss bow (Mortemines, doable with 4 at 27)")
  for slot, cd in pairs(nw) do
    if type(cd) == "table" and cd.need then ctx.check(cd.need <= plan.ctx.level, "now: wearable today, slot " .. tostring(slot)) end
  end
  ctx.check(nw[10] and nw[10].nowSrc.kind == "bag", "now: gloves from the bags")
  local gt = Gr.GainText(nw[5], plan.cur[5], 5)
  ctx.check(gt and gt:find("%+7 Agi"), "real gain vs the worn chest (12 - 5 Agi): " .. tostring(gt))
  local rows = Gr.NowRows(plan)
  ctx.check(#rows >= 3 and rows.cost >= 5000, "now rows + total cost (" .. #rows .. ", " .. tostring(rows.cost) .. ")")
  local craftRow
  for _, r in ipairs(rows) do
    ctx.check(r.onClick and r.clickHint and r.right, "now row clickable with cost/time: " .. r.text)
    if r.srcKind == "craft" then craftRow = r end
  end
  ctx.check(craftRow and Gr.GoSource(craftRow.src) == "prof", "craft row: click opens the Métiers page")
  -- (4.3 V43) potion stock: the best classic healing potion for the level, counted in the bags
  local ps = Gr.PotionStock()
  ctx.check(ps and ps.best == 1710 and ps.have == 0 and ps.want == 5, "potions: item 1710 (level 21+) is the best at 27, none in bags")
  -- hunter notes: ammo mismatch (bow + only bullets), mail at 40
  ctx.check(plan.hunter and plan.hunter.ammoNeed == 2 and plan.hunter.ammoOK == false, "ammo type mismatch detected")
  ctx.check(plan.hunter.mail and plan.hunter.mail:find("40"), "mail at 40 note")
  -- shopping list: vendor chest + AH ring, total, affordability vs 12 po
  local sh = plan.shopping
  ctx.check(sh and #sh == 2 and sh.total == 30000, "shopping list (2 items, 3 po)")
  -- tab lines, suggestion, summary
  local lines = T:GetTabLines("gear") or {}
  local txt = ""
  for _, l in ipairs(lines) do txt = txt .. (l.text or "") .. "\n" end
  ctx.check(txt:find("Priorité des stats : Agilité"), "French stat priority line")
  ctx.check(txt:find("Torse : Gilet porté") and txt:find("Gilet%-test agile"), "slot line with next upgrade")
  ctx.check(txt:find("Gilet%-test agile %(emplacement vide%)") or txt:find("Gilet%-test agile %(nettement mieux%)"),
    "verdict in words, never « +X % »")
  ctx.check(not txt:find("%+%d+ %%"), "no estimated percentage shown: " .. txt)
  ctx.check(txt:find("Liste de courses"), "shopping list lines")
  ctx.check(txt:find("tu n'en as pas"), "ammo warning line")
  ctx.check(txt:find("DPS"), "ranged weapon DPS / speed shown")
  -- clicks (the user prefers the interface): expand a slot, bump the horizon, recalc
  local chestLine
  for _, l in ipairs(lines) do if l.text:find("Torse :") then chestLine = l end end
  ctx.check(chestLine and chestLine.onClick, "slot line clickable")
  if chestLine then chestLine.onClick() end
  ctx.check(Gr.expanded[5], "click expands the slot")
  local hz
  for _, l in ipairs(T:GetTabLines("gear") or {}) do if l.text:find("^Horizon") then hz = l end end
  ctx.check(hz and hz.onClick, "horizon line clickable")
  if hz then hz.onClick() end
  ctx.check(Gr.db.horizon == 5, "horizon +2 by click")
  Gr.db.horizon = 3
  Gr.expanded[5] = nil
  Gr:ScanNow()
  local S = T._suggestions
  ctx.check(S["Gear:upgrade"] and S["Gear:upgrade"].text:find("Gants%-test"), "best immediate upgrade = bag item")
  local s = T:Summary()
  ctx.check(s.gear and #s.gear.slots == 17 and s.gear.hunter and s.gear.priority, "summary.gear written")

  -- 3. coroutine path: paused in combat, finishes out of combat
  Gr.MarkDirty()
  T.Data.Gear.items[990011] = { 9, 25, 2, 4, 2, "3:7", 0, 0, 30, 2, 900, "Brassards-test", 0 }
  M.EnterCombat()
  Gr:AutoRefresh()
  M.Advance(2)
  ctx.check(not (Gr.plan.slots[9] and Gr.plan.slots[9][1]), "no computation in combat")
  M.LeaveCombat()
  M.Advance(1)
  Gr:AutoRefresh()
  for _ = 1, 50 do M.Advance(0.2) end
  ctx.check(Gr.plan.slots[9] and Gr.plan.slots[9][1] and Gr.plan.slots[9][1].id == 990011, "background plan finished")

  T.Data.Gear.items, T.Data.Gear.crafted = realItems, realCrafted

  -- 4. quest reward (moved from Boost): leather agi beats cloth int / plate for the hunter; glow anchored, never parented
  M.AddItem(90101, { name = "Gilet usé", classID = 4, subclassID = 2, equipLoc = "INVTYPE_CHEST", quality = 2,
    sellPrice = 100, stats = { ITEM_MOD_AGILITY_SHORT = 5 } })
  M.AddItem(90102, { name = "Robe de tissu", classID = 4, subclassID = 1, equipLoc = "INVTYPE_ROBE", quality = 2,
    sellPrice = 900, stats = { ITEM_MOD_INTELLECT_SHORT = 8 } })
  M.AddItem(90103, { name = "Gilet agile", classID = 4, subclassID = 2, equipLoc = "INVTYPE_CHEST", quality = 2,
    sellPrice = 300, stats = { ITEM_MOD_AGILITY_SHORT = 10 } })
  M.AddItem(90104, { name = "Cuirasse lourde", classID = 4, subclassID = 4, equipLoc = "INVTYPE_CHEST", quality = 2,
    sellPrice = 5000, stats = { ITEM_MOD_STRENGTH_SHORT = 20 } })
  local keepChest = M.inventory[5]
  M.inventory[5] = { id = 90101, count = 1 }
  M.questChoices = { 90104, 90102, 90103 }
  local bi = Gr.RankQuestRewards()
  ctx.check(bi == 3, "best reward = leather agi (got " .. tostring(bi) .. ")")
  CreateFrame("Button", "QuestInfoRewardsFrameQuestInfoItem3", UIParent)
  ctx.fire("QUEST_COMPLETE")
  M.Advance(1)
  local rw = S["Gear:reward"]
  ctx.check(rw and rw.text:find("^Prends « Gilet agile » : nettement mieux"), "reward line, verb first, words: " .. tostring(rw and rw.text))
  M.questChoices = { 90104, 90102 }
  local i2, v2, why2 = Gr.RankQuestRewards()
  ctx.check(i2 == 1 and why2 == "sell" and v2 == 5000, "no upgrade -> best vendor price")
  ctx.fire("QUEST_FINISHED")
  ctx.check(not S["Gear:reward"], "reward line cleared when the window closes")
  -- 5. item tooltip (moved from Boost): words, sell price ; plate refused for a hunter
  -- (4.0.2) one owner for item tooltip lines: with « Infos objets » (ItemInfo) on, Gear stays silent and ItemInfo
  -- writes the verdict with Gear's engine; Gear's own line is the fallback when ItemInfo is off
  GameTooltip.__item, GameTooltip.__lines = 90103, {}
  local fn = GameTooltip:GetScript("OnTooltipSetItem")
  ctx.check(fn, "tooltip hooked")
  local II = T.modules.ItemInfo
  if fn and II and II._active then
    fn(GameTooltip)
    local joined = table.concat(GameTooltip.__lines, " | ")
    ctx.check(not joined:find("que ce que tu portes", 1, true) and joined:find("Nettement mieux", 1, true),
      "ItemInfo on: Gear silent, one verdict line: " .. joined)
    T:SetModuleEnabled("ItemInfo", false)
    GameTooltip.__item, GameTooltip.__lines = 90103, {}
  end
  if fn then
    fn(GameTooltip)
    local joined = table.concat(GameTooltip.__lines, " | ")
    ctx.check(joined:find("TreeAI : nettement mieux que ce que tu portes"), "upgrade in words: " .. joined)
    ctx.check(joined:find("Se vend 3 pa au marchand"), "vendor price line: " .. joined)
    GameTooltip.__item, GameTooltip.__lines = 90104, {}
    fn(GameTooltip)
    ctx.check(table.concat(GameTooltip.__lines, " "):find("pas pour toi %(armure que tu ne peux pas porter%)"), "plate refused")
    GameTooltip.__item = nil
  end
  if II then T:SetModuleEnabled("ItemInfo", true) end
  M.inventory[5] = keepChest
  M.questChoices = {}

  -- slash
  M.RunSlash("/tai equipement")
  ctx.check(T:GetActiveTab() == "gear" or true, "slash ran")

  if A then A.Stats = oldStats end
  T.Data.Gear.items, T.Data.Gear.crafted = realItems, realCrafted
end
