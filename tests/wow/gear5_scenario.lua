-- gear5_scenario.lua — GEAR 5.0 « un vrai assistant pour l'armure » on the real addon (fake names only).
-- Run by tests/wow/test_gear5.py for HUNTER / WARRIOR / MAGE / PRIEST at 30 and 60, forever + modern (+ legacy).
--  A. spec detection paths: game API (Classic tabs, Forever C_Traits groups, C_Traits nodes), talent spells known,
--     asked once then chosen by the player; « Spé : X (source) » always shown; Talents reads Forever ranks.
--  B. value model sanity: a known better bow wins, slow bow wins at equal DPS, melee weapon = stat stick, mail at 40,
--     hit cap, unknown stats = « inconnu » (never a score), warrior / mage / priest units.
--  C. « stuff rentable » with mocked vendor / AH / quest data: Équipe / Achète / Fais la quête / Attends / Garde,
--     budget and mount reserve, T.Gear.BestNow().
--  D. live help: tooltip line (cached, same table on repeat), quest reward pick, keeper for BAGS, bag arrows, page.
--  E. no chat, no blocked action, nothing computed on a plain bag change.
-- TREEAI_GEAR5_DUMP=FILE appends « what the player sees » (the read-it-critically step of ROUND 11).
return function(M, ctx)
  local T = TreeAI
  local Gr = T and T.modules and T.modules.Gear
  local check = ctx.check
  check(Gr and Gr._active, "Gear on (safe mode: on by default in 5.0)")
  check(T.SAFE_MODULES and T.SAFE_MODULES.Gear, "Gear is a safe-mode module")
  check(Gr and Gr.Spec and Gr.Score and Gr.BestNow and Gr.Value, "contract: T.Gear.Spec / Score / BestNow")
  check(T.Data and T.Data.TalentSpells and T.Data.TalentSpells.HUNTER, "Data/TalentSpells.lua loaded")
  if not (Gr and Gr.Spec and Gr.Value) then return end
  local V = Gr.Value
  local cls = T:PlayerClass()
  local lvl = M.player.level
  M.LeaveCombat()
  local out0, blocked0 = #M.output, #M.blocked
  local dump = {}
  local function say(...) local t = {} for i = 1, select("#", ...) do t[#t + 1] = tostring((select(i, ...))) end dump[#dump + 1] = table.concat(t, " ") end
  say(("===== %s niveau %d (%s) ====="):format(cls, lvl, tostring(M.flavor)))
  local function settle(n) for _ = 1, (n or 6) do M.Advance(0.5) end end

  -------------------------------------------------------------------------------------------------
  -- A. spec
  -------------------------------------------------------------------------------------------------
  local saved = {}
  local SAVED = { "GetTalentTabInfo", "GetNumTalentTabs", "C_Traits", "C_ClassTalents", "GetSpecialization", "GetNumSpecializations",
    "GetQuestReward", "C_TooltipInfo" }
  for _, n in ipairs(SAVED) do saved[n] = rawget(_G, n) end
  local SI0 = rawget(_G, "C_SpecializationInfo")
  local siGet = type(SI0) == "table" and SI0.GetSpecialization or nil
  local function clearTalentApis()
    rawset(_G, "GetTalentTabInfo", nil) rawset(_G, "GetNumTalentTabs", nil) rawset(_G, "C_Traits", nil)
    rawset(_G, "C_ClassTalents", { HasUnspentTalentPoints = function() return false end })
    rawset(_G, "GetSpecialization", nil) rawset(_G, "GetNumSpecializations", function() return 1 end)
    local SI = rawget(_G, "C_SpecializationInfo")
    if type(SI) == "table" then SI.GetSpecialization = nil end
  end
  local knownSpells = {}
  local function forget() for id in pairs(knownSpells) do if M.spells[id] then M.spells[id].known = false end end knownSpells = {} end
  local function learn(id, name) M.spells[id] = M.spells[id] or { name = name or ("Sort " .. id), rank = "", icon = 1 } M.spells[id].known = true knownSpells[id] = true end
  local dataTrees = T.Data.Talents.trees[cls]
  local spellRows = T.Data.TalentSpells[cls]
  local function talentIds(tab, n)
    local i = 0
    for grp in spellRows[tab]:gmatch("[^;]+") do
      i = i + 1
      if i == n then local ids = {} for id in grp:gmatch("%d+") do ids[#ids + 1] = tonumber(id) end return ids end
    end
  end
  local function reread() Gr.SpecDirty(true) return Gr.Spec() end
  Gr.SetSpec(nil)
  Gr.char.specSeen, Gr.char.specAsked = nil, nil

  -- A1. Classic Era API: points per tab
  clearTalentApis()
  rawset(_G, "GetNumTalentTabs", function() return 3 end)
  rawset(_G, "GetTalentTabInfo", function(tab) return dataTrees[tab].name, 136000 + tab, ({ 3, 18, 0 })[tab], "bg" end)
  local s = reread()
  check(s.tree == 2 and s.source == "api" and s.conf == 1 and s.points[2] == 18, "A1 Classic tabs → tree 2 from the game API: " .. Gr.SpecText(s))
  check(Gr.SpecText(s):find("lue dans tes talents", 1, true), "A1 source shown: " .. Gr.SpecText(s))
  say("A1", Gr.SpecText(s))

  -- A2. WoW Forever: C_Traits groups (what the game's own talent window reads), shuffled display order
  clearTalentApis()
  local pts = { 0, 5, 16 }
  local function traits(withGroups, ranks)
    local TR = {}
    TR.GetConfigInfo = function(cfg) if cfg == 777 then return { ID = 777, name = "x", treeIDs = { 42 } } end end
    if withGroups then
      TR.GetGroupDisplayInfoByTreeID = function(tree)
        if tree ~= 42 then return {} end
        return { { groupID = 30, treeID = 42, skillLineID = 0, orderIndex = 3, displayName = dataTrees[3].name, icon = 1 },
          { groupID = 10, treeID = 42, skillLineID = 0, orderIndex = 1, displayName = dataTrees[1].name, icon = 1 },
          { groupID = 20, treeID = 42, skillLineID = 0, orderIndex = 2, displayName = dataTrees[2].name, icon = 1 } }
      end
      TR.GetGroupCurrencyInfo = function(cfg, ids)
        local o = {}
        for _, id in ipairs(ids) do
          o[#o + 1] = { traitNodeGroupID = id, currencyInfos = { { traitCurrencyID = 9, quantity = 0, spent = pts[id / 10], spentInTree = pts[id / 10] } } }
        end
        return o
      end
    end
    local nodes, spellOf = {}, {}
    for tab = 1, 3 do
      for n = 1, #dataTrees[tab].t do
        local id = tab * 100 + n
        nodes[#nodes + 1] = id
        spellOf[id] = talentIds(tab, n)[1]
      end
    end
    TR.GetTreeNodes = function(tree) return tree == 42 and nodes or {} end
    TR.GetNodeInfo = function(cfg, nodeID)
      local r = ranks and ranks[nodeID] or 0
      return { ID = nodeID, posX = 0, posY = 0, flags = 0, ranksPurchased = r, activeRank = r, currentRank = r, maxRanks = 5,
        entryIDs = { nodeID + 10000 }, activeEntry = r > 0 and { entryID = nodeID + 10000, rank = r } or nil }
    end
    TR.GetEntryInfo = function(cfg, entry) return { definitionID = entry + 10000, type = 1 } end
    TR.GetDefinitionInfo = function(def) return { spellID = spellOf[def - 20000] } end
    rawset(_G, "C_Traits", TR)
    rawset(_G, "C_ClassTalents", { GetActiveConfigID = function() return 777 end, HasUnspentTalentPoints = function() return false end })
  end
  traits(true)
  s = reread()
  check(s.tree == 3 and s.source == "api" and s.points[3] == 16 and s.points[2] == 5,
    "A2 Forever C_Traits groups → tree 3 (matched by name, not by display order): " .. Gr.SpecText(s))
  check(s.name == dataTrees[3].name, "A2 the tree name is the game's own")
  say("A2", Gr.SpecText(s))

  -- A3. C_Traits nodes (no group API): per-talent ranks through the definition spell → Talents reads the tree on Forever
  clearTalentApis()
  local ranks = { [101] = 5, [102] = 3, [201] = 2 }
  traits(false, ranks)
  s = reread()
  settle(4)
  s = Gr.Spec()
  check(s.tree == 1 and s.source == "api" and s.points and s.points[1] == 8 and s.points[2] == 2,
    "A3 C_Traits nodes → tree 1 with 8/2/0: " .. Gr.SpecText(s))
  local tr = Gr.TalentRanks()
  local row1 = dataTrees[1].t[1]
  check(tr and tr.ok and tr.ranks["1:" .. row1[1] .. ":" .. row1[2]] == 5, "A3 rank of each talent readable (tier / column of the data)")
  local TL = T.modules.Talents
  if TL and TL.Read then
    local r = TL.Read()
    check(r.ok and r.points[1] == 8 and r.spent == 10, "A3 Talents.Read: tree read on Forever (root cause of « ne peut pas lire tes talents »)")
  end
  say("A3", Gr.SpecText(s))

  -- A4. no talent API at all: talent spells known (all ranks probed, highest rank = points)
  clearTalentApis()
  forget()
  local deep = cls == "HUNTER" and { 2, 5 } or { 2, 3 }   -- hunter: Précision, talent n° 5 = Visée
  local ids4 = talentIds(deep[1], deep[2])
  learn(ids4[#ids4], dataTrees[deep[1]].t[deep[2]][4])
  local ids4b = talentIds(deep[1], 1)
  learn(ids4b[math.min(3, #ids4b)], dataTrees[deep[1]].t[1][4])
  s = reread()
  check(s.pending or s.stale or s.source == "spells", "A4 the long read is not done in the caller's frame")
  settle(4)
  s = Gr.Spec()
  check(s.tree == deep[1] and s.source == "spells" and s.conf > 0 and s.conf < 1, "A4 talent spells known → tree " .. deep[1] .. ": " .. Gr.SpecText(s))
  check(Gr.SpecText(s):find("déduite de tes sorts", 1, true), "A4 source shown")
  say("A4", Gr.SpecText(s))
  if cls == "HUNTER" then
    for _, case in ipairs({ { 19577, "Intimidation", 1 }, { 19263, "Dissuasion", 3 }, { 19434, "Visée", 2 } }) do
      forget()
      learn(case[1], case[2])
      reread() settle(4)
      s = Gr.Spec()
      check(s.tree == case[3] and s.source == "spells", ("A4 hunter: %s → %s"):format(case[2], Gr.SpecText(s)))
    end
  end

  -- A5. nothing readable: asked ONCE (one line, click = the page with 3 buttons), then the player's choice
  clearTalentApis()
  forget()
  Gr.char.specSeen, Gr.char.specAsked = nil, nil
  T._suggestions["Gear:spec"] = nil
  s = reread() settle(4)
  s = Gr.Spec()
  if lvl >= 10 then
    check(s.tree == nil and s.ask, "A5 spec unknown → ask")
    Gr.AskSpecOnce()
    local q = T._suggestions["Gear:spec"]
    check(q and q.text:find("Quelle est ta spé", 1, true) and q.onClick, "A5 asked once, clickable: " .. tostring(q and q.text))
    T._suggestions["Gear:spec"] = nil
    check(Gr.AskSpecOnce() == false and not T._suggestions["Gear:spec"], "A5 never asked twice")
    say("A5 demande", q and q.text)
    say("A5", Gr.SpecText(s))
  end
  Gr.SetSpec(3)
  s = Gr.Spec()
  check(s.tree == 3 and s.source == "manual" and Gr.SpecText(s):find("choisie par toi", 1, true), "A5 manual choice: " .. Gr.SpecText(s))
  say("A5 choix", Gr.SpecText(s))
  Gr.SetSpec(nil)
  check(Gr.Spec().tree == nil, "A5 back to automatic (nothing readable here → unknown again)")
  local probe = table.concat(Gr.SpecProbe(), " | ")
  check(probe:find("classe=" .. cls, 1, true) and probe:find("résultat", 1, true), "probe for the error report: " .. probe)
  -- the spec used for the rest: hunter Précision (Visée), warrior Armes, mage Givre, priest Sacré — chosen by the player
  local useTree = ({ HUNTER = 2, WARRIOR = 1, MAGE = 3, PRIEST = 2 })[cls] or 1
  Gr.SetSpec(useTree)

  -------------------------------------------------------------------------------------------------
  -- B. value model
  -------------------------------------------------------------------------------------------------
  local realItems, realCrafted = T.Data.Gear.items, T.Data.Gear.crafted
  T.Data.Gear.items, T.Data.Gear.crafted = {}, {}
  local function item(id, name, loc, classID, sub, stats, extra)
    local t = { name = name, classID = classID, subclassID = sub, equipLoc = loc, quality = 2, stats = stats, sellPrice = 100 }
    for k, v in pairs(extra or {}) do t[k] = v end
    M.AddItem(id, t)
    return M.ItemLink(id)
  end
  local function wear(slot, id) M.inventory[slot] = id and { id = id, count = 1 } or nil end
  local function equipEvent() ctx.fire("PLAYER_EQUIPMENT_CHANGED", 1) end
  M.inventory = {}
  M.bags = {}
  local AGI, STR, STA, INT, SPI = "ITEM_MOD_AGILITY_SHORT", "ITEM_MOD_STRENGTH_SHORT", "ITEM_MOD_STAMINA_SHORT", "ITEM_MOD_INTELLECT_SHORT", "ITEM_MOD_SPIRIT_SHORT"
  local DPS, HIT, CRIT, SP, HEAL = "ITEM_MOD_DAMAGE_PER_SECOND_SHORT", "ITEM_MOD_HIT_RATING_SHORT", "ITEM_MOD_CRIT_RATING_SHORT",
    "ITEM_MOD_SPELL_POWER_SHORT", "ITEM_MOD_SPELL_HEALING_DONE_SHORT"
  local w
  if cls == "HUNTER" then
    -- speeds come from the client tables (Data/Gear.lua), like in game
    T.Data.Gear.items[980001] = { 15, 20, 2, 2, 2, "", 20, 2.0, 0, 2, 0, "Arc-test rapide", 0 }
    T.Data.Gear.items[980002] = { 15, 20, 2, 2, 2, "", 20, 3.0, 0, 2, 0, "Arc-test lent", 0 }
    T.Data.Gear.items[980003] = { 15, 20, 2, 2, 2, "", 24, 2.8, 0, 2, 0, "Arc-test meilleur", 0 }
    local fast = item(980001, "Arc-test rapide", "INVTYPE_RANGED", 2, 2, { [DPS] = 20 })
    local slow = item(980002, "Arc-test lent", "INVTYPE_RANGED", 2, 2, { [DPS] = 20 })
    local better = item(980003, "Arc-test meilleur", "INVTYPE_RANGED", 2, 2, { [DPS] = 24 })
    wear(18, 980001)
    equipEvent()
    w = V.Weights()
    check(w.unit == "dps" and w.kind == "ranged" and w.RDPS == 1 and w.MDPS == 0, "B hunter: ranged DPS model, melee weapon = stat stick")
    check(math.abs(w[AGI] - (2 / 14 + (w.base / 100) / (53 * math.max(10, math.min(60, lvl)) / 60))) < 1e-6, "B hunter: 1 Agi = 2 RAP / 14 + crit share")
    local sb, ss = Gr.Score(better), Gr.Score(slow)
    check(sb.kind == "good" and sb.dps > 4 and sb.text:find("^≈ %+%d+,%d DPS"), "B a known better bow wins, in DPS: " .. tostring(sb.text))
    if lvl >= 18 then
      check(ss.kind == "good" and ss.dps > 0 and ss.dps < sb.dps, "B same DPS, slower bow = more (Flèches multiples / Visée): " .. tostring(ss.text))
      check(w.aimed == nil or w.b > 0, "B speed model on from level 18")
    end
    check(Gr.Score(fast).text == nil or Gr.Score(fast).kind == "same", "B the worn bow against itself = same")
    local lbl, txt = Gr.ScoreLine(sb)
    check(lbl:find("pour toi", 1, true) and txt:find("remplace Arc-test rapide", 1, true), "B line: " .. lbl .. " : " .. txt)
    say("B arc", lbl .. " : " .. txt)
    for _, l in ipairs(sb.lines) do say("   ", l) end
    say("B modèle", V.ModelText(w))
    -- armor: agi leather beats int cloth; mail at 40
    local worn = item(980010, "Gilet-test porté", "INVTYPE_CHEST", 4, 2, { [AGI] = 5, [STA] = 5 })
    local agi = item(980011, "Gilet-test agile", "INVTYPE_CHEST", 4, 2, { [AGI] = 12, [STA] = 5 })
    local cloth = item(980012, "Robe-test du sage", "INVTYPE_ROBE", 4, 1, { [INT] = 14, [SPI] = 8 })
    local mail = item(980013, "Haubert-test", "INVTYPE_CHEST", 4, 3, { [AGI] = 20 })
    local staOnly = item(980014, "Gilet-test endurant", "INVTYPE_CHEST", 4, 2, { [AGI] = 5, [STA] = 20 })
    wear(5, 980010)
    equipEvent()
    check(worn and Gr.Score(agi).kind == "good" and Gr.Score(cloth).kind == "bad", "B agi leather good, int cloth bad for a hunter")
    local sm = Gr.Score(mail)
    if lvl < 40 then check(sm.usable == false and sm.later == 40, "B mail: not before 40 (« pas encore »)")
    else check(sm.usable and sm.kind == "good", "B mail wearable at 40+") end
    local so = Gr.Score(staOnly)
    check(so.kind == "good" and so.comfortOnly and not so.text:find("DPS"), "B stamina-only gain is never shown as DPS: " .. tostring(so.text))
    say("B endurance", select(1, Gr.ScoreLine(so)) .. " : " .. select(2, Gr.ScoreLine(so)))
    -- melee = stat stick: the staff with agility beats the bigger axe without stats
    item(980020, "Hache-test 2M", "INVTYPE_2HWEAPON", 2, 1, { [DPS] = 40 })
    local staff = item(980021, "Bâton-test agile", "INVTYPE_2HWEAPON", 2, 10, { [DPS] = 20, [AGI] = 10 })
    wear(16, 980020)
    equipEvent()
    check(Gr.Score(staff).kind == "good", "B melee weapon judged by its stats only (stat stick)")
    -- hit: useful up to the cap, worth nothing after
    local hitRing = item(980030, "Anneau-test du tireur", "INVTYPE_FINGER", 4, 0, { [HIT] = 20 })
    local w1 = V.Weights()
    check(w1[HIT] > 0 and w1.hitCap == (lvl >= 58 and 9 or 5), "B hit worth something under the cap (" .. tostring(w1.hitCap) .. " %)")
    item(980031, "Anneau-test plafond", "INVTYPE_FINGER", 4, 0, { [HIT] = 100 })
    wear(11, 980031) wear(12, 980031)
    equipEvent()
    local w2 = V.Weights()
    check(w2.hitCapped and w2[HIT] == 0, "B hit capped by the worn gear → worth 0")
    check(Gr.Score(hitRing).kind ~= "good", "B more hit over the cap is not an upgrade")
    wear(11, nil) wear(12, nil)
    equipEvent()
  elseif cls == "WARRIOR" then
    item(981001, "Hache-test 2M", "INVTYPE_2HWEAPON", 2, 1, { [DPS] = 30 })
    local big = item(981002, "Épée-test 2M", "INVTYPE_2HWEAPON", 2, 8, { [DPS] = 38 })
    local intStaff = item(981003, "Bâton-test du sage", "INVTYPE_2HWEAPON", 2, 10, { [DPS] = 30, [INT] = 20 })
    wear(16, 981001)
    local worn = item(981010, "Cuirasse-test portée", "INVTYPE_CHEST", 4, 3, { [STR] = 5 })
    local str = item(981011, "Cuirasse-test forte", "INVTYPE_CHEST", 4, 3, { [STR] = 12 })
    local int = item(981012, "Cuirasse-test savante", "INVTYPE_CHEST", 4, 3, { [INT] = 14 })
    wear(5, 981010)
    equipEvent()
    w = V.Weights()
    check(w.unit == "dps" and w.kind == "melee" and w.MDPS == 1 and w.RDPS == 0, "B warrior Armes: melee DPS model, ranged = stat stick")
    check(math.abs(w[STR] - 2 / 14) < 1e-6, "B warrior: 1 Force = 2 PA = 1/7 DPS")
    local sb = Gr.Score(big)
    check(sb.kind == "good" and math.abs(sb.dps - 8) < 0.01 and sb.text == "≈ +8,0 DPS", "B a better 2H weapon wins, in DPS: " .. tostring(sb.text))
    check(Gr.Score(intStaff).kind ~= "good", "B intellect on a weapon is not an upgrade for a warrior")
    check(worn and Gr.Score(str).kind == "good" and Gr.Score(int).kind == "bad", "B strength mail good, intellect mail bad")
    say("B arme", select(1, Gr.ScoreLine(sb)) .. " : " .. select(2, Gr.ScoreLine(sb)))
    Gr.SetSpec(3)
    local wt = V.Weights()
    check(wt.unit == "pts" and wt.role == "tank" and wt[STA] == 1, "B warrior Protection: tank points (stamina first)")
    Gr.SetSpec(1)
  else   -- MAGE, PRIEST
    item(982001, "Bâton-test porté", "INVTYPE_2HWEAPON", 2, 10, { [DPS] = 20, [INT] = 5 })
    local better = item(982002, "Bâton-test du savoir", "INVTYPE_2HWEAPON", 2, 10, { [DPS] = 15, [INT] = 12, [cls == "PRIEST" and HEAL or SP] = 10 })
    local brute = item(982003, "Bâton-test brutal", "INVTYPE_2HWEAPON", 2, 10, { [DPS] = 40 })
    wear(16, 982001)
    local worn = item(982010, "Robe-test portée", "INVTYPE_ROBE", 4, 1, { [INT] = 8, [STA] = 4 })
    local good = item(982011, "Robe-test du mage", "INVTYPE_ROBE", 4, 1, { [INT] = 14, [STA] = 6, [SPI] = 4 })
    local leather = item(982012, "Gilet-test de cuir", "INVTYPE_CHEST", 4, 2, { [AGI] = 20 })
    wear(5, 982010)
    equipEvent()
    w = V.Weights()
    check(w.unit == "pts" and w[INT] > 0 and (w.MDPS or 0) == 0, "B caster: weighted points, weapon DPS worth nothing")
    local sb = Gr.Score(better)
    check(sb.kind == "good" and sb.dps == nil and (sb.text:find("^%+%d+ %%$") or sb.text:find("^vaut %d+,%d fois")), "B caster: the number is « +N % » / « vaut N fois », never a DPS: " .. tostring(sb.text))
    check(Gr.Score(brute).kind == "bad", "B more weapon DPS without stats is worse for a caster")
    check(worn and Gr.Score(good).kind == "good", "B int cloth is an upgrade")
    local sl = Gr.Score(leather)
    check(sl.usable == false, "B leather: not for a cloth class")
    say("B bâton", select(1, Gr.ScoreLine(sb)) .. " : " .. select(2, Gr.ScoreLine(sb)))
    for _, l in ipairs(sb.lines) do say("   ", l) end
    say("B modèle", V.ModelText(w))
    if cls == "PRIEST" then
      check(w.role == "heal" and w[HEAL] >= 1, "B priest Sacré: healing counts")
      Gr.SetSpec(3)
      local ws = V.Weights()
      check(ws.role == "caster" and (ws[HEAL] or 0) == 0 and ws[SP] > 1, "B priest Ombre: spell damage, not healing")
      Gr.SetSpec(2)
    end
    check(V.Bracket(lvl) == (lvl >= 58 and "at60" or "mid") and (lvl < 58 or w[SP] == nil or w[SP] >= 1.2), "B level bracket of the weights")
  end
  -- unknown stats: never a score (no game stats, not in the client tables, no tooltip)
  local ghost = item(983001, "Objet-test illisible", "INVTYPE_WAIST", 4, cls == "WARRIOR" and 3 or (cls == "HUNTER" and 2 or 1), nil)
  local sg = Gr.Score(ghost)
  local gl, gt = Gr.ScoreLine(sg)
  check(sg.known == false and sg.pct == nil and gt:find("inconnu", 1, true), "B unreadable stats → « inconnu », no number: " .. tostring(gl) .. " : " .. tostring(gt))
  check(Gr.UpgradePct(ghost) == nil, "B UpgradePct of an unreadable item = nil (old contract kept)")
  say("B inconnu", gl .. " : " .. gt)
  -- tooltip scan (C_TooltipInfo) reads what the stats API does not: speed, enchant / scope, unique, set
  rawset(_G, "C_TooltipInfo", { GetHyperlink = function(link)
    if tostring(link):find("item:983001", 1, true) then
      return { lines = { { leftText = "Objet-test illisible" }, { leftText = "+7 Endurance" }, { leftText = "+3 Agilité" }, { leftText = "40 Armure" },
        { leftText = "Unique" }, { leftText = "Durabilité 20 / 30" } } }
    end
  end })
  ctx.fire("PLAYER_EQUIPMENT_CHANGED", 6)
  local tip = V.Tip(ghost)
  check(tip and tip.st[STA] == 7 and tip.st[AGI] == 3 and tip.unique and tip.dur == 20 and tip.durMax == 30, "B tooltip scan: stats, unique, durability")
  rawset(_G, "C_TooltipInfo", nil)

  -------------------------------------------------------------------------------------------------
  -- C. « stuff rentable » (mocked vendor / AH / quest / dungeon / craft data)
  -------------------------------------------------------------------------------------------------
  local prim = (cls == "HUNTER" and 3) or (cls == "WARRIOR" and 4) or 5       -- stat id: Agi / Force / Int
  local primKey = (cls == "HUNTER" and AGI) or (cls == "WARRIOR" and STR) or INT
  local armorSub = (cls == "HUNTER" and 2) or (cls == "WARRIOR" and 3) or 1
  local D = T.Data.Gear.items
  local function st(n) return prim .. ":" .. n end
  D[990001] = { 7, lvl - 2, 2, 4, armorSub, st(10), 0, 0, 40, 2, 5000, "Jambières-test du marchand", 0 }      -- legs, vendor 50 pa
  D[990002] = { 8, lvl - 2, 2, 4, armorSub, st(9), 0, 0, 30, 2, 0, "Bottes-test de quête", 0 }                -- feet, quest reward
  D[990003] = { 6, lvl - 2, 2, 4, armorSub, st(8), 0, 0, 20, 2, 0, "Ceinture-test de l'HV", 0 }               -- waist, AH 2 po 50
  D[990004] = { 3, lvl - 2, 3, 4, armorSub, st(11), 0, 0, 20, 1, 0, "Épaulières-test du boss", 0 }            -- shoulders, dungeon boss
  D[990005] = { 9, lvl - 1, 2, 4, armorSub, st(4), 0, 0, 10, 2, 990000, "Brassards-test hors de prix", 0 }    -- wrist, vendor 99 po
  D[990006] = { 10, lvl - 1, 2, 4, armorSub, st(6), 0, 0, 10, 2, 8000, "Gants-test du marchand", 0 }          -- hands, vendor 80 pa…
  D[990007] = { 10, lvl + 1, 3, 4, armorSub, st(14), 0, 0, 10, 1, 0, "Gants-test du niveau suivant", 0 }      -- … but a quest gives much better next level
  D[990008] = { 1, lvl - 2, 2, 4, armorSub, st(7), 0, 0, 10, 1, 0, "Casque-test réservé", cls == "MAGE" and 1 or 128 }   -- other class only
  M.InstallQuestieDB({
    items = { [990001] = { vendors = { 7001 } }, [990005] = { vendors = { 7001 } }, [990006] = { vendors = { 7001 } },
      [990002] = { questRewards = { 5001 } }, [990007] = { questRewards = { 5002 } }, [990004] = { npcDrops = { 7002 } } },
    npcs = { [7001] = { name = "Marchand-test", zoneID = 267 }, [7002] = { name = "Boss-test", zoneID = 796, rank = 3, maxLevel = lvl } },
    quests = { [5001] = { name = "Quête-test", requiredLevel = lvl - 3 }, [5002] = { name = "Quête-test suivante", requiredLevel = lvl + 1 } },
  })
  local A = T.modules.Auction
  local oldStats = A and A.Stats
  if A then A.Stats = function(id) if id == 990003 then return { market = 25000, conf = 0.8, src = "TreeAI" } end end end
  -- bags: an upgrade for the hands... no: for the neck (equip now) + a grey item
  local bagNeck = item(984001, "Collier-test des sacs", "INVTYPE_NECK", 4, 0, { [primKey] = 6 })
  item(984002, "Camelote-test", "", 15, 0, nil, { quality = 0 })
  M.SetBagItem(0, 1, 984001, 1)
  M.SetBagItem(0, 2, 984002, 3)
  ctx.fire("BAG_UPDATE_DELAYED")
  Gr.db.horizon = 3
  Gr.SetMountReserve(0)
  M.player.money = 100000   -- 10 po
  Gr.MarkDirty()
  local plan = Gr:ScanNow()
  check(plan and plan.slots and plan.now and plan.top, "C plan computed")
  local rows = Gr.Rent(plan)
  local by = {}
  for _, r in ipairs(rows) do by[r.slot] = r end
  local function act(slot) return by[slot] and by[slot].action end
  check(#rows == 17, "C one verdict per slot")
  check(act(2) == "equip" and by[2].text:find("^Équipe Collier%-test des sacs") and by[2].onClick, "C bags → « Équipe » first: " .. tostring(by[2] and by[2].text))
  check(rows[1].action == "equip", "C equip (free, now) ranks first")
  check(act(7) == "buy" and by[7].text:find("^Achète Jambières%-test du marchand") and by[7].right == "50 pa", "C vendor in budget → « Achète »: " .. tostring(by[7] and by[7].text))
  check(act(8) == "quest" and by[8].text:find("^Fais la quête « Quête%-test »"), "C quest reward → « Fais la quête X »: " .. tostring(by[8] and by[8].text))
  check(act(6) == "buy" and by[6].text:find("^Achète à l'HV Ceinture%-test"), "C AH price → « Achète à l'HV »: " .. tostring(by[6] and by[6].text))
  if lvl >= 32 then
    check(act(3) == "boss" and by[3].text:find("^Donjon : Monastère Écarlate"), "C dungeon boss → « Donjon X »: " .. tostring(by[3] and by[3].text))
  else
    check(act(3) == "later" and by[3].text:find("plus tard", 1, true) and by[3].text:find("dès le niveau 32", 1, true),
      "C dungeon too high for now → « plus tard (dès le niveau 32) », not « maintenant »: " .. tostring(by[3] and by[3].text))
  end
  check(act(9) == "save" and by[9].text:find("trop cher", 1, true), "C 99 po with 10 po → « trop cher », not « Achète »: " .. tostring(by[9] and by[9].text))
  if lvl < 60 then
    check(act(10) == "wait" and by[10].text:find("attends le niveau " .. (lvl + 1), 1, true) and by[10].text:find("Gants%-test du niveau suivant"),
      "C better next level from a quest → « Attends le niveau N », don't spend: " .. tostring(by[10] and by[10].text))
  else
    check(act(10) == "buy" and by[10].text:find("Gants%-test du marchand"), "C at the level cap nothing « au niveau 61 »: " .. tostring(by[10] and by[10].text))
  end
  check(act(1) == "keep", "C class-reserved helm never proposed → « garde » / vide: " .. tostring(by[1] and by[1].text))
  local unit = V.Weights().unit
  check(by[7].text:find(unit == "dps" and "DPS" or "emplacement vide"), "C the gain is ONE number (DPS; empty slot said for point specs): " .. by[7].text)
  -- ranking: among the things to get, the best gain per cost first (the quest is free, the AH belt costs 2 po 50)
  local order = {}
  for i, r in ipairs(rows) do order[r.slot] = i end
  if lvl < 40 then check(order[7] < order[6], "C vendor 50 pa (+10) ranks before AH 2 po 50 (+8): gain per cost") end
  check(order[9] > order[6] and (lvl >= 60 or order[10] > order[9]) and order[1] > order[9], "C then « trop cher », « attends », « garde »")
  -- budget: the mount reserve changes the verdicts (asked once between 30 and 39)
  local bn = Gr.BestNow()
  local nNow = #bn
  check(nNow >= 4 and bn[1].action == "equip", "C BestNow(): what can be done now (" .. nNow .. ")")
  for _, r in ipairs(bn) do check(r.action ~= "wait" and r.action ~= "keep" and r.action ~= "save", "C BestNow has only doable rows") end
  Gr.SetMountReserve(1000000)
  rows = Gr.Rent(plan)
  by = {}
  for _, r in ipairs(rows) do by[r.slot] = r end
  if lvl < 42 then
    check(rows.budget.free == 0 and by[7].action == "save" and by[6].action == "save", "C 100 po kept for the mount → no purchase advised with 10 po")
    check(by[8].action == "quest" and by[2].action == "equip", "C free things stay advised")
  else
    check(rows.budget.reserve == 0 and by[7].action == "buy", "C no mount reserve after 40")
  end
  Gr.SetMountReserve(0)
  if lvl >= 30 and lvl < 40 then
    Gr.char.mountAsked = nil
    check(Gr.AskMountOnce() and T._suggestions["Gear:mount"] and T._suggestions["Gear:mount"].text:find("monture", 1, true), "C mount reserve asked once (30-39)")
    check(Gr.AskMountOnce() == false, "C … once")
    say("C monture", T._suggestions["Gear:mount"].text)
    Gr.SetMountReserve(0)
  end
  rows = Gr.Rent(plan)
  for _, r in ipairs(rows) do
    say("C", ("%-7s"):format(r.action), r.text, r.right and ("[" .. r.right .. "]") or "", r.clickHint and ("{" .. r.clickHint .. "}") or "")
  end
  for _, t in ipairs(rows[2].tooltip or {}) do say("      infobulle :", t) end
  -- one click to go: vendor → compass (or false without a known position), AH → the AH page, bag → equip out of combat
  local equipped
  local CI = rawget(_G, "C_Item")
  local oldEq, oldEqG = CI and CI.EquipItemByName, rawget(_G, "EquipItemByName")
  if CI then CI.EquipItemByName = function(l) equipped = l end end
  rawset(_G, "EquipItemByName", function(l) equipped = l end)
  by = {}
  for _, r in ipairs(rows) do by[r.slot] = r end
  M.EnterCombat()
  T:UserAction(by[2].onClick)
  check(equipped == nil, "C equip click in combat: nothing (protected in combat)")
  M.LeaveCombat()
  T:UserAction(by[2].onClick)
  check(equipped == bagNeck, "C equip click out of combat: EquipItemByName(link) once")
  if CI then CI.EquipItemByName = oldEq end
  rawset(_G, "EquipItemByName", oldEqG)
  -- paper doll: weakest first, bars
  local doll = Gr.Doll(plan)
  check(#doll == 17 and doll[1].ratio <= doll[17].ratio and doll[1].ratio == 0, "C « Mon équipement »: weakest slots first (empty = 0)")

  -------------------------------------------------------------------------------------------------
  -- D. live help
  -------------------------------------------------------------------------------------------------
  -- D1. tooltip line through « Infos objets » (one owner), cached: same result table on repeat
  local II = T.modules.ItemInfo
  local upLink = (cls == "HUNTER" and M.ItemLink(980011)) or (cls == "WARRIOR" and M.ItemLink(981011)) or M.ItemLink(982011)
  local badLink = (cls == "HUNTER" and M.ItemLink(980012)) or (cls == "WARRIOR" and M.ItemLink(981012)) or M.ItemLink(982003)
  if II and II._active and II.Decorate then
    local function tipOf(id)
      GameTooltip.__item, GameTooltip.__lines = id, {}
      local lines = II.Decorate(GameTooltip, { id = id })
      local t = {}
      for _, l in ipairs(lines or {}) do t[#t + 1] = (tostring(l[1]):gsub("|c%x%x%x%x%x%x%x%x", ""):gsub("|r", "")) end
      return table.concat(t, " || ")
    end
    local up = tipOf(tonumber(upLink:match("item:(%d+)")))
    check(up:find("mieux pour toi : ", 1, true) and up:find("remplace", 1, true) and up:find(unit == "dps" and "DPS" or "[%%f]"),
      "D1 tooltip « Mieux pour toi : <nombre> (remplace X) »: " .. up)
    local bad = tipOf(tonumber(badLink:match("item:(%d+)")))
    check(bad:find("Moins bien pour toi", 1, true), "D1 tooltip « Moins bien »: " .. bad)
    local unk = tipOf(983001)
    check(unk:find("inconnu", 1, true) and not unk:find("mieux", 1, true), "D1 tooltip of an unreadable item: « inconnu »: " .. unk)
    say("D1 infobulle +", up)
    say("D1 infobulle -", bad)
    say("D1 infobulle ?", unk)
  end
  local s1, s2 = Gr.Score(upLink), Gr.Score(upLink)
  check(s1 == s2 and s1.known, "D1 Score cached by link: O(1) on repeat (same table)")
  -- D2. quest reward: the biggest gain in VALUE is circled, never picked
  local picked
  rawset(_G, "GetQuestReward", function(i) picked = i end)
  M.questChoices = { tonumber(badLink:match("item:(%d+)")), tonumber(upLink:match("item:(%d+)")) }
  local bi, _, why, bsc = Gr.RankQuestRewards()
  check(bi == 2 and why == "upgrade" and bsc and bsc.text, "D2 best reward = the upgrade (index 2), with its number")
  CreateFrame("Button", "QuestInfoRewardsFrameQuestInfoItem2", UIParent)
  ctx.fire("QUEST_COMPLETE")
  M.Advance(1)
  local rw = T._suggestions["Gear:reward"]
  check(rw and rw.text:find("^Prends « ") and Gr.RewardGlowShown() and picked == nil, "D2 reward circled + one line, never chosen: " .. tostring(rw and rw.text))
  say("D2 récompense", rw and rw.text)
  ctx.fire("QUEST_FINISHED")
  M.questChoices = {}
  -- D3. keeper for BAGS: upgrades are never sold (guarded registration)
  local keeper
  local oldBags = T.Bags
  T.Bags = { RegisterKeeper = function(name, fn) keeper = { name = name, fn = fn } end }
  check(Gr.RegisterKeeper() and keeper and keeper.name == "Gear", "D3 keeper registered with T.Bags.RegisterKeeper")
  if keeper then
    local n, whyK = keeper.fn(984001)
    check(n == 1 and type(whyK) == "string" and whyK:find("amélioration", 1, true), "D3 bag upgrade kept: " .. tostring(whyK))
    check((keeper.fn(984002)) == 0, "D3 junk is not kept")
    say("D3 garde-fou", "Collier-test des sacs →", whyK)
  end
  T.Bags = oldBags
  -- D4. bag arrows: one arrow on the upgrade's button, none on junk; our own frame, not a child of the button
  local H = T.Highlight
  if H then
    local b1, b2 = CreateFrame("Button", nil, UIParent), CreateFrame("Button", nil, UIParent)
    local oldBtns = H.BlizzardBagButtons
    H.BlizzardBagButtons = function() return { { b1, 0, 1 }, { b2, 0, 2 } } end
    local n = Gr.RefreshArrows()
    check(n == 1, "D4 one green arrow (the upgrade), none on junk: " .. tostring(n))
    local kids = 0
    for _, c in ipairs(b1.__children or {}) do if c then kids = kids + 1 end end
    check(kids == 0, "D4 the arrow is never a child of the game's button")
    Gr.db.arrows = false
    check(Gr.RefreshArrows() == 0, "D4 option off → no arrow")
    Gr.db.arrows = true
    H.BlizzardBagButtons = oldBtns
    Gr.RefreshArrows()
  end
  -- D5. the page: 3 big actions on top, spec line, the three views
  local W = T.Window
  if W and W.Open then
    Gr.view = "now"
    W.Open("gear")
    W.Render()
    local function labels()
      local t = {}
      for _, r in ipairs(W.lastLay and W.lastLay.rects or {}) do t[#t + 1] = r.kind .. ":" .. tostring(r.label) end
      return t, table.concat(t, " || ")
    end
    local t, all = labels()
    check(t[1] == "button:» Meilleur maintenant" and t[2] == "button:Stuff rentable" and t[3] == "button:Mon équipement",
      "D5 top = 3 big actions: " .. table.concat({ tostring(t[1]), tostring(t[2]), tostring(t[3]) }, " | "))
    check(all:find("row:Spé : ", 1, true) and all:find("choisie par toi", 1, true), "D5 spec + source on the page")
    check(all:find("header:Le meilleur maintenant", 1, true) and all:find("Équipe Collier%-test des sacs"), "D5 « Meilleur maintenant » lists the bag upgrade")
    check(not all:find("garde", 1, true), "D5 « Meilleur maintenant » has no « garde » rows")
    say("D5 page « Meilleur maintenant »")
    for _, l in ipairs(t) do say("      ", l) end
    Gr.view = "rent"
    W.Render()
    t, all = labels()
    check(all:find("row:Budget : ", 1, true) and (lvl >= 60 or all:find("attends le niveau", 1, true)) and all:find("garde", 1, true), "D5 « Stuff rentable »: budget + every verdict: " .. all)
    Gr.view = "doll"
    W.Render()
    t, all = labels()
    local bars = 0
    for _, l in ipairs(t) do if l:find("^gearbar:") or l:find("^row:[^:]+ : ") then bars = bars + 1 end end
    check(all:find("header:Mon équipement", 1, true) and bars >= 17, "D5 « Mon équipement »: one bar per slot (" .. bars .. ")")
    say("D5 page « Mon équipement »")
    for i = 1, math.min(#t, 9) do say("      ", t[i]) end
    -- the spec row opens the 3-button choice
    Gr.view = "now"
    Gr.pickSpec = true
    W.Render()
    t, all = labels()
    local tiles = 0
    for _, l in ipairs(t) do if l:find("^tile:") then tiles = tiles + 1 end end
    check(tiles == 3, "D5 spec choice = 3 buttons (" .. tiles .. ")")
    Gr.pickSpec = false
    Gr.view = "now"
  end

  -------------------------------------------------------------------------------------------------
  -- E. cost and rules
  -------------------------------------------------------------------------------------------------
  -- E1. a plain bag change (loot) does not start a plan; the verdicts stay cached
  settle(12)
  local before = Gr.plan
  local r0 = Gr.Rent()
  M.SetBagItem(0, 3, 984002, 1)
  ctx.fire("BAG_UPDATE_DELAYED")
  settle(14)
  check(Gr.plan == before and not Gr.Busy(), "E1 bag change: no new plan (bags are read live)")
  check(Gr.Rent().count.equip == r0.count.equip, "E1 same verdicts after junk loot")
  -- E2. equipment change → one plan, time-sliced, never in combat
  M.EnterCombat()
  ctx.fire("PLAYER_EQUIPMENT_CHANGED", 5)
  settle(14)
  check(Gr.plan == before, "E2 no plan in combat")
  M.LeaveCombat()
  for _ = 1, 60 do M.Advance(0.5) Gr:AutoRefresh() end
  check(Gr.plan ~= before and not Gr.Busy(), "E2 equipment change → new plan after combat")
  -- E3. nothing in chat, no blocked action
  check(#M.output == out0, "E3 nothing in chat (" .. (#M.output - out0) .. " lines)")
  check(#M.blocked == blocked0, "E3 zero blocked actions")

  -- restore
  if A then A.Stats = oldStats end
  T.Data.Gear.items, T.Data.Gear.crafted = realItems, realCrafted
  for _, n in ipairs(SAVED) do rawset(_G, n, saved[n]) end
  if type(SI0) == "table" then SI0.GetSpecialization = siGet end
  forget()
  Gr.SetSpec(nil)
  M.inventory, M.bags = {}, {}
  ctx.fire("PLAYER_EQUIPMENT_CHANGED", 1)
  local path = os.getenv("TREEAI_GEAR5_DUMP")
  if path and path ~= "" then
    local f = io.open(path, "a")
    if f then f:write(table.concat(dump, "\n"), "\n\n") f:close() end
  end
end
