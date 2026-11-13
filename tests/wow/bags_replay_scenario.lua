-- Harness scenario (5.1, agent BAGS) — THE REGRET REPLAY. A hunter levels from 30 to 45 on the faithful WoW: Forever
-- mock with the REAL keepers (Gear, Prof) and a QuestieDB of real schema. At every level he loots (items of quests he
-- has NOT taken yet, trade goods, potions of every tier, greys, ammunition, pet food, gear, recipes), takes the quests
-- he can, turns them in, gains leatherworking skill — and at every level visits a merchant and clicks ONCE on the
-- TreeAI card (the clean-up), and every third level visits the bank (« Ranger »). Everything sold is written down.
-- The ground truth is the TIMELINE of this file (what each quest asks and when, which potion tier becomes usable when,
-- what his profession plan consumes later) — not the predictors. Asserted: nothing sold is ever regretted:
--   quests ........ a quest he takes later never lacks an item he sold (need > what he has, and he sold some);
--   consumables ... he never sold a potion / food that is, later, better than everything he owns and usable;
--   professions ... the plan TreeAI shows him later never asks for a component he sold;
--   ammunition, pet food, usable gear, quest starters, items for quests beyond level 45: never sold at all.
-- Prints « REPLAY … » lines with the counts.
return function(M, ctx)
  local T = TreeAI
  local check = ctx.check
  local B = T.Bags
  local P = B and B.Predict
  local function out(s) io.write("REPLAY ", tostring(s), "\n") end
  if not check(B and B.Verdict and P, "Bags + predictors loaded") then return end
  local chat0, v0 = #M.output, #M.violations
  M.LeaveCombat()
  -- BAGS_MUTATION=veto|plan : the same replay with a predictor broken ON PURPOSE — it must then find regrets (else the
  -- zero of the real run would prove nothing)
  local MUT = os.getenv("BAGS_MUTATION")
  if MUT == "veto" then P.Veto = function() return nil end end
  if MUT == "tiers" then
    local I = B._int
    I.PRE["consommable"] = function() return false end
    B.RegisterKeeper("mutation", function(id) if B.CatOf(id) then return 0, "mutation : en trop" end end)
  end
  local Q = T.modules.QoL
  if Q and Q.db then Q.db.sellRepair = false end
  local K = T.modules.Quick
  if K and K.db then K.db.autoSell, K.db.autoRepair = false, false end

  -- ------------------------------------------------------------------ items
  local function item(id, name, t) t.name = name M.AddItem(id, t) return id end
  local GOOD = { classID = 7, stack = 20 }
  local function good(id, name, sell) return item(id, name, { classID = GOOD.classID, stack = GOOD.stack, sellPrice = sell or 30 }) end
  -- trade goods (some are components of his leatherworking, some of other professions, all can be asked by quests)
  local LEATHER = { good(2318, "Cuir léger", 15), good(2319, "Cuir moyen", 50), good(4234, "Cuir lourd", 150), good(4304, "Cuir épais", 300) }
  local CLOTH = { good(2589, "Étoffe de lin", 13), good(2592, "Étoffe de laine", 33), good(4306, "Étoffe de soie", 150), good(4338, "Étoffe de tisse-mage", 250) }
  local ORE = { good(2770, "Minerai de cuivre", 5), good(2772, "Minerai de fer", 150), good(3858, "Minerai de mithril", 250) }
  local MISC = { good(2321, "Fil fin", 25), good(4291, "Fil de soie", 125), good(2604, "Teinture rouge", 12), good(3371, "Fiole vide", 1),
    good(2453, "Sauvageonne", 25), good(3356, "Sang-royal", 30), good(5784, "Écaille de murloc fine", 40), good(4337, "Soie d'araignée épaisse", 750) }
  -- potions of every tier (real ids: Data/Consumables), food, pet meat, ammunition
  item(858, "Potion de soins inférieure", { stack = 5, sellPrice = 25, classID = 0, subclassID = 1, minLevel = 3 })
  item(929, "Potion de soins", { stack = 5, sellPrice = 75, classID = 0, subclassID = 1, minLevel = 12 })
  item(1710, "Potion de soins supérieure", { stack = 5, sellPrice = 125, classID = 0, subclassID = 1, minLevel = 21 })
  item(3928, "Potion de soins excellente", { stack = 5, sellPrice = 250, classID = 0, subclassID = 1, minLevel = 35 })
  item(13446, "Potion de soins majeure", { stack = 5, sellPrice = 500, classID = 0, subclassID = 1, minLevel = 45 })
  item(4541, "Pain de seigle frais", { stack = 20, sellPrice = 6, classID = 0, subclassID = 5, minLevel = 5 })
  item(4544, "Petit pain", { stack = 20, sellPrice = 50, classID = 0, subclassID = 5, minLevel = 25 })
  item(8950, "Pain de voyage maison", { stack = 20, sellPrice = 150, classID = 0, subclassID = 5, minLevel = 45 })
  item(3771, "Jarret de sanglier", { stack = 20, sellPrice = 50, classID = 0, subclassID = 5, minLevel = 25 })
  item(2504, "Arc court usé", { stack = 1, sellPrice = 50, classID = 2, subclassID = 2, equipLoc = "INVTYPE_RANGED" })
  item(3030, "Flèche rasoir", { stack = 200, sellPrice = 0, classID = 6, subclassID = 2, minLevel = 25 })
  item(11285, "Flèche dentelée", { stack = 200, sellPrice = 0, classID = 6, subclassID = 2, minLevel = 40 })
  item(3033, "Balle solide", { stack = 200, sellPrice = 0, classID = 6, subclassID = 3, minLevel = 25 })
  M.inventory[18] = { id = 2504, count = 1 }
  -- greys, another class's reagent, gear, recipes
  local GREY = { item(9101, "Dent ébréchée", { quality = 0, stack = 20, sellPrice = 12 }), item(9102, "Fourrure miteuse", { quality = 0, stack = 20, sellPrice = 33 }),
    item(9103, "Hache rouillée", { quality = 0, stack = 1, sellPrice = 410, classID = 2, subclassID = 0, equipLoc = "INVTYPE_WEAPON" }) }
  item(17031, "Rune de téléportation", { stack = 10, sellPrice = 250, classID = 5 })
  item(9110, "Plastron de plaques terni", { quality = 1, stack = 1, sellPrice = 300, classID = 4, subclassID = 4, equipLoc = "INVTYPE_CHEST" })
  item(9111, "Tunique de cuir du traqueur", { quality = 2, stack = 1, sellPrice = 900, classID = 4, subclassID = 2, equipLoc = "INVTYPE_CHEST", minLevel = 36,
    ilvl = 41, stats = { ITEM_MOD_AGILITY_SHORT = 12, ITEM_MOD_STAMINA_SHORT = 8 } })
  local R = T.Data.Recipes.r
  local function recipeOf(line, pick) for _, r in ipairs(R[line] or {}) do if (tonumber(r[8]) or 0) > 0 and pick(tonumber(r[4]) or 1) then return r[8] end end end
  local patLater = recipeOf(165, function(at) return at >= 190 and at <= 215 end)
  local patOther = recipeOf(197, function() return true end)
  if patLater then item(patLater, "Patron de test (plus tard)", { stack = 1, sellPrice = 100, classID = 9 }) end
  if patOther then item(patOther, "Patron de couture de test", { stack = 1, sellPrice = 100, classID = 9 }) end
  item(9120, "Lettre froissée de test", { stack = 1, sellPrice = 20, classID = 15 })
  item(9121, "Carte déchirée de test", { stack = 1, sellPrice = 20, classID = 15 })
  local QI = {}                    -- quest-only items (class 12)
  for i = 1, 8 do QI[i] = item(9130 + i, "Trophée de quête " .. i, { stack = 20, sellPrice = 0, classID = 12 }) end

  -- ------------------------------------------------------------------ the quests of the timeline (QuestieDB, real schema)
  -- { id, name, required level, quest level, item, how many, opts }
  local QL = {
    { 8001, "Du cuir pour les selles", 31, 33, 2319, 10 }, { 8002, "La laine des collines", 32, 34, 2592, 12 },
    { 8003, "Le fer du forgeron", 33, 35, 2772, 8 }, { 8004, "Remède de campagne", 34, 36, 929, 4 },
    { 8005, "Soie pour les bannières", 35, 37, 4306, 10 }, { 8006, "Des écailles fines", 36, 38, 5784, 6 },
    { 8007, "Les trophées du marais", 36, 38, QI[1], 8 }, { 8008, "Fil et aiguilles", 37, 39, 2321, 6 },
    { 8009, "Cuir lourd pour l'armée", 38, 40, 4234, 12 }, { 8010, "Sang-royal pour le sage", 39, 41, 3356, 5 },
    { 8011, "Le mithril du nain", 40, 42, 3858, 6 }, { 8012, "Soie d'araignée", 41, 43, 4337, 2 },
    { 8013, "Les trophées des pics", 41, 43, QI[2], 6 }, { 8014, "Tisse-mage pour l'érudit", 42, 44, 4338, 10 },
    { 8015, "Cuir épais du nord", 43, 45, 4304, 8 }, { 8016, "Des fioles pour l'apothicaire", 44, 46, 3371, 10 },
    { 8017, "Du lin pour les bandages", 30, 14, 2589, 20, { specialFlags = 1 } },                    -- repeatable turn-in, low level
    { 8018, "Petits pains pour la troupe", 38, 40, 4544, 6 },
    -- beyond the replay (he is 45 at the end): never sold before, still owned at the end
    { 8020, "Mithril pour la citadelle", 49, 51, 3858, 10 }, { 8021, "Potions pour le front", 52, 54, 3928, 5 },
    -- not for him: another class, the other faction, a profession he lacks, already done → their items may go
    { 8030, "Secrets d'arcanes", 31, 33, 2604, 6, { requiredClasses = 128 } }, { 8031, "Cuivre pour la Horde", 30, 32, 2770, 10, { requiredRaces = 178 } },
    { 8032, "Commande du tailleur", 32, 34, 4291, 4, { requiredSkill = { 197, 100 } } }, { 8033, "Sauvageonne du passé", 29, 31, 2453, 6, { done = true } },
  }
  local QDB = { quests = {}, items = {} }
  for _, q in ipairs(QL) do
    local row = { name = q[2], requiredLevel = q[3], questLevel = q[4], objectives = { nil, nil, { { q[5], nil } } }, requiredRaces = 0, requiredClasses = 0 }
    for k, v in pairs(q[7] or {}) do if k ~= "done" then row[k] = v end end
    QDB.quests[q[1]] = row
    QDB.items[q[5]] = QDB.items[q[5]] or { name = "objet " .. q[5], relatedQuests = {} }
    table.insert(QDB.items[q[5]].relatedQuests, q[1])
  end
  -- two items that START a quest
  QDB.quests[8040] = { name = "La lettre froissée", requiredLevel = 30, questLevel = 32, requiredRaces = 0, requiredClasses = 0 }
  QDB.quests[8041] = { name = "La carte déchirée", requiredLevel = 44, questLevel = 46, requiredRaces = 0, requiredClasses = 0 }
  QDB.items[9120] = { name = "Lettre froissée de test", startQuest = 8040 }
  QDB.items[9121] = { name = "Carte déchirée de test", startQuest = 8041 }
  M.InstallQuestieDB(QDB)
  -- the auction house is known (Auctionator): most goods are worth less there than at the vendor (→ vendor, one click),
  -- cloth and thick leather are worth much more (→ « HV » : he follows that advice too, see merchant())
  local AHP = { [2592] = 400, [4306] = 900, [4338] = 1500, [4304] = 1800, [2589] = 90 }
  rawset(_G, "Auctionator", { API = { v1 = { GetAuctionPriceByItemID = function(_, id)
    if AHP[id] then return AHP[id] end
    local it = M.items[id]
    if it and it.classID == 7 then return math.max(1, math.floor((it.sellPrice or 0) * 0.6)) end
    return nil
  end } } })
  local completed = { [8033] = true }
  M.SetCompleted(completed)
  P.ResetQuestie()

  -- ------------------------------------------------------------------ the character
  M.player.level = 30
  M.player.money = 500000
  M.skills = { { name = "Travail du cuir", rank = 143, max = 150 }, { name = "Dépeçage", rank = 150, max = 150 } }
  -- BAGS_PROFS=player : like the player, the secondary professions too (First Aid, Cooking — they level with him)
  local SECOND = os.getenv("BAGS_PROFS") == "player"
  if SECOND then
    M.skills[3] = { name = "Secourisme", rank = 150, max = 225 }
    M.skills[4] = { name = "Cuisine", rank = 120, max = 150 }
  end
  M.quests = {}
  for b = 0, 4 do M.bags[b].size = 28 M.bags[b].slots = {} end
  M.AddToBags(6948, 1)
  M.AddToBags(3030, 600)
  M.AddToBags(929, 5)
  M.AddToBags(4541, 20)
  local Pm = T.modules.Prof
  if Pm and Pm.Update then Pm.Update(true) end
  M.FireEvent("SKILL_LINES_CHANGED")
  M.FireEvent("QUEST_LOG_UPDATE")
  M.FireEvent("BAG_UPDATE_DELAYED")
  M.Advance(15, 0.1)
  check(#B.Keepers() >= 2, "the real keepers (Gear, Prof) are registered: " .. table.concat(B.Keepers(), ", "))
  local function count(id) local n = 0 for b = 0, 4 do for _, s in pairs(M.bags[b].slots) do if s.id == id then n = n + s.count end end end return n end
  local function bankCount(id)
    local n = 0
    for _, bag in pairs(M.bank or {}) do for _, s in pairs(bag.slots or {}) do if s.id == id then n = n + s.count end end end
    return n
  end
  local function owned(id) return count(id) + bankCount(id) end
  local function bagsChanged(sec) M.FireEvent("BAG_UPDATE", 0) M.FireEvent("BAG_UPDATE_DELAYED") M.Advance(sec or 1.5, 0.1) end
  -- bank deposits (the mock sells / uses a bag stack; a real bank takes it): the same shim as bags_scenario §5
  local CC = rawget(_G, "C_Container")
  local useReal = CC and CC.UseContainerItem or rawget(_G, "UseContainerItem")
  local bankBag = M.BankBagIDs()[1]
  local function useWrap(bag, slot)
    local s = M.bags[bag] and M.bags[bag].slots[slot]
    if s and M.bankOpen and not M.merchantOpen then
      if not M.hardware then M.Violation("automation", "deposit outside a click") end
      M.bank[bankBag] = M.bank[bankBag] or { size = 98, slots = {} }
      M.bank[bankBag].size = 98
      for k = 1, 98 do if not M.bank[bankBag].slots[k] then M.bank[bankBag].slots[k] = { id = s.id, count = s.count } break end end
      M.bags[bag].slots[slot] = nil
      return
    end
    return useReal(bag, slot)
  end
  if CC and CC.UseContainerItem then CC.UseContainerItem = useWrap else rawset(_G, "UseContainerItem", useWrap) end

  -- ------------------------------------------------------------------ the ledger
  local sold = {}                  -- id → { n, level, line }
  local soldStacks, soldCopper, confirmSeen = 0, 0, {}
  local regrets = {}
  local function regret(kind, id, detail)
    regrets[#regrets + 1] = ("%s : %s — %s (vendu au niveau %d : « %s »)"):format(kind, tostring(M.items[id] and M.items[id].name or id), detail,
      sold[id] and sold[id].level or -1, sold[id] and sold[id].line or "?")
  end
  local NEVER = { [3030] = "munitions", [11285] = "munitions", [9120] = "lance une quête", [9121] = "lance une quête", [9111] = "équipement portable plus tard",
    [6948] = "pierre de foyer", [3858] = "quête au-delà du niveau 45", [3928] = "quête au-delà du niveau 45" }
  if patLater then NEVER[patLater] = "recette à apprendre plus tard" end
  for _, id in ipairs(QI) do NEVER[id] = "objet de quête" end
  if SECOND then
    -- silk (heavy silk bandage, his First Aid plan) and mageweave (mageweave bandages: after his plan, from 210)
    NEVER[4306], NEVER[4338] = "Secourisme : son plan", "Secourisme : après son plan (dès 210)"
  end
  local HP = { { 858, 3, 160 }, { 929, 12, 320 }, { 1710, 21, 520 }, { 3928, 35, 800 }, { 13446, 45, 1400 } }   -- (Data/Consumables, read above)
  local FOOD = { { 4541, 5, 243 }, { 4544, 25, 874 }, { 3771, 25, 874 }, { 8950, 45, 1933 } }
  local function tierRegrets(kind, tiers, L)
    local best = 0
    for _, t in ipairs(tiers) do if t[2] <= L and owned(t[1]) > 0 then best = math.max(best, t[3]) end end
    for _, t in ipairs(tiers) do
      if sold[t[1]] and t[2] <= L and t[3] > best then regret(kind, t[1], ("au niveau %d c'est mieux que tout ce qu'il a"):format(L)) end
    end
  end
  local PLv = T.Prof and T.Prof.Level
  local function profRegrets(L)
    if not (PLv and PLv.KeepFor) then return 0 end
    local asked = 0
    for id, e in pairs(sold) do
      local ok, n = pcall(PLv.KeepFor, id)
      n = ok and tonumber(n) or 0
      if n > 0 and n < math.huge then
        asked = asked + 1
        if n > owned(id) then regret("métier", id, ("au niveau %d son plan en demande %d, il en a %d"):format(L, n, owned(id))) end
      end
      local _ = e
    end
    return asked
  end

  -- ------------------------------------------------------------------ one level of play
  local function loot(L)
    local add = M.AddToBags
    -- what the quests to come will ask, BEFORE he can take them (the dangerous loot)
    for _, q in ipairs(QL) do
      if L >= q[3] - 6 and L <= q[3] + 1 and not completed[q[1]] then add(q[5], math.ceil(q[6] / 3)) end
    end
    add(GREY[1], 4) add(GREY[2], 3)
    if L % 3 == 0 then add(GREY[3], 1) end
    add(LEATHER[L < 34 and 2 or (L < 40 and 3 or 4)], 6)
    add(LEATHER[1], 2)
    add(CLOTH[L < 33 and 2 or (L < 40 and 3 or 4)], 5)
    add(CLOTH[1], 2)
    add(ORE[1 + L % 3], 2)
    for i, id in ipairs(MISC) do if (L + i) % 4 == 0 then add(id, 2) end end
    add(858, 1)
    if L % 2 == 0 then add(929, 2) end
    if L >= 31 then add(1710, 1) end
    if L >= 32 and L % 2 == 0 then add(3928, 1) end              -- usable at 35: looted from 32
    if L >= 40 and L % 2 == 0 then add(13446, 1) end             -- usable at 45: looted from 40
    add(4544, 4)
    if L >= 41 then add(8950, 2) end
    add(3771, 3)
    add(2672, 3)
    if L % 4 == 0 then add(3033, 200) add(17031, 2) add(9110, 1) end
    if L == 33 then add(9111, 1) add(9120, 1) if patLater then add(patLater, 1) end if patOther then add(patOther, 1) end end
    if L == 37 then add(9121, 1) add(11285, 200) end
    -- he plays: drinks 2 of his best usable potion, eats, shoots
    for i = #HP, 1, -1 do if HP[i][2] <= L and count(HP[i][1]) > 0 then M.RemoveFromBags(HP[i][1], math.min(2, count(HP[i][1]))) break end end
    M.RemoveFromBags(3030, math.min(150, math.max(0, count(3030) - 200)))
  end
  local function eligible(q, L)
    local o = q[7] or {}
    if o.done or o.requiredClasses or o.requiredRaces or o.requiredSkill then return false end
    return q[3] <= L and q[3] <= 45
  end
  local inLog, taken = {}, 0
  local function questStep(L)
    -- turn in what is complete
    for i = #M.quests, 1, -1 do
      local e = M.quests[i]
      local q = inLog[e.id]
      if q and count(q[5]) >= q[6] then
        M.RemoveFromBags(q[5], q[6])
        if not (q[7] and q[7].specialFlags) then completed[q[1]] = true end
        inLog[e.id] = nil
        table.remove(M.quests, i)
      end
    end
    -- take the quests he can: THE moment of truth for what was sold before
    for _, q in ipairs(QL) do
      if eligible(q, L) and not completed[q[1]] and not inLog[q[1]] and not q.took then
        q.took = true
        taken = taken + 1
        inLog[q[1]] = q
        local have = count(q[5])
        M.quests[#M.quests + 1] = { id = q[1], title = q[2], level = q[4],
          objectives = { { text = ("%s : %d/%d"):format(M.items[q[5]].name, math.min(have, q[6]), q[6]), done = have >= q[6], have = math.min(have, q[6]), need = q[6], type = "item" } } }
        if sold[q[5]] and owned(q[5]) < q[6] then
          regret("quête", q[5], ("« %s » en demande %d, il en a %d"):format(q[2], q[6], owned(q[5])))
        end
      end
    end
    for _, e in ipairs(M.quests) do
      local q = inLog[e.id]
      local have = math.min(count(q[5]), q[6])
      e.objectives[1].have, e.objectives[1].done = have, have >= q[6]
      e.objectives[1].text = ("%s : %d/%d"):format(M.items[q[5]].name, have, q[6])
    end
    M.SetCompleted(completed)
    M.FireEvent("QUEST_LOG_UPDATE")
  end
  local visits, clicks, bankMoves, ahStacks = 0, 0, 0, 0
  local function merchant(L)
    visits = visits + 1
    M.units.npc = { name = "Marchand-Test" }
    M.merchantItems = { { id = 3030, price = 10, stack = 200, maxStack = 200 } }
    M.SetMerchant(true)
    M.Advance(4, 0.05)
    local card = B.GetCard("merchant")
    for _ = 1, 4 do
      local m = B.cardModel
      if not (card and card:IsShown() and m and m.n > 0) then break end
      for _, r in ipairs(m.apart or {}) do confirmSeen[r.id] = confirmSeen[r.id] or r end
      local before = {}
      for b = 0, 4 do for _, s in pairs(M.bags[b].slots) do before[s.id] = (before[s.id] or 0) + s.count end end
      local lines = {}
      for _, r in ipairs(m.rows) do if not (r.apart or r.sold or r.verify) then lines[r.id] = B.Line(r.v) end end
      local money0, sold0 = M.player.money, M.sold or 0
      M.Hw(function() card.btn:GetScript("OnClick")(card.btn, "LeftButton") end)
      clicks = clicks + 1
      M.Advance(3, 0.05)
      for id, n in pairs(before) do
        local gone = n - count(id)
        if gone > 0 then
          sold[id] = sold[id] or { n = 0, level = L, line = lines[id] or "?" }
          sold[id].n = sold[id].n + gone
          if NEVER[id] then regret("jamais vendable (" .. NEVER[id] .. ")", id, "vendu") end
        end
      end
      soldStacks, soldCopper = soldStacks + ((M.sold or 0) - sold0), soldCopper + (M.player.money - money0)
      bagsChanged(1.5)
    end
    local m = B.cardModel
    for _, r in ipairs(m and m.apart or {}) do confirmSeen[r.id] = confirmSeen[r.id] or r end
    -- he follows the « HV » advice too (price known and clearly better): those stacks leave his bags as well
    for _, e in ipairs(B.Lists().ah) do
      if not e.v.ahUnknown then
        local s = M.bags[e.bag].slots[e.slot]
        if s and s.id == e.id then
          sold[e.id] = sold[e.id] or { n = 0, level = L, line = B.Line(e.v) }
          sold[e.id].n = sold[e.id].n + s.count
          ahStacks = ahStacks + 1
          M.bags[e.bag].slots[e.slot] = nil
          if NEVER[e.id] then regret("jamais vendable (" .. NEVER[e.id] .. ")", e.id, "mis à l'HV") end
        end
      end
    end
    bagsChanged(1)
    M.SetMerchant(false)
    M.Advance(1, 0.1)
  end
  local function bank()
    M.SetBank(true)
    M.Advance(3, 0.1)
    local bcard = B.GetCard("bank")
    for _ = 1, 6 do
      if not (bcard and bcard:IsShown()) then break end
      M.Hw(function() bcard.btn:GetScript("OnClick")(bcard.btn, "LeftButton") end)
      bankMoves = bankMoves + 1
      M.FireEvent("BAG_UPDATE", 0) M.FireEvent("PLAYERBANKSLOTS_CHANGED", 1) M.FireEvent("BAG_UPDATE_DELAYED")
      M.Advance(2, 0.1)
    end
    M.SetBank(false)
    M.Advance(1, 0.1)
  end

  -- ------------------------------------------------------------------ 30 → 45
  local profAsked = 0
  for L = 30, 45 do
    M.player.level = L
    if L > 30 then M.FireEvent("PLAYER_LEVEL_UP", L) end
    -- his leatherworking follows: +9 per level, Expert (cap 225) trained at 35
    local cap = L >= 35 and 225 or 150
    M.skills[1].max = cap
    M.skills[1].rank = math.min(cap, 143 + (L - 30) * 9)
    M.skills[2].max, M.skills[2].rank = (L >= 35) and 225 or 150, math.min((L >= 35) and 225 or 150, 150 + (L - 30) * 5)
    if SECOND then
      M.skills[3].rank = math.min(225, 150 + (L - 30) * 4)
      M.skills[4].max = (L >= 35) and 225 or 150
      M.skills[4].rank = math.min(M.skills[4].max, 120 + (L - 30) * 4)
    end
    M.FireEvent("SKILL_LINES_CHANGED")
    if Pm and Pm.Update then Pm.Update(true) end
    loot(L)
    bagsChanged(2)
    questStep(L)
    M.Advance(8, 0.1)                -- (the profession keeper rebuilds its plan: 0.5 s + slices)
    tierRegrets("potion", HP, L)
    tierRegrets("nourriture", FOOD, L)
    profAsked = profAsked + profRegrets(L)
    merchant(L)
    if L % 3 == 0 then bank() end
  end
  -- the end: what the quests beyond 45 will ask is still his
  for id, whyNever in pairs(NEVER) do
    if sold[id] then regret("jamais vendable (" .. whyNever .. ")", id, "vendu") end
  end
  if not MUT then
    check(owned(3858) >= 10 and not sold[3858] and not sold[3928], "items of the quests beyond level 45 were never sold (mithril owned: " .. owned(3858) .. ")")
    check(owned(9111) == 1 or M.inventory[5] ~= nil, "the leather chest usable at 36 was kept")
  end
  local kinds, names = 0, {}
  for id, e in pairs(sold) do kinds = kinds + 1 names[#names + 1] = ("%s ×%d"):format(tostring(M.items[id] and M.items[id].name or id), e.n) end
  table.sort(names)
  out(("niveaux 30 → 45 : %d visites chez le marchand, %d clics, %d piles vendues au marchand pour %s + %d piles mises à l'HV (%d objets différents) ; %d dépôts / retraits à la banque ; %d quêtes prises")
    :format(visits, clicks, soldStacks, B.Money(soldCopper), ahStacks, kinds, bankMoves, taken))
  out("vendu : " .. table.concat(names, ", "))
  for id, e in pairs(sold) do out(("  %s — « %s » (niveau %d)"):format(tostring(M.items[id] and M.items[id].name or id), tostring(e.line), e.level)) end
  local cs = {}
  for id, r in pairs(confirmSeen) do cs[#cs + 1] = ("%s (%s)"):format(tostring(r.name), tostring(r.v and r.v.unsure or (r.big and "valeur élevée") or "?")) local _ = id end
  table.sort(cs)
  out(("listés « à confirmer » (jamais vendus par le clic) : %d — %s"):format(#cs, table.concat(cs, " ; ")))
  out(("contrôles métier : %d fois un objet vendu était redemandé par un plan plus tard"):format(profAsked))
  out(("REGRETS : %d"):format(#regrets))
  for i, r in ipairs(regrets) do if i <= 20 then out("  regret : " .. r) end end
  if MUT then
    out(("MUTATION %s : %d regret(s) trouvés"):format(MUT, #regrets))
    check(#regrets > 0, "a broken predictor (" .. MUT .. ") is caught by the replay")
    B.UnregisterKeeper("mutation")
    if CC and CC.UseContainerItem then CC.UseContainerItem = useReal else rawset(_G, "UseContainerItem", useReal) end
    rawset(_G, "Auctionator", nil)
    return
  end
  check(soldStacks >= 100 and kinds >= (SECOND and 15 or 20), "the clean-up really sold things (" .. soldStacks .. " stacks, " .. kinds .. " different items)")
  check(taken >= 16, "the timeline really took its quests (" .. taken .. ")")
  check(#regrets == 0, ("regret replay: %d sold item(s) needed later"):format(#regrets))
  if CC and CC.UseContainerItem then CC.UseContainerItem = useReal else rawset(_G, "UseContainerItem", useReal) end
  rawset(_G, "Auctionator", nil)
  local chat = 0
  for i = chat0 + 1, #M.output do if not tostring(M.output[i]):find("^%[UIError%]") then chat = chat + 1 out("chat: " .. tostring(M.output[i])) end end
  check(chat == 0, "nothing in the chat")
  for i = v0 + 1, #M.violations do out("violation: " .. tostring(M.violations[i].msg or M.violations[i][2] or "?")) end
  check(#M.violations == v0, "zero violation")
end
