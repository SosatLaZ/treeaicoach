-- Harness scenario (5.0, agent BAGS), run by tests/wow/test_bags.py on forever + modern (HUNTER, level 30). Proves, with
-- what the player sees dumped as « BAGS … » lines:
--   1. THE INCIDENT (evidence first): « la vente rapide a vendu mes meilleures potions de soins ». Cause shown: the
--      healing potion is a component of a Tailoring recipe → Courses en ville said « Pas besoin : sert en Couture (pas
--      ton métier) », extra = all of them → sold. Now: T.Bags.Verdict keeps the best potion he owns whatever anyone
--      says; a lower rank is « en trop » only when the better ones already cover the stock (5);
--   2. every keeper: quest items, « garder » marks, food / water / bandages, ammo of his ranged weapon, pet food (diet),
--      class reagents, keys, tools (one is enough), bags, mount, gear worn in alternation, unusable gear, recipes;
--      unknown item → keep; registered keepers (claim, explicit « en trop », error = keep);
--   3. AH vs vendor by expected value (Auctionator price), unknown AH price never sold by the one-click;
--   4. merchant card: « Vendre N objets · X », list, right-click = garder, ONE click → batches re-checked, never in
--      combat, at most 12 non-grey per click, undo hint, repair in the same click; greys through Verdict (QoL);
--   5. bank: « Ranger » deposits what the verdict says, withdraws the missing stock one stack per click;
--   6. full-bag warning before it happens, cheapest item highlighted, nothing destroyed;
--   7. overlays on the game's bag buttons (only the changed slot is touched), legend, tooltip line, « Ranger » = the
--      game's sort only inside a click; « Il te manque » + T.Bags.Shopping(); page « Sacs »;
--   8. looting with bags closed computes nothing; nothing in the chat, zero blocked action, zero violation.
-- Fake names only.
return function(M, ctx)
  local T = TreeAI
  local check = ctx.check
  local B = T.Bags
  local function out(s) io.write("BAGS ", tostring(s), "\n") end
  local chat0, v0 = #M.output, #M.violations
  local function blocked() return #(M.blocked or {}) end
  local b0 = blocked()
  M.LeaveCombat()
  if not check(B and B.Verdict and B.RegisterKeeper and B._active, "Bags module loaded and active") then return end
  -- (5.0 INTEG) sections 1-8 = Bags ALONE with its coarse bridges (what the player gets when « Équipement » or
  -- « Métiers » is switched off): the real keepers of GEAR and PROF step aside; section 9 puts them back.
  local Gm, PLm = T.Gear, T.Prof and T.Prof.Level
  check(#B.Keepers() >= 2, "the real keepers « Gear » and « Prof » are registered at login: " .. table.concat(B.Keepers(), ", "))
  B.UnregisterKeeper("Gear")
  B.UnregisterKeeper("Prof")
  check(#B.Keepers() == 0, "real keepers set aside for the bridge sections")
  local Q = T.modules.QoL
  if Q and Q.db then Q.db.sellRepair = false end          -- the card first; the automatic grey sale has its own step
  local K = T.modules.Quick
  if K and K.db then K.db.autoSell, K.db.autoRepair = false, false end

  -- ------------------------------------------------------------------ fixtures
  local function item(id, name, t) t.name = name M.AddItem(id, t) end
  item(858, "Potion de soins inférieure", { stack = 5, sellPrice = 25, classID = 0, subclassID = 1, minLevel = 3 })
  item(3928, "Potion de soins supérieure", { stack = 5, sellPrice = 250, classID = 0, subclassID = 1, minLevel = 35 })
  item(13446, "Potion de soins majeure", { stack = 5, sellPrice = 500, classID = 0, subclassID = 1, minLevel = 45 })
  item(3385, "Potion de mana inférieure", { stack = 5, sellPrice = 30, classID = 0, subclassID = 1, minLevel = 14 })
  item(4541, "Pain de seigle frais", { stack = 20, sellPrice = 6, classID = 0, subclassID = 5, minLevel = 5 })
  item(2504, "Arc court usé", { stack = 1, sellPrice = 50, classID = 2, subclassID = 2, equipLoc = "INVTYPE_RANGED" })
  item(9001, "Dent de test", { quality = 0, stack = 20, sellPrice = 12 })
  item(9002, "Plume sans valeur", { quality = 0, stack = 20, sellPrice = 0 })
  item(9003, "Lettre scellée de test", { stack = 1, sellPrice = 0, classID = 12 })
  item(9004, "Clé de test", { stack = 1, sellPrice = 0, classID = 13 })
  item(2901, "Pioche de mineur", { stack = 1, sellPrice = 20, classID = 15 })
  item(9005, "Sac en lin de test", { stack = 1, sellPrice = 100, classID = 1 })
  item(9006, "Rênes de test", { stack = 1, sellPrice = 0, classID = 15, subclassID = 5 })
  item(17031, "Rune de téléportation", { stack = 10, sellPrice = 250, classID = 5 })
  item(9007, "Plastron en plaques de test", { quality = 1, stack = 1, sellPrice = 300, classID = 4, subclassID = 4, equipLoc = "INVTYPE_CHEST" })
  item(9008, "Gilet de cuir de test", { quality = 2, stack = 1, sellPrice = 400, classID = 4, subclassID = 2, equipLoc = "INVTYPE_CHEST", minLevel = 25 })
  item(9009, "Soie de test", { stack = 20, sellPrice = 150, classID = 7 })
  item(9010, "Laine de test", { stack = 20, sellPrice = 33, classID = 7 })
  item(9011, "Lingot de test", { stack = 20, sellPrice = 40, classID = 7 })
  item(9012, "Bijou vert de test", { quality = 2, stack = 1, sellPrice = 800, classID = 15 })
  item(9013, "Babiole de test", { stack = 1, sellPrice = 10, classID = 15 })
  M.inventory[18] = { id = 2504, count = 1 }
  -- (5.1) the player has Questie: a small QuestieDB with the REAL schema (objectives[3] = items, requiredSourceItems,
  -- Item.relatedQuests / startQuest, requiredLevel / questLevel / requiredClasses / requiredSkill / specialFlags). The
  -- item → quests link is verified by Bags on real quests before it is trusted (≥ 3 objectives found back).
  item(9031, "Peau tannée de test", { stack = 20, sellPrice = 40, classID = 7 })
  item(9032, "Écaille de test", { stack = 20, sellPrice = 40, classID = 7 })
  item(9033, "Croc de test", { stack = 20, sellPrice = 40, classID = 7 })
  item(9034, "Herbe de mage de test", { stack = 20, sellPrice = 40, classID = 7 })
  item(9035, "Lettre trouvée de test", { stack = 1, sellPrice = 15, classID = 15 })
  item(9036, "Patte de test", { stack = 20, sellPrice = 40, classID = 7 })
  item(9037, "Fil de tailleur de test", { stack = 20, sellPrice = 40, classID = 7 })
  item(9038, "Étoffe de don de test", { stack = 20, sellPrice = 40, classID = 7 })
  item(9039, "Os de test", { stack = 20, sellPrice = 40, classID = 7 })
  local function quest(name, ql, rl, items, extra)
    local q = { name = name, questLevel = ql, requiredLevel = rl, objectives = { nil, nil, {} }, requiredRaces = 0, requiredClasses = 0 }
    for i, id in ipairs(items or {}) do q.objectives[3][i] = { id, nil } end
    for k, v in pairs(extra or {}) do q[k] = v end
    return q
  end
  local QDB = { quests = {
      [7001] = quest("Des peaux pour le camp", 34, 32, { 9031 }),
      [7002] = quest("Les écailles du marais", 31, 28, { 9032 }),
      [7003] = quest("Des crocs pour plus tard", 47, 44, { 9033 }),
      [7004] = quest("Secrets de mage", 32, 30, { 9034 }, { requiredClasses = 128 }),
      [7005] = quest("La lettre trouvée", 32, 30, {}),
      [7006] = quest("Des pattes de débutant", 9, 6, { 9036 }),
      [7007] = quest("Commande du tailleur", 32, 30, { 9037 }, { requiredSkill = { 197, 100 } }),
      [7008] = quest("Don d'étoffes", 30, 12, { 9038 }, { specialFlags = 1 }),
      [7009] = quest("Les os du passé", 31, 28, { 9039 }),
    }, items = {
      [9031] = { name = "Peau tannée de test", relatedQuests = { 7001 } }, [9032] = { name = "Écaille de test", relatedQuests = { 7002 } },
      [9033] = { name = "Croc de test", relatedQuests = { 7003 } }, [9034] = { name = "Herbe de mage de test", relatedQuests = { 7004 } },
      [9035] = { name = "Lettre trouvée de test", startQuest = 7005 }, [9036] = { name = "Patte de test", relatedQuests = { 7006 } },
      [9037] = { name = "Fil de tailleur de test", relatedQuests = { 7007 } }, [9038] = { name = "Étoffe de don de test", relatedQuests = { 7008 } },
      [9039] = { name = "Os de test", relatedQuests = { 7009 } },
    } }
  M.InstallQuestieDB(QDB)
  M.SetCompleted({ [7002] = true, [7008] = true })
  local P = B.Predict
  check(P and P.Veto and P.QuestState, "predictors loaded (Modules/BagsPredict.lua)")
  P.ResetQuestie()
  local function clearBags() for b = 0, 4 do M.bags[b].slots = {} end end
  local function bagsChanged() M.FireEvent("BAG_UPDATE_DELAYED") M.Advance(1.2, 0.1) end
  local function show(id, bag, slot)
    local v = B.Verdict(id, bag, slot)
    return ("%s ×%d → %s"):format(tostring(v.name or id), B.Count(id), tostring(B.Text(v))), v
  end

  -- ------------------------------------------------------------------ 1. the incident
  M.player.level = 30
  M.skills = { { name = "Travail du cuir", rank = 143, max = 150 }, { name = "Dépeçage", rank = 140, max = 150 } }
  clearBags()
  M.SetBagItem(0, 1, 6948, 1)
  M.SetBagItem(0, 2, 929, 5)      -- his best healing potions
  M.SetBagItem(0, 3, 858, 4)      -- a lower rank
  M.SetBagItem(0, 4, 9001, 3)     -- grey
  local Pm = T.modules.Prof
  if Pm and Pm.Update then Pm.Update(true) end
  M.units.npc = { name = "Marchand-Test" }
  M.merchantItems = { { id = 2515, price = 10, stack = 200, maxStack = 200 } }
  M.SetMerchant(true)
  M.Advance(4, 0.05)
  out("quêtes à venir : état = " .. tostring(P.QuestState()) .. " (" .. tostring(P.QF.src) .. ")")
  check(P.QuestState() == "fast" and (P.QF.calN or 0) >= 3, "Questie's item → quests link verified on real quests before use (" .. tostring(P.QF.calN) .. " objectives)")
  check(P.RX.ready, "recipe index built in slices (one profession line per image)")
  local CR = T.CityRun
  local cv = CR and CR._active and CR.Verdict and CR.Verdict(929) or nil
  if cv then
    out(("AVANT (cause) : Courses en ville sur Potion de soins : « %s », en trop = %s"):format(tostring(cv.text), tostring(cv.extra)))
    check((tonumber(cv.extra) or 0) >= 5 and tostring(cv.text):find("pas ton métier", 1, true),
      "evidence: the profession verdict calls the healing potions surplus (" .. tostring(cv.text) .. ")")
    local listed = false
    for _, e in ipairs(CR.ExtraList and CR.ExtraList() or {}) do if e.id == 929 and (e.bags or 0) >= 5 then listed = true end end
    check(not listed, "« à vendre » of Courses en ville no longer lists the 5 healing potions (consumable + the bags' verdict)")
    local stillSold = false
    for _, e in ipairs(CR.SellStacks and CR.SellStacks() or {}) do if e.id == 929 then stillSold = true end end
    check(not stillSold, "the profession quick sale no longer lists them (a keeper claims them)")
  else
    check(T.modules.CityRun == nil or not (T.modules.CityRun._active), "CityRun inactive in this profile: bridge silent")
  end
  local line, v = show(929, 0, 2)
  out("incident : " .. line)
  check(v.action == "keep" and v.surplus == 0 and v.keep == 5, "the best healing potions are kept (" .. tostring(v.action) .. ")")
  check(tostring(v.why):find("potion de soins", 1, true), "the reason names the potions: " .. tostring(v.why))
  line, v = show(858, 0, 3)
  out("incident : " .. line)
  check(v.action == "sell" and v.surplus == 4, "lower rank is surplus once 5 better are stocked (" .. tostring(v.action) .. ")")
  check(tostring(v.why):find("Potion de soins", 1, true) and tostring(v.why):find("5 suffisent", 1, true), "…and says why: " .. tostring(v.why))
  local sl = B.SellList()
  local has929, has858 = false, false
  for _, e in ipairs(sl.list) do if e.id == 929 then has929 = true end if e.id == 858 then has858 = true end end
  check(not has929 and has858, "one-click list: never the best potions, the outdated ones yes")
  local greys = T.modules.Inventory and T.modules.Inventory.GreyItems and select(1, T.modules.Inventory.GreyItems()) or {}
  check(#greys == 1 and greys[1].bag == 0 and greys[1].slot == 4, "Inventory.GreyItems goes through Verdict (1 grey stack)")
  -- only 3 best potions: the lower rank is NOT surplus (the better ones do not cover the stock)
  M.SetBagItem(0, 2, 929, 3)
  bagsChanged()
  line, v = show(929, 0, 2)
  out("incident : " .. line)
  check(v.action == "keep" and B.Text(v) == "Garde : 5 potions de soins pour toi (tu en as 3)", "tooltip wording: " .. tostring(B.Text(v)))
  line, v = show(858, 0, 3)
  out("incident : " .. line)
  check(v.action == "keep", "lower rank kept while the better ones are short (" .. tostring(v.why) .. ")")
  -- a far-too-high potion is not « for you » yet, a soon-usable one is kept
  M.SetBagItem(0, 5, 13446, 2)
  M.SetBagItem(0, 6, 3928, 1)
  bagsChanged()
  local _, vHigh = show(13446)
  local _, vSoon = show(3928)
  -- (5.1) he WILL be 45: never « sell ». Far away → the bank, said so
  check(vHigh.action == "bank" and tostring(vHigh.why):find("niveau 45", 1, true) and tostring(vHigh.why):find("banque", 1, true),
    "level-45 potion at 30: kept for later, bank (" .. tostring(vHigh.action) .. " : " .. tostring(vHigh.why) .. ")")
  out("incident : " .. show(13446))
  check(vSoon.action == "keep" and tostring(vSoon.why):find("niveau 35", 1, true), "level-35 potion at 30: kept (" .. tostring(vSoon.why) .. ")")
  -- the best potion can never be surplus, even 40 of them and whatever a keeper says
  M.SetBagItem(0, 2, 929, 5) M.SetBagItem(0, 7, 929, 5) M.SetBagItem(0, 8, 929, 5)
  B.RegisterKeeper("test-en-trop", function(id) if id == 929 then return 0, "un autre module dit : en trop" end end)
  bagsChanged()
  local _, v15 = show(929)
  check(v15.action == "keep" and v15.surplus == 0, "15 best potions + a keeper saying « en trop »: still kept")
  B.UnregisterKeeper("test-en-trop")
  M.SetMerchant(false)
  M.Advance(1, 0.1)

  -- ------------------------------------------------------------------ 2. every keeper
  clearBags()
  M.SetBagItem(0, 1, 6948, 1)       -- hearthstone
  M.SetBagItem(0, 2, 4540, 12)      -- bread (level 1 food)
  M.SetBagItem(0, 3, 1179, 8)       -- water
  M.SetBagItem(0, 4, 3530, 6)       -- bandage
  M.SetBagItem(0, 5, 2672, 4)       -- raw meat: the pet eats it
  M.SetBagItem(0, 6, 9003, 1)       -- quest item
  M.SetBagItem(0, 7, 9004, 1)       -- key
  M.SetBagItem(0, 8, 2901, 1)       -- mining pick
  M.SetBagItem(0, 9, 2901, 1)       -- a second one
  M.SetBagItem(0, 10, 9005, 1)      -- bag
  M.SetBagItem(0, 11, 9006, 1)      -- mount
  M.SetBagItem(0, 12, 17031, 3)     -- mage reagent
  M.SetBagItem(0, 13, 9007, 1)      -- plate chest (hunter cannot wear)
  M.SetBagItem(0, 14, 9001, 3)      -- grey
  M.SetBagItem(0, 15, 9002, 2)      -- grey, no value
  M.SetBagItem(1, 1, 2515, 200)     -- arrows (his bow)
  M.SetBagItem(1, 2, 2519, 200)     -- bullets (no gun)
  M.SetBagItem(1, 3, 99999, 1)      -- an item the client does not know yet
  bagsChanged()
  local expect = {
    { 6948, "keep", "pierre de foyer" }, { 4540, "keep", "repas" }, { 1179, "keep", "boisson" }, { 3530, "keep", "bandage" },   -- (no First Aid read ≠ useless)
    { 2672, "keep", "familier" }, { 9003, "keep", "quête" }, { 9004, "keep", "clé" }, { 9005, "keep", "sac" },
    { 9006, "keep", "monture" }, { 17031, "sell", "mage" }, { 9007, "sell", "porter" }, { 9001, "sell", "gris" },
    { 9002, "destroy?", "gris" }, { 2515, "keep", "munitions" }, { 2519, "destroy?", "flèches" }, { 99999, "keep", "inconnu" },
  }
  for _, e in ipairs(expect) do
    local l, vv = show(e[1])
    out("gardiens : " .. l)
    check(vv.action == e[2] and tostring(vv.why):lower():find(e[3], 1, true),
      ("keeper %d: %s / %s (got %s : %s)"):format(e[1], e[2], e[3], tostring(vv.action), tostring(vv.why)))
  end
  local _, vt = show(2901)
  out("gardiens : " .. show(2901))
  check(vt.keep == 1 and vt.surplus == 1 and vt.action == "sell", "tools: one is enough, the duplicate is surplus")
  check(B.Verdict(2901, 0, 8).action == "sell" and B.Verdict(2901, 0, 9).action == "keep", "…one stack to sell, the other kept")
  -- « garder » mark on the grey, then removed
  B.Mark(9001, "keep")
  check(B.Verdict(9001).action == "keep" and tostring(B.Verdict(9001).why):find("marqué", 1, true), "mark « garder » wins over grey")
  B.Mark(9001, nil)
  check(B.Verdict(9001).action == "sell", "mark removed: grey again")
  -- gear he just took off is kept (« en alternance »)
  M.AddToBags(9008, 1)
  bagsChanged()
  local beforeWorn = B.Verdict(9008)
  out("gardiens : " .. show(9008))
  M.inventory[5] = { id = 9008, count = 1 }
  M.FireEvent("PLAYER_EQUIPMENT_CHANGED", 5, true)
  M.inventory[5] = nil
  M.FireEvent("PLAYER_EQUIPMENT_CHANGED", 5, false)
  M.Advance(1, 0.1)
  local afterWorn = B.Verdict(9008)
  out("gardiens : " .. show(9008))
  check(afterWorn.action == "keep" and tostring(afterWorn.why):find("alternance", 1, true),
    "gear he wore is kept (before: " .. tostring(beforeWorn.action) .. ", after: " .. tostring(afterWorn.why) .. ")")
  -- quest log: an ordinary item a quest asks for (by name) is claimed for the count asked; when the quest is gone the
  -- claim goes too — and only the verdicts touched by the quest change are redone (a kill must not redo the whole bag)
  item(9015, "Laine de quête de test", { stack = 20, sellPrice = 33, classID = 7 })
  M.SetBagItem(2, 8, 9015, 10)
  local quests0 = M.quests
  M.quests = { { title = "Des laines pour l'hiver", level = 28, objectives = { { text = "Laine de quête de test : 10/6", done = true, have = 10, need = 6, type = "item" } } } }
  bagsChanged()
  M.FireEvent("QUEST_LOG_UPDATE")
  M.Advance(4, 0.2)
  out("gardiens : " .. show(9015))
  local vq = B.Verdict(9015)
  check(vq.action == "keep" and vq.claimed and tostring(vq.why):find("Des laines pour l'hiver", 1, true), "item asked by a quest of the log: kept (" .. tostring(vq.why) .. ")")
  B.Invalidate()
  for _, id in ipairs({ 9015, 9003, 6948, 4540, 2515 }) do B.Verdict(id) end
  local cq = B.stats.computed
  M.quests = {}
  M.FireEvent("QUEST_LOG_UPDATE")
  M.Advance(4, 0.2)
  local vq2 = B.Verdict(9015)
  B.Verdict(9003) B.Verdict(6948) B.Verdict(4540) B.Verdict(2515)
  out("gardiens : (quête rendue) " .. show(9015))
  check(not vq2.claimed, "quest gone: the claim goes away (" .. tostring(vq2.why) .. ")")
  check(B.stats.computed - cq <= 2, "…and only the verdicts the quest touched were redone (" .. (B.stats.computed - cq) .. ")")
  M.quests = quests0
  M.SetBagItem(2, 8, nil)
  M.FireEvent("QUEST_LOG_UPDATE")
  bagsChanged()
  -- recipes (client data): learnable now → « à utiliser », later → kept for later, a profession he does not have → surplus
  local R = T.Data and T.Data.Recipes and T.Data.Recipes.r
  local function recipeOf(line, pick)
    for _, r in ipairs(R and R[line] or {}) do if (tonumber(r[8]) or 0) > 0 and pick(tonumber(r[4]) or 1) then return r[8], r[4] end end
  end
  local rNow = recipeOf(165, function(at) return at <= 143 end)
  local rLater, laterAt = recipeOf(165, function(at) return at > 175 end)
  local rOther = recipeOf(197, function() return true end)
  if check(rNow and rLater and rOther, "recipe items found in the client data") then
    item(rNow, "Patron de test (maintenant)", { stack = 1, sellPrice = 100, classID = 9 })
    item(rLater, "Patron de test (plus tard)", { stack = 1, sellPrice = 100, classID = 9 })
    item(rOther, "Patron de couture de test", { stack = 1, sellPrice = 100, classID = 9 })
    M.SetBagItem(2, 8, rNow, 1) M.SetBagItem(2, 9, rLater, 1) M.SetBagItem(2, 10, rOther, 1)
    bagsChanged()
    for _, id in ipairs({ rNow, rLater, rOther }) do out("gardiens : " .. show(id)) end
    check(B.Verdict(rNow).action == "use" and tostring(B.Verdict(rNow).why):find("apprendre maintenant", 1, true), "recipe learnable now: « à utiliser »")
    check(B.Verdict(rLater).action == "bank" and tostring(B.Verdict(rLater).why):find(tostring(laterAt), 1, true), "recipe for later: kept (bank)")
    check(B.Verdict(rOther).action == "ah" and tostring(B.Verdict(rOther).why):find("tu n'as pas ce métier", 1, true),
      "recipe of a profession he does not have: surplus, AH first (" .. tostring(B.Verdict(rOther).why) .. ")")
    M.SetBagItem(2, 8, nil) M.SetBagItem(2, 9, nil) M.SetBagItem(2, 10, nil)
    bagsChanged()
  end
  -- in a group of 4, a tradable piece he cannot wear may serve a friend: the bridge keeps quiet (GEAR's keeper knows)
  B.Mark(9007, nil)
  local soloV = B.Verdict(9007)
  M.group = { { name = "Ami-Un", class = "WARRIOR", level = 30, online = true }, { name = "Ami-Deux", class = "MAGE", level = 30, online = true },
    { name = "Ami-Trois", class = "PRIEST", level = 29, online = true } }
  M.FireEvent("GROUP_ROSTER_UPDATE")
  B.Invalidate()
  local groupV = B.Verdict(9007)
  out("gardiens : (groupe de 4) " .. show(9007))
  check(soloV.action == "sell" and groupV.action == "keep" and not groupV.claimed,
    "plate chest: surplus alone, kept in a group (solo " .. tostring(soloV.action) .. " / group " .. tostring(groupV.action) .. ")")
  check(B.TipLine(9007) == nil, "…and no tooltip line when we do not know")
  M.group = {}
  M.FireEvent("GROUP_ROSTER_UPDATE")
  B.Invalidate()
  -- registered keepers: claim, explicit surplus, partial claim, error
  M.SetBagItem(2, 1, 9009, 20) M.SetBagItem(2, 2, 9009, 10) M.SetBagItem(2, 3, 9010, 20) M.SetBagItem(2, 4, 9011, 8)
  bagsChanged()
  check(B.Verdict(9009).action == "keep" and not B.Verdict(9009).claimed, "trade good nobody knows: kept (" .. tostring(B.Verdict(9009).why) .. ")")
  B.RegisterKeeper("test-plan", function(id)
    if id == 9009 then return 12, "ton plan de métier en utilise 12" end
    if id == 9010 then return 0, "aucune recette n'en a besoin" end
    if id == 9011 then error("boom") end
  end)
  local _, vs = show(9009)
  out("gardiens : " .. show(9009))
  check(vs.keep == 12 and vs.surplus == 18 and B.Verdict(9009, 2, 2).action ~= "keep" and B.Verdict(9009, 2, 1).action == "keep",
    "partial claim: keep 12, the 10-stack is the surplus lot, the 20-stack stays")
  out("gardiens : " .. show(9010))
  check(B.Verdict(9010).action ~= "keep", "explicit « en trop » from a keeper: sellable")
  out("gardiens : " .. show(9011))
  check(B.Verdict(9011).action == "keep" and tostring(B.Verdict(9011).why):find("erreur", 1, true), "a keeper in error claims everything")
  check(#B.Keepers() == 1 and B.Keepers()[1] == "test-plan", "Keepers() lists the registered ones")

  -- ------------------------------------------------------------------ 3. AH vs vendor
  local atr = { [9009] = 900, [9010] = 30 }
  rawset(_G, "Auctionator", { API = { v1 = {
    GetAuctionPriceByItemID = function(caller, id) if caller ~= "TreeAI" then error("callerID") end return atr[id] end,
    GetVendorPriceByItemID = function() return nil end, GetAuctionAgeByItemID = function() return 0 end,
    RegisterForDBUpdate = function() end } } })
  B.Invalidate()
  local _, va = show(9009)
  out("HV ou marchand : " .. show(9009))
  check(va.action == "ah" and va.value > 150 * 10 and tostring(va.why):find("HV", 1, true), "AH clearly better (900 vs 150): « ah »")
  out("HV ou marchand : " .. show(9010))
  check(B.Verdict(9010).action == "sell", "AH worse than the vendor (30 vs 33): vendor")
  -- a trade good nobody claims: proposed to the AH only when its price there is known and clearly better; a
  -- component of one of HIS professions is kept (the bridge, until PROF registers its own keeper)
  B.UnregisterKeeper("test-plan")
  item(2319, "Cuir moyen", { stack = 20, sellPrice = 50, classID = 7 })
  item(9014, "Minerai de test", { stack = 20, sellPrice = 20, classID = 7 })
  M.SetBagItem(2, 6, 2319, 6) M.SetBagItem(2, 7, 9014, 10)
  atr[2319], atr[9014] = 400, 300
  bagsChanged()
  B.Invalidate(2319) B.Invalidate(9014) B.Invalidate(9011)
  out("HV ou marchand : " .. show(2319))
  check(B.Verdict(2319).action == "keep" and tostring(B.Verdict(2319).why):find("Travail du cuir", 1, true), "component of his leatherworking: kept")
  out("HV ou marchand : " .. show(9014))
  check(B.Verdict(9014).action == "ah" and tostring(B.Verdict(9014).why):find("personne ne le réclame", 1, true), "unclaimed trade good with a good AH price: « ah »")
  out("HV ou marchand : " .. show(9011))
  check(B.Verdict(9011).action == "keep" and not B.Verdict(9011).claimed, "unclaimed trade good with no AH price: kept, never vendored")
  check(B.TipLine(9011) == nil and B.TipLine(9014) ~= nil, "tooltip: nothing to say when we do not know")
  B.RegisterKeeper("test-plan", function(id)
    if id == 9009 then return 12, "ton plan de métier en utilise 12" end
    if id == 9010 then return 0, "aucune recette n'en a besoin" end
  end)
  M.SetBagItem(2, 5, 9012, 1)
  B.RegisterKeeper("test-gear", function(id) if id == 9012 then return 0, "personne n'en veut" end end)
  bagsChanged()
  local _, vu = show(9012)
  out("HV ou marchand : " .. show(9012))
  check(vu.action == "ah" and vu.ahUnknown and tostring(vu.why):find("inconnu", 1, true), "green with no scanned price: never dumped by the one-click")
  local inSell = false
  for _, e in ipairs(B.SellList().list) do if e.id == 9012 or e.id == 9009 then inSell = true end end
  check(not inSell, "« ah » items are not in the vendor one-click list")
  B.db.ahUnknown = true
  B.Invalidate()
  check(B.Verdict(9012).action == "sell", "option « sans prix d'HV : vendre au marchand » on: vendor")
  B.db.ahUnknown = false
  B.UnregisterKeeper("test-gear")
  rawset(_G, "Auctionator", nil)
  B.Invalidate()

  -- ------------------------------------------------------------------ 4. merchant card
  M.units.npc = { name = "Marchand-Test" }
  M.merchantItems = { { id = 2515, price = 10, stack = 200, maxStack = 200 }, { id = 4541, price = 25, stack = 5, maxStack = 20 } }
  M.inventory[1] = { id = 9008, count = 1, dur = 10, durMax = 60 }
  for i = 6, 10 do M.SetBagItem(2, i, 9001, i) end          -- 5 more grey stacks: more than one batch
  M.FireEvent("BAG_UPDATE_DELAYED")
  M.sold = 0
  local money0 = M.player.money
  M.SetMerchant(true)
  M.Advance(2, 0.1)
  local card = B.GetCard("merchant")
  check(card and card:IsShown(), "merchant card shown under the merchant window")
  local m = B.cardModel
  out("carte marchand : " .. tostring(m and m.label) .. " | " .. tostring(m and m.info))
  check(m and m.label:find("^Vendre %d+ objets · ") and m.label:find("réparer", 1, true), "label « Vendre N objets · X + réparer »: " .. tostring(m and m.label))
  check(tostring(m.info):find("Annulable : onglet « Rachat »", 1, true), "undo hint (buyback tab) shown")
  local names = {}
  for _, e in ipairs(m.rows) do names[#names + 1] = e.name .. " ×" .. e.n end
  out("carte marchand : liste = " .. table.concat(names, ", "))
  for _, e in ipairs(m.rows) do
    check(B.Verdict(e.id).action == "sell", "every listed item has verdict « sell »: " .. e.name)
    check(e.id ~= 4540 and e.id ~= 2515 and e.id ~= 9003 and e.id ~= 6948, "never a kept item in the list: " .. e.name)
  end
  -- hover = the rows; right-click on a row = « garder »
  B.CardExpand("merchant", true)
  local row = card.rows[1]
  check(row and row:IsShown() and row.e, "hover: rows shown")
  local excluded = row.e.id
  M.Hw(function() row:GetScript("OnClick")(row, "RightButton") end)
  M.Advance(3, 0.05)
  check(B.MarkOf(excluded) == "keep" and B.Verdict(excluded).action == "keep", "right-click on a row: kept from now on")
  local n1 = #B.cardModel.rows
  check(n1 == #m.rows - 1, "the list shrinks by that item (" .. #m.rows .. " → " .. n1 .. ")")
  B.Mark(excluded, nil)
  M.Advance(1, 0.1)
  -- never in combat
  M.EnterCombat()
  M.Hw(function() T:UserAction(B.SellClick) end)
  check((M.sold or 0) == 0 and not B.sale, "in combat: nothing sold")
  M.LeaveCombat()
  M.Advance(1.5, 0.1)
  -- outside a click: refused
  check(B.SellClick() == 0 or (M.sold or 0) == 0, "outside a click: nothing sold")
  B.sale = nil
  -- ONE click: batches, each stack re-read just before
  local want = #B.SellList().list
  local wantValue = B.SellList().value
  M.Hw(function() card.btn:GetScript("OnClick")(card.btn, "LeftButton") end)
  local first = M.sold or 0
  check(first == 6 and B.sale and want > 6, "first batch inside the click: " .. first .. " stacks of " .. want .. " (6 per batch)")
  -- a stack changes between two batches (the player moved it): skipped, never sold blind
  local pending = B.sale and B.sale.q[B.sale.i + 1]
  if pending then M.SetBagItem(pending.bag, pending.slot, 4540, 3) end
  M.Advance(3, 0.1)
  check(not B.sale, "sale finished")
  local sold = M.sold or 0
  out(("carte marchand : 1 clic → %d piles vendues sur %d, +%s ; ignorées : %s"):format(sold, want, B.Money(M.player.money - money0),
    tostring(B.lastSale and B.lastSale.skipped or 0)))
  check(pending ~= nil and sold == want - 1 and B.lastSale and B.lastSale.skipped == 1, "all listed stacks sold but the one that changed")
  if pending then
    local s2 = M.bags[pending.bag].slots[pending.slot]
    check(s2 and s2.id == 4540, "the changed stack (now bread) was not sold")
  end
  check(M.bags[0].slots[2] and M.bags[0].slots[2].id == 4540 and M.bags[1].slots[1] and M.bags[0].slots[6], "food, arrows, quest item still there")
  M.FireEvent("BAG_UPDATE_DELAYED")
  M.Advance(1.5, 0.1)
  local m2 = B.cardModel
  out("carte marchand : après = " .. tostring(m2 and m2.label) .. " | " .. tostring(m2 and m2.info))
  check(m2 and tostring(m2.info):find("Rachat", 1, true) and tostring(m2.info):find("Vendu", 1, true), "after the sale: « Vendu … Rachat »")
  check(wantValue > 0, "sale value known")
  -- 20 non-grey stacks: one click sells at most 12 (the buyback keeps 12)
  for i = 1, 20 do M.SetBagItem(3, i <= 10 and i or i - 10, nil) end
  M.bags[3].size, M.bags[4].size = 10, 10
  for i = 1, 10 do M.SetBagItem(3, i, 9013, 1) M.SetBagItem(4, i, 9013, 1) end
  B.RegisterKeeper("test-babiole", function(id) if id == 9013 then return 0, "ne sert à rien" end end)
  M.FireEvent("BAG_UPDATE_DELAYED")
  M.Advance(1.5, 0.1)
  local sl12 = B.SellList()
  local nonGrey = 0
  for _, e in ipairs(sl12.list) do if not e.grey then nonGrey = nonGrey + 1 end end
  out("carte marchand : " .. tostring(B.cardModel and B.cardModel.label) .. " | " .. tostring(B.cardModel and B.cardModel.info))
  check(nonGrey == 12 and sl12.rest == 8, "20 sellable stacks: 12 in this click, 8 after (" .. nonGrey .. " / " .. sl12.rest .. ")")
  check(tostring(B.cardModel.info):find("Puis 8 autres", 1, true), "the card says the rest comes with the next click")
  for b = 3, 4 do M.bags[b].slots = {} end
  B.UnregisterKeeper("test-babiole")
  M.SetMerchant(false)
  M.Advance(1, 0.1)
  check(not card:IsShown(), "merchant closed: card hidden")
  -- the automatic grey sale (QoL) uses the same brain: a grey marked « garder » is not sold
  if Q and Q.db then
    M.SetBagItem(3, 1, 9001, 4) M.SetBagItem(3, 2, 9001, 2)
    M.FireEvent("BAG_UPDATE_DELAYED")
    M.Advance(1, 0.1)
    B.Mark(9001, "keep")
    Q.db.sellRepair = true
    M.sold = 0
    M.SetMerchant(true)
    M.Advance(3, 0.1)
    check((M.sold or 0) == 0, "auto grey sale: a grey marked « garder » stays")
    M.SetMerchant(false)
    M.Advance(1, 0.1)
    B.Mark(9001, nil)
    M.sold = 0
    M.SetMerchant(true)
    M.Advance(3, 0.1)
    check((M.sold or 0) == 2, "auto grey sale: both grey stacks sold (" .. tostring(M.sold) .. ")")
    M.SetMerchant(false)
    M.Advance(1, 0.1)
    Q.db.sellRepair = false
  end

  -- ------------------------------------------------------------------ 5. bank
  local CC = rawget(_G, "C_Container")
  local useReal = CC and CC.UseContainerItem or rawget(_G, "UseContainerItem")
  local deposits = {}
  local function useWrap(bag, slot)
    local s = M.bags[bag] and M.bags[bag].slots[slot]
    if s and M.bankOpen and not M.merchantOpen then           -- a bag stack with the bank open goes to the bank
      if not M.hardware then M.Violation("automation", "deposit outside a click") end
      deposits[#deposits + 1] = { id = s.id, n = s.count }
      M.bags[bag].slots[slot] = nil
      return
    end
    return useReal(bag, slot)
  end
  if CC and CC.UseContainerItem then CC.UseContainerItem = useWrap else rawset(_G, "UseContainerItem", useWrap) end
  clearBags()
  M.SetBagItem(0, 1, 6948, 1)
  M.SetBagItem(0, 2, 929, 2)        -- 2 potions: 3 missing
  M.SetBagItem(0, 3, 9011, 8)
  M.SetBagItem(0, 4, 9011, 5)
  B.UnregisterKeeper("test-plan")
  B.Mark(9011, "bank")
  local bankBag = M.BankBagIDs()[1]
  M.SetBankItem(bankBag, 1, 929, 5)
  M.SetBankItem(bankBag, 2, 858, 5)
  M.FireEvent("BAG_UPDATE_DELAYED")
  M.SetBank(true)
  M.Advance(2, 0.1)
  local bcard = B.GetCard("bank")
  local plan = B.bankPlan
  check(bcard and bcard:IsShown() and plan and #plan.dep == 2, "bank card: 2 stacks to deposit (mark « banque »)")
  check(plan and #plan.wd == 1 and plan.wd[1].id == 929, "…and the 5 healing potions of the bank to withdraw (3 missing)")
  out("banque : " .. tostring(bcard and bcard.btn and bcard.btn.label and bcard.btn.label:GetText()) .. " | " .. tostring(bcard and bcard.info and bcard.info:GetText()))
  M.Hw(function() bcard.btn:GetScript("OnClick")(bcard.btn, "LeftButton") end)
  check(#deposits == 2, "one click: the 2 stacks deposited (" .. #deposits .. ")")
  M.FireEvent("BAG_UPDATE_DELAYED")
  M.Advance(1.5, 0.1)
  out("banque : " .. tostring(bcard.btn.label and bcard.btn.label:GetText()) .. " | " .. tostring(bcard.info and bcard.info:GetText()))
  M.withdrawn = {}
  M.Hw(function() bcard.btn:GetScript("OnClick")(bcard.btn, "LeftButton") end)
  check(#M.withdrawn == 1 and M.withdrawn[1].id == 929, "next click: ONE stack of healing potions withdrawn")
  M.FireEvent("BAG_UPDATE_DELAYED")
  M.Advance(1.5, 0.1)
  check(not bcard:IsShown(), "nothing left to move: bank card hidden")
  M.SetBank(false)
  M.Advance(1, 0.1)
  if CC and CC.UseContainerItem then CC.UseContainerItem = useReal else rawset(_G, "UseContainerItem", useReal) end
  B.Mark(9011, nil)

  -- ------------------------------------------------------------------ 6. full bags, 7. overlays, legend, tooltip, sort
  clearBags()
  for b = 1, 4 do M.bags[b].size = 0 end
  M.bags[0].size = 16
  M.SetBagItem(0, 1, 6948, 1)
  M.SetBagItem(0, 2, 929, 5)
  M.SetBagItem(0, 3, 9001, 3)       -- grey, 36 c
  M.SetBagItem(0, 4, 9002, 2)       -- grey, no value: the cheapest
  M.SetBagItem(0, 5, 9003, 1)       -- quest
  M.SetBagItem(0, 6, 9010, 20)
  for i = 7, 14 do M.SetBagItem(0, i, 4540, 20) end   -- 2 free slots left
  B.RegisterKeeper("test-plan", function(id) if id == 9010 then return 0, "aucune recette n'en a besoin" end end)
  local cf = rawget(_G, "ContainerFrame1") or CreateFrame("Frame", "ContainerFrame1", UIParent)
  cf:SetID(0)
  local btns = {}
  for i = 1, 16 do
    local b = rawget(_G, "ContainerFrame1Item" .. i) or CreateFrame("Button", "ContainerFrame1Item" .. i, cf)
    b:SetID(i)
    b:Show()
    btns[i] = b
  end
  cf:Show()
  M.FireEvent("BAG_UPDATE_DELAYED")
  M.Advance(2, 0.1)
  local full
  for _, e in ipairs(T:GetSuggestions()) do if tostring(e.id) == "Bags:full" then full = e end end
  out("sacs pleins : " .. tostring(full and full.text))
  check(full and full.text == "2 places : vends / jette Plume sans valeur ×2 (0 pc)", "warning before the bags are full: " .. tostring(full and full.text))
  B.RefreshOverlays(true)
  M.Advance(1, 0.1)
  local kinds = {}
  for i = 1, 16 do kinds[i] = B.OverlayKind(btns[i]) or "-" end
  out("pastilles : " .. table.concat(kinds, " "))
  check(kinds[3] == "sell" and kinds[4] == "destroy" and kinds[6] == "ah" and kinds[2] == "-" and kinds[5] == "-" and kinds[1] == "-",
    "coin on what sells, AH icon, cross on the cheapest, nothing on kept items")
  out("légende : " .. tostring(B.legendText))
  check(tostring(B.legendText):find("vendre", 1, true) and tostring(B.legendText):find(" HV", 1, true)
    and tostring(B.legendText):find("à jeter d'abord", 1, true), "legend line")
  -- loot one more grey: only that slot's button is touched
  local before = B.stats.overlays
  M.SetBagItem(0, 15, 9001, 1)
  M.FireEvent("BAG_UPDATE_DELAYED")
  M.Advance(2, 0.1)
  local touched = B.stats.overlays - before
  out(("pastilles : 1 objet ramassé → %d bouton(s) retouché(s)"):format(touched))
  check(B.OverlayKind(btns[15]) ~= nil and touched <= 3, "overlays: only the changed slots are redrawn (" .. touched .. ")")
  -- optional lock on kept items
  B.db.keepIcon = true
  B.RefreshOverlays(true)
  M.Advance(1, 0.1)
  check(B.OverlayKind(btns[2]) == "keep" and B.OverlayKind(btns[5]) == "keep", "option: lock on what a keeper claims")
  B.db.keepIcon = false
  B.RefreshOverlays(true)
  M.Advance(1, 0.1)
  -- tooltip: one line, the same verdict
  local tipText = B.TipLine(929, 0, 2)
  out("infobulle : " .. tostring(tipText))
  check(tipText == "TreeAI · Garde : ta meilleure potion de soins (tu en as 5)", "tooltip line: " .. tostring(tipText))
  check(B.TipLine(123456) == nil, "no line for an item he does not own")
  local cbs = {}
  check(B.HookTooltip({ AddTooltipPostCall = function(kind, fn) cbs[kind] = fn end }, { Item = 0 }) and type(cbs[0]) == "function",
    "hooked through TooltipDataProcessor (item tooltips)")
  GameTooltip.__lines = {}
  cbs[0](GameTooltip, { id = 9001 })
  check(#GameTooltip.__lines == 1 and GameTooltip.__lines[1] == "TreeAI · Objet gris : ne sert à rien → vends au marchand (48 pc)",
    "post-call adds the line: " .. tostring(GameTooltip.__lines[1]))
  M.EnterCombat()
  GameTooltip.__lines = {}
  cbs[0](GameTooltip, { id = 9001 })
  check(#GameTooltip.__lines == 0, "in combat: tooltip untouched")
  M.LeaveCombat()
  M.Advance(1.5, 0.1)
  -- « Ranger mes sacs »: the game's sort, only inside a click
  local sorted = 0
  local hadSort = CC and CC.SortBags
  if CC then CC.SortBags = function() if not M.hardware then M.Violation("automation", "SortBags outside a click") end sorted = sorted + 1 end end
  check(B.Sort() == false and sorted == 0, "sort outside a click: refused")
  M.Hw(function() T:UserAction(B.Sort) end)
  check(not CC or sorted == 1, "sort inside a click: the game's sort called once")
  if CC then CC.SortBags = hadSort end
  -- « Il te manque » + Shopping()
  M.SetBagItem(0, 2, 929, 3)
  for i = 7, 14 do M.SetBagItem(0, i, nil) end
  M.SetBagItem(0, 7, 4540, 4)
  M.SetBagItem(0, 8, 2515, 150)
  M.FireEvent("BAG_UPDATE_DELAYED")
  M.Advance(1.5, 0.1)
  local shop = B.Shopping()
  out("stock : " .. tostring(B.MissingText()))
  local byCat = {}
  for _, e in ipairs(shop) do byCat[e.cat] = e end
  check(byCat.hpot and byCat.hpot.n == 2 and byCat.hpot.id == 1710, "missing: 2 healing potions (has 3 of 5), the level-21 one named")
  check(byCat.food and byCat.food.id == 4541, "food named = the one seen at a merchant (not his level-1 bread)")
  check(byCat.food and byCat.food.n == 16, "missing: 16 meals (has 4 of 20)")
  check(byCat.arrow and byCat.arrow.n == 400 and byCat.arrow.want == 400, "missing: arrows up to 400 (has 150)")
  check(not byCat.water and not byCat.mpot, "hunter: no water / mana potion asked")
  check(tostring(B.MissingText()):find("^Il te manque : 2 potions de soins"), "text: " .. tostring(B.MissingText()))

  -- page « Sacs »
  local W = T.Window
  if W and W.Open and W._active then
    W.Open("bags")
    M.Advance(1, 0.2)
    local labels = {}
    for _, rr in ipairs(W.lastLay and W.lastLay.rects or {}) do
      if rr.kind == "tile" or rr.kind == "header" or rr.kind == "row" then labels[#labels + 1] = rr.kind .. ":" .. tostring(rr.label) end
    end
    for _, l in ipairs(labels) do out("page : " .. l) end
    local joined = table.concat(labels, "\n")
    check(joined:find("tile:Vendre", 1, true) and joined:find("tile:Ranger", 1, true) and joined:find("tile:Ce qu'il te manque", 1, true), "page: the 3 big actions")
    check(joined:find("header:Vendre au marchand", 1, true) and joined:find("header:Garder", 1, true) and joined:find("header:Il te manque", 1, true), "page: sections")
    if W.Close then W.Close() elseif W.Toggle then W.Toggle() end
    M.Advance(1, 0.2)
  end
  B.UnregisterKeeper("test-plan")

  -- ------------------------------------------------------------------ 8. looting with the bags closed: nothing computed
  local legend = B.GetLegend()
  check(legend and legend:IsShown(), "legend shown above the open bags")
  cf:Hide()
  M.FireEvent("BAG_CLOSED", 0)
  M.Advance(1, 0.1)
  check(legend and not legend:IsShown(), "bags closed: the legend goes with them")
  for i = 9, 14 do M.SetBagItem(0, i, nil) end
  M.FireEvent("BAG_UPDATE_DELAYED")
  M.Advance(2, 0.1)
  local c0 = B.stats.computed
  for i = 1, 4 do
    M.SetBagItem(0, 8 + i, 9001, i)
    M.FireEvent("BAG_UPDATE", 0)
    M.FireEvent("BAG_UPDATE_DELAYED")
    M.Advance(0.2, 0.05)
  end
  M.Advance(3, 0.1)
  out(("butin sacs fermés : %d avis calculés pour 4 objets ramassés"):format(B.stats.computed - c0))
  check(B.stats.computed == c0, "bags closed, room left: looting computes no verdict")
  -- in combat (each shot uses an arrow → BAG_UPDATE): nothing at all, done once after the fight
  M.EnterCombat()
  for _ = 1, 10 do M.FireEvent("BAG_UPDATE", 1) M.FireEvent("BAG_UPDATE_DELAYED") M.Advance(0.3, 0.1) end
  check(B.stats.computed == c0, "in combat: no verdict computed")
  M.LeaveCombat()
  M.Advance(2, 0.1)

  -- ================================================================== 5.1 — PREDICT, never guess
  local function settle(sec) M.FireEvent("BAG_UPDATE_DELAYED") M.Advance(sec or 1.5, 0.05) end
  local function line(id, bag, slot) return tostring(B.Line(B.Verdict(id, bag, slot))) end
  local function has(list, id) for _, e in ipairs(list) do if e.id == id then return e end end end
  M.SetMerchant(false)
  M.Advance(0.5, 0.1)
  cf:Hide()
  M.bags[0].size = 16
  for b = 1, 4 do M.bags[b].size = 16 end

  -- ------------------------------------------------------------------ 10. quests he has NOT taken yet (Questie, read in game)
  clearBags()
  M.player.level = 30
  M.quests = {}
  M.FireEvent("QUEST_LOG_UPDATE")
  for i, id in ipairs({ 9031, 9032, 9033, 9034, 9035, 9036, 9037, 9038, 9039 }) do M.SetBagItem(0, i, id, id == 9035 and 1 or 8) end
  -- somebody (a keeper) calls each of them surplus: without the prediction they would all be sold
  B.RegisterKeeper("test-surplus", function(id) if id >= 9031 and id <= 9040 then return 0, "test : aucun plan n'en veut" end end)
  rawset(_G, "Auctionator", { API = { v1 = { GetAuctionPriceByItemID = function() return 10 end } } })   -- AH worth less than the vendor
  settle(3)
  local want = {
    { 9031, "keep", "Garde : pour la quête « Des peaux pour le camp » (dès le niveau 32) — nombre demandé inconnu, je garde tout" },
    { 9032, "sell", "Test : aucun plan n'en veut → vends au marchand (3 pa 20 pc)", "quête « Les écailles du marais » : déjà faite" },
    { 9033, "bank", "À la banque : pour la quête « Des crocs pour plus tard » (niveau 44) : à la banque en attendant" },
    { 9034, "sell", "Test : aucun plan n'en veut → vends au marchand (3 pa 20 pc)", "quête « Secrets de mage » : pas pour ta classe / ta faction" },
    { 9035, "use", "À utiliser : lance la quête « La lettre trouvée » (clic droit dessus)" },
    { 9036, "sell", "Test : aucun plan n'en veut → vends au marchand (3 pa 20 pc)", "quête « Des pattes de débutant » : trop basse pour toi" },
    { 9037, "sell", "Test : aucun plan n'en veut → vends au marchand (3 pa 20 pc)", "quête « Commande du tailleur » : demande un métier que tu n'as pas" },
    { 9038, "keep", "Garde : pour la quête répétable « Don d'étoffes » (pas encore prise) — nombre demandé inconnu, je garde tout" },
    { 9039, "keep", "Garde : pour la quête « Les os du passé » (pas encore prise) — nombre demandé inconnu, je garde tout" },
  }
  for _, w in ipairs(want) do
    local v = B.Verdict(w[1])
    out("quêtes à venir : " .. tostring(v.name) .. " → " .. line(w[1]))
    check(v.action == w[2] and line(w[1]) == w[3], ("future quest %d: %s / « %s » (got %s / « %s »)"):format(w[1], w[2], w[3], tostring(v.action), line(w[1])))
    if w[4] then
      local said = false
      for _, r in ipairs(v.reasons or {}) do if r == w[4] then said = true end end
      check(said, "…and the quest that was ruled out is named with the cause: " .. w[4])
    end
  end
  local sl10 = B.SellList()
  check(not has(sl10.list, 9031) and not has(sl10.list, 9033) and not has(sl10.list, 9035) and not has(sl10.list, 9038) and not has(sl10.list, 9039),
    "one-click list: never an item a quest to come asks for")
  check(has(sl10.list, 9032) and has(sl10.list, 9034) and has(sl10.list, 9036) and has(sl10.list, 9037), "…the four ruled out by data are in it")
  -- he takes the quest (the game now gives the count), then turns it in: only then the item may go
  local cQ = B.stats.computed
  M.quests = { { id = 7009, title = "Les os du passé", level = 31, objectives = { { text = "Os de test : 8/6", done = true, have = 8, need = 6, type = "item" } } } }
  M.FireEvent("QUEST_LOG_UPDATE")
  M.Advance(3, 0.1)
  out("quêtes à venir : (prise) " .. line(9039))
  check(B.Verdict(9039).action == "keep" and tostring(B.Verdict(9039).why):find("Les os du passé", 1, true), "quest taken: claimed by the log (" .. tostring(B.Verdict(9039).why) .. ")")
  M.quests = {}
  M.SetCompleted({ [7002] = true, [7008] = true, [7009] = true })
  M.FireEvent("QUEST_LOG_UPDATE")
  M.Advance(3, 0.1)
  out("quêtes à venir : (rendue) " .. line(9039))
  check(B.Verdict(9039).action == "sell", "quest turned in: ruled out, sellable (" .. line(9039) .. ")")
  for _, w in ipairs(want) do B.Verdict(w[1]) end
  check(B.stats.computed - cQ <= 20, "quest-log changes redo only the verdicts that depend on quests (" .. (B.stats.computed - cQ) .. ")")
  -- he levels: a quest of level 44 comes within 8 levels at 36 → out of the bank, into the bags
  M.player.level = 36
  M.Advance(0.2, 0.1)
  out("quêtes à venir : (niveau 36) " .. line(9033))
  check(B.Verdict(9033).action == "keep" and line(9033):find("dès le niveau 44", 1, true), "level 36: the level-44 quest is now « soon » (" .. line(9033) .. ")")
  M.player.level = 30
  M.Advance(0.2, 0.1)

  -- ------------------------------------------------------------------ 11. no Questie / link not verified → never the one-click
  M.SetBagItem(1, 1, 17031, 3)     -- mage rune: useless for a hunter (class reagent)
  M.SetBagItem(1, 2, 9001, 3)      -- a grey
  local keepLib = { rawget(_G, "LibQuestieDB"), rawget(_G, "QuestDB"), rawget(_G, "NpcDB"), rawget(_G, "ObjectDB"), rawget(_G, "ItemDB") }
  for _, n in ipairs({ "LibQuestieDB", "QuestDB", "NpcDB", "ObjectDB", "ItemDB" }) do rawset(_G, n, nil) end
  P.ResetQuestie()
  settle(2)
  local vA = B.Verdict(17031)
  out("sans Questie : " .. line(17031))
  check(P.QuestState() == "absent" and vA.action == "keep" and vA.confirm and vA.confirm.action == "sell",
    "no Questie: nothing but « keep » for the rest of the addon, the possible sale is only a « confirm » (" .. tostring(vA.action) .. ")")
  check(line(17031) == "À confirmer : Composant de mage : inutile pour toi → vends au marchand (7 pa 50 pc) · quêtes à venir inconnues (Questie absent)",
    "…and the line says what is not known: " .. line(17031))
  local slA = B.SellList()
  check(not has(slA.list, 17031) and has(slA.apart, 17031) and has(slA.list, 9001), "one-click = the grey only; the rune is listed apart")
  -- Questie present but its item → quests link is NOT there: not trusted, the player can have the whole base re-read
  local QDB2 = { quests = QDB.quests, items = {} }
  for id, row in pairs(QDB.items) do QDB2.items[id] = { name = row.name, startQuest = row.startQuest } end
  M.InstallQuestieDB(QDB2)
  P.ResetQuestie()
  M.units.npc = { name = "Marchand-Test" }
  M.merchantItems = {}
  M.SetMerchant(true)
  M.Advance(3, 0.05)
  check(P.QuestState() == "unverified", "link not found on real quests: state « unverified » (" .. tostring(P.QuestState()) .. ")")
  out("lien non vérifié : " .. line(17031))
  local mU = B.cardModel
  out("lien non vérifié : carte = " .. tostring(mU and mU.label) .. " | " .. tostring(mU and mU.info))
  check(mU and mU.verify and has(mU.apart, 17031), "merchant card: the rune apart + a « Vérifier mes quêtes à venir » line")
  local cardU = B.GetCard("merchant")
  B.CardExpand("merchant", true)
  local vrow
  for _, r in ipairs(cardU.rows) do if r:IsShown() and r.e and r.e.verify then vrow = r end end
  local scanned0 = P.stats.scanned
  if check(vrow, "the « verify » row is shown") then
    M.Hw(function() vrow:GetScript("OnClick")(vrow, "LeftButton") end)
    check(P.QuestState() == "scan", "his click starts the full read, sliced")
    M.Advance(3, 0.05)
  end
  check(P.QuestState() == "index" and P.stats.scanned - scanned0 == 9, "whole base read once (" .. (P.stats.scanned - scanned0) .. " quests), state « index »")
  out("base relue : " .. tostring(P.QF.src) .. " → " .. line(17031) .. " | " .. line(9031))
  check(B.Verdict(17031).action == "sell" and B.Verdict(9031).action == "keep", "after the read: the rune is a sure sale, the quest hide is kept")
  check(type(B.db.qidx) == "table" and B.db.qidx.by[9031] == 7001, "the index is saved for the next sessions")
  P.ResetQuestie()                 -- « next session »
  P.QuestState()                   -- (asked the first time a sale is considered: here, by hand)
  M.Advance(2, 0.05)
  check(P.QuestState() == "index" and P.stats.scanned - scanned0 == 9 and tostring(P.QF.src):find("gardé", 1, true),
    "next session: the saved index is reused, nothing re-read (" .. tostring(P.QF.src) .. ")")
  M.SetMerchant(false)
  M.Advance(0.5, 0.1)
  B.db.qidx = false
  for i, n in ipairs({ "LibQuestieDB", "QuestDB", "NpcDB", "ObjectDB", "ItemDB" }) do rawset(_G, n, keepLib[i]) end
  M.InstallQuestieDB(QDB)
  P.ResetQuestie()
  B.UnregisterKeeper("test-surplus")
  P.QuestState()
  settle(3)
  check(P.QuestState() == "fast", "back to the verified link")

  -- ------------------------------------------------------------------ 12. professions: « utile à mon métier » … mais en ai-je besoin ?
  -- found in the client recipe data (the oracle is the table, not the predictor)
  local RD = T.Data.Recipes
  local function usedBy(id)
    local by = {}
    for ln, rows in pairs(RD.r) do
      for _, r in ipairs(rows) do
        for k = 1, #r[10], 2 do if r[10][k] == id then by[ln] = math.max(by[ln] or 0, r[6]) end end
      end
    end
    return by
  end
  local mine = { [165] = 143, [393] = 140 }
  local pastID, pastAt
  for _, r in ipairs(RD.r[165]) do
    for k = 1, #r[10], 2 do
      local id = r[10][k]
      local by = usedBy(id)
      local only = true
      for ln in pairs(by) do if ln ~= 165 then only = false end end
      if only and by[165] <= 143 and not pastID and id ~= 2319 then pastID, pastAt = id, by[165] end
    end
  end
  local byDye = usedBy(2604)
  check(byDye[197] and not byDye[165] and not byDye[393], "client data: red dye serves Tailoring, not his professions")
  if check(pastID, "client data: a leatherworking component whose recipes are all grey at 143") then
    item(pastID, "Compo dépassée de test", { stack = 20, sellPrice = 30, classID = 7 })
  end
  item(2604, "Teinture rouge", { stack = 20, sellPrice = 50, classID = 7 })
  item(2319, "Cuir moyen", { stack = 20, sellPrice = 50, classID = 7 })
  clearBags()
  M.SetBagItem(0, 1, 2604, 5) M.SetBagItem(0, 2, pastID or 9010, 10) M.SetBagItem(0, 3, 2319, 20) M.SetBagItem(0, 4, 2319, 14)
  rawset(_G, "Auctionator", { API = { v1 = { GetAuctionPriceByItemID = function(_, id) return id == 2319 and 400 or 10 end } } })
  settle(2)
  out("métiers : " .. line(2604))
  check(line(2604) == "Pas besoin : sert en Couture (pas ton métier) → vends au marchand (2 pa 50 pc)", "not his profession, said exactly: " .. line(2604))
  check(B.Verdict(2604).action == "sell" and has(B.SellList().list, 2604), "…a sure sale (Questie rules the quests out)")
  if pastID then
    out("métiers : " .. line(pastID))
    check(line(pastID) == ("Pas besoin : sert en Travail du cuir mais tu l'as dépassé (recettes grises depuis %d) → vends au marchand (3 pa)"):format(pastAt),
      "already past it, said exactly: " .. line(pastID))
  end
  out("métiers : " .. line(2319))
  check(B.Verdict(2319).action == "keep" and line(2319):find("^Garde : sert à ton métier : Travail du cuir %(143→%d+%)$"),
    "useful and no plan known: everything kept, with the skill range (" .. line(2319) .. ")")
  -- the plan of PROF gives the count: « garde 20, 14 en trop »
  local planN = 20
  B.RegisterKeeper("Prof", function(id) if id == 2319 and planN then return planN, "ton plan de travail du cuir en utilise " .. planN end end)
  settle(1)
  local to = line(2319):match("%(143→(%d+)%)")
  out("métiers : " .. line(2319))
  check(line(2319) == ("Garde : 20 pour Travail du cuir (143→%s) · 14 en trop → HV ≈ 4 pa l'unité"):format(tostring(to)),
    "stock covered, the player's sentence: " .. line(2319))
  check(B.Verdict(2319, 0, 4).action == "ah" and B.Verdict(2319, 0, 3).action == "keep" and B.TipLine(2319, 0, 3) == "TreeAI · " .. line(2319),
    "…the 14-stack is the surplus lot, the 20-stack is kept, both tooltips say the same sentence")
  -- what he has at the bank counts (last reading made with the bank open)
  P.SetOverride({ bank = { [2319] = 20 } })
  B.Invalidate(nil, "bank")
  out("métiers : (20 à la banque) " .. line(2319))
  check(line(2319) == ("Garde : 20 pour Travail du cuir (143→%s), dont 20 à la banque · 34 en trop → HV ≈ 4 pa l'unité"):format(tostring(to)),
    "bank counted: " .. line(2319))
  P.SetOverride(nil)
  B.Invalidate(nil, "bank")
  -- useful to his profession but NOT on his plan (plan known, silent on it): said, and never in the one-click
  planN = nil
  local PLv = T.Prof and T.Prof.Level
  local gen0 = PLv and PLv.keepGen
  if PLv then PLv.keepGen = 1 end
  B.Invalidate(2319)
  out("métiers : " .. line(2319))
  local vP = B.Verdict(2319)
  check(vP.action == "keep" and vP.confirm and line(2319):find("mais pas sur ton chemin", 1, true) and line(2319):find("^À confirmer : Pas besoin : sert en Travail du cuir"),
    "not on his path: said, to confirm (" .. line(2319) .. ")")
  check(not has(B.SellList().list, 2319), "…never in the one-click list")
  if PLv then PLv.keepGen = gen0 end
  B.UnregisterKeeper("Prof")
  -- a friend of the group has the profession (shared by TreeAI): « donne »
  local Grp = T.modules.Group
  local oList = Grp and Grp.List
  if Grp then
    Grp.List = function() return { { name = "Ami-Un", st = { pr = "Couture 120 Enchantement 95" } }, { name = "Moi", me = true, st = { pr = "" } } } end
    M.group = { { name = "Ami-Un", class = "MAGE", level = 30, online = true } }
    M.FireEvent("GROUP_ROSTER_UPDATE")
    M.Advance(1.5, 0.1)
    B.Invalidate()
    out("métiers : (ami couturier) " .. line(2604))
    local vF = B.Verdict(2604)
    check(vF.action == "keep" and vF.hint == "give" and line(2604):find("^Donne à un ami du groupe : Ami%-Un peut s'en servir %(Couture 120%)"),
      "a friend's profession uses it: « donne » (" .. line(2604) .. ")")
    Grp.List = oList
    M.group = {}
    M.FireEvent("GROUP_ROSTER_UPDATE")
    M.Advance(1.5, 0.1)
  end
  -- consumables outside our tables (elixir, scroll): kept, saying what is not known; a locked box
  item(9041, "Élixir de test", { stack = 5, sellPrice = 60, classID = 0, subclassID = 2, minLevel = 20 })
  item(9042, "Parchemin de test", { stack = 5, sellPrice = 60, classID = 0, subclassID = 4, minLevel = 45 })
  item(4632, "Coffret orné de test", { stack = 1, sellPrice = 0, classID = 15 })
  item(9043, "Jeton sans prix de test", { stack = 1, sellPrice = 0, classID = 15 })
  M.SetBagItem(1, 1, 9041, 2) M.SetBagItem(1, 2, 9042, 2) M.SetBagItem(1, 3, 4632, 1) M.SetBagItem(1, 4, 9043, 1)
  settle(1)
  for _, id in ipairs({ 9041, 9042, 4632, 9043 }) do out("jamais vendus : " .. tostring(B.Verdict(id).name) .. " → " .. tostring(B.Text(B.Verdict(id)))) end
  check(B.Verdict(9041).action == "keep" and tostring(B.Verdict(9041).why):find("élixir utilisable", 1, true), "elixir: kept (" .. tostring(B.Verdict(9041).why) .. ")")
  check(B.Verdict(9042).action == "bank" and tostring(B.Verdict(9042).why):find("niveau 45", 1, true), "level-45 scroll at 30: bank")
  check(B.Verdict(4632).action == "keep" and tostring(B.Verdict(4632).why):find("coffre verrouillé", 1, true), "locked box: never sold")
  check(B.Verdict(9043).action == "keep" and tostring(B.Verdict(9043).why):find("pas de prix de vente", 1, true), "no sell price: kept, reason stated")

  -- ------------------------------------------------------------------ 13. safety nets at the merchant
  clearBags()
  item(9040, "Gemme chère de test", { stack = 1, sellPrice = 25000, classID = 15 })
  item(9044, "Bricole de test", { stack = 5, sellPrice = 30, classID = 15 })
  B.RegisterKeeper("test-surplus", function(id) if id == 9040 or id == 9044 then return 0, "test : ne sert à rien" end end)
  M.SetBagItem(0, 1, 9001, 5) M.SetBagItem(0, 2, 9044, 3) M.SetBagItem(0, 3, 9040, 1) M.SetBagItem(0, 4, 929, 5)
  M.inventory[1] = nil
  M.sold = 0
  M.SetMerchant(true)
  M.Advance(3, 0.05)
  local m13 = B.cardModel
  out("filets : carte = " .. tostring(m13 and m13.label) .. " | " .. tostring(m13 and m13.info))
  check(m13 and m13.n == 2 and m13.apartN == 1 and has(m13.apart, 9040) and tostring(m13.info):find("+ 1 à confirmer un par un", 1, true),
    "a 2 po 50 item is never in the one-click lot: listed apart")
  check(B.Verdict(9040).action == "sell", "…its verdict is sure (« sell »), only the click is separate")
  local card13 = B.GetCard("merchant")
  M.Hw(function() card13.btn:GetScript("OnClick")(card13.btn, "LeftButton") end)
  M.Advance(2, 0.05)
  check(M.sold == 2 and M.bags[0].slots[3] and M.bags[0].slots[3].id == 9040, "ONE click sold the lot (2 stacks), not the gem")
  settle(1.5)
  local m13b = B.cardModel
  out("filets : après = " .. tostring(m13b and m13b.label) .. " | " .. tostring(m13b and m13b.info))
  check(m13b and tostring(m13b.info):find("Vendu : 2 objets", 1, true) and tostring(m13b.info):find("Annuler (Rachat) : onglet « Rachat » du marchand", 1, true),
    "reminder while the merchant is open: what was sold, how to undo")
  check(m13b.sold and #m13b.sold == 2, "…the sold items are listed (" .. tostring(m13b.sold and #m13b.sold) .. ")")
  -- he buys one back (the stack returns, gold goes down): learned for good
  M.SetBagItem(0, 2, 9044, 3)
  M.player.money = M.player.money - 90
  settle(1.5)
  out("filets : (racheté) " .. tostring(B.Text(B.Verdict(9044))))
  check(B.MarkOf(9044) == "keep" and B.char.learned[9044] == "rachat" and tostring(B.Verdict(9044).why):find("racheté", 1, true),
    "bought back → never proposed again (" .. tostring(B.Verdict(9044).why) .. ")")
  check(B.MarkOf(9001) == nil, "…the grey he did not buy back is not marked")
  -- the gem: its own click on its own row
  B.CardExpand("merchant", true)
  local grow
  for _, r in ipairs(card13.rows) do if r:IsShown() and r.e and r.e.apart and r.e.id == 9040 then grow = r end end
  if check(grow, "the gem has its own row") then
    M.Hw(function() grow:GetScript("OnClick")(grow, "LeftButton") end)
    M.Advance(1.5, 0.05)
    check(M.sold == 3 and not M.bags[0].slots[3], "its own click sells it (and only it)")
  end
  -- « toujours vendre » always wins; if a need claims the item, it is said and the click is separate
  B.Mark(929, "sell")
  settle(1.5)
  out("filets : (toujours vendre) " .. line(929))
  local v929 = B.Verdict(929)
  check(v929.action == "keep" and v929.confirm and v929.confirm.action == "sell" and line(929):find("attention : ta meilleure potion de soins", 1, true),
    "« toujours vendre » on his best potions: proposed, with the warning, never in the lot (" .. line(929) .. ")")
  B.Mark(929, nil)
  B.Mark(9044, nil)
  check(B.char.learned[9044] == nil, "/tai oublie: the learned buy-back is forgotten too")
  B.UnregisterKeeper("test-surplus")
  M.SetMerchant(false)
  M.Advance(0.5, 0.1)

  -- ------------------------------------------------------------------ 14. verdicts invalidated BY CAUSE, cost per image
  clearBags()
  local fill = { 6948, 929, 858, 4540, 1179, 3530, 2672, 9003, 9004, 2901, 9005, 9006, 17031, 9007, 9001, 9002, 2515, 2519, 9008, 9009, 9010, 9011,
    9012, 9013, 2319, 2604, 9031, 9032, 9033, 9041, 9042, 4632 }
  for i, id in ipairs(fill) do M.SetBagItem(math.floor((i - 1) / 16), (i - 1) % 16 + 1, id, 1) end
  settle(3)
  B.Invalidate()
  for _, id in ipairs(fill) do B.Verdict(id) end
  local function redone(fn)
    local c0 = B.stats.computed
    fn()
    for _, id in ipairs(fill) do B.Verdict(id) end
    return B.stats.computed - c0
  end
  local nLevel = redone(function() M.player.level = 31 M.Advance(0.1, 0.1) end)
  local nPet = redone(function() B.Invalidate(nil, "pet") end)
  local nProf = redone(function() B.Invalidate(nil, "prof") end)
  local nRoster = redone(function() B.Invalidate(nil, "roster") end)
  local nNone = redone(function() M.Advance(120, 1) end)
  M.player.level = 30
  M.Advance(0.2, 0.1)
  out(("causes : %d objets — niveau %d, familier %d, métiers %d, groupe %d refaits ; 2 min sans rien : %d"):format(#fill, nLevel, nPet, nProf, nRoster, nNone))
  check(nLevel > 0 and nLevel < #fill and nPet > 0 and nPet <= 3 and nProf > 0 and nProf < #fill and nRoster < #fill,
    "a cause redoes only the verdicts that depend on it")
  check(nNone == 0, "no cause, no external keeper: a verdict is kept as long as nothing changes (no 45 s expiry)")
  -- cost of the worst cold verdict and of one slice of each background job (VM instructions, counted by a hook)
  local function instr(fn)
    local n = 0
    debug.sethook(function() n = n + 100 end, "", 100)
    fn()
    debug.sethook()
    return n
  end
  local worst = 0
  B.Invalidate()
  for _, id in ipairs(fill) do worst = math.max(worst, instr(function() B.Verdict(id) end)) end
  local chunk = 0
  B.Invalidate()
  for i = 1, #fill, 6 do
    chunk = math.max(chunk, instr(function() for k = i, math.min(#fill, i + 5) do B.Verdict(fill[k]) end end))
  end
  rawset(_G, "Auctionator", nil)
  out(("coût : pire avis à froid %.1f kinstr ; pire paquet de 6 avis (une image) %.1f kinstr (4 ms = 200 kinstr)"):format(worst / 1000, chunk / 1000))
  check(chunk < 60000, "one image of verdicts stays far under 4 ms (" .. chunk .. " instructions)")

  -- ------------------------------------------------------------------ 9. (5.0 INTEG) with the REAL keepers of GEAR and PROF
  -- the bridges step aside (one opinion per item, from the module that knows): an upgrade is « à utiliser », a piece
  -- he cannot wear is surplus alone and kept in a group until GEAR knows nobody wants it, a leather of HIS plan is kept
  clearBags()
  item(9021, "Collier d'agilité de test", { quality = 2, stack = 1, sellPrice = 900, classID = 4, subclassID = 0, equipLoc = "INVTYPE_NECK",
    minLevel = 20, stats = { ITEM_MOD_AGILITY_SHORT = 6 } })
  M.SetBagItem(0, 1, 6948, 1)
  M.SetBagItem(0, 2, 9007, 1)       -- plate chest: not for a hunter
  M.SetBagItem(0, 3, 9021, 1)       -- an upgrade (empty neck slot)
  M.SetBagItem(0, 4, 2319, 6)       -- medium leather (his leatherworking)
  M.group = {}
  M.FireEvent("GROUP_ROSTER_UPDATE")
  if Gm and Gm.RegisterKeeper then Gm.UnregisterKeeper() Gm.RegisterKeeper() end
  if PLm and PLm.RegisterKeeper then PLm.keeperOn = nil PLm.RegisterKeeper() end
  M.FireEvent("BAG_UPDATE", 0)
  bagsChanged()
  M.Advance(4, 0.1)
  B.Invalidate()
  local names = table.concat(B.Keepers(), ", ")
  check(names:find("Gear", 1, true) and names:find("Prof", 1, true), "real keepers back: " .. names)
  local _, v7 = show(9007, 0, 2)
  out("vrais gardiens : " .. show(9007, 0, 2))
  check(v7.action ~= "keep" and tostring(v7.why):find("pas pour toi", 1, true), "GEAR: plate chest alone = surplus, said by Gear (" .. tostring(v7.why) .. ")")
  local _, vb = show(9021, 0, 3)
  out("vrais gardiens : " .. show(9021, 0, 3))
  check((vb.action == "use" or vb.action == "keep") and vb.keeper == "Gear", "GEAR: the upgrade is claimed by Gear (" .. tostring(vb.action) .. " / " .. tostring(vb.why) .. ")")
  local _, vl = show(2319, 0, 4)
  out("vrais gardiens : " .. show(2319, 0, 4))
  check(vl.action ~= "sell", "PROF: the leather of his own profession is never a one-click vendor sale (" .. tostring(vl.action) .. " : " .. tostring(vl.why) .. ")")
  M.group = { { name = "Ami-Un", class = "WARRIOR", level = 30, online = true }, { name = "Ami-Deux", class = "MAGE", level = 30, online = true },
    { name = "Ami-Trois", class = "PRIEST", level = 29, online = true } }
  M.FireEvent("GROUP_ROSTER_UPDATE")
  M.Advance(1.5, 0.1)
  B.Invalidate()
  local _, vg = show(9007, 0, 2)
  out("vrais gardiens : (groupe de 4) " .. show(9007, 0, 2))
  check(vg.action == "keep", "GEAR in a group of 4: the tradable plate chest is kept (a friend may wear it): " .. tostring(vg.why))
  M.group = {}
  M.FireEvent("GROUP_ROSTER_UPDATE")
  M.Advance(1.5, 0.1)
  -- a switched-off module takes its keeper away: the bridge answers again
  if Gm and Gm.UnregisterKeeper then
    Gm.UnregisterKeeper()
    B.Invalidate()
    check(not table.concat(B.Keepers(), ","):find("Gear", 1, true) and B.Verdict(9007, 0, 2).action ~= "keep", "Gear off: its keeper leaves, the bridge decides again")
    Gm.RegisterKeeper()
  end

  -- (« [UIError] … » = the short answer to a click at the top of the screen, not the chat)
  local chat = 0
  for i = chat0 + 1, #M.output do
    if not tostring(M.output[i]):find("^%[UIError%]") then chat = chat + 1 out("chat: " .. tostring(M.output[i])) end
  end
  check(chat == 0, "nothing in the chat")
  check(blocked() == b0, "zero blocked action")
  for i = v0 + 1, #M.violations do out("violation: " .. tostring(M.violations[i].msg or M.violations[i][2] or "?")) end
  check(#M.violations == v0, "zero violation")
end
