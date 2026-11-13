-- iteminfo_scenario.lua — Modules/ItemInfo.lua (ITEMINFO, 4.0.2) : lignes d'infobulle d'objet, une section par sujet.
-- Hunter niv 28 (familier niv 25). Fake items / names only. Every flavour (modern, legacy, forever : valeurs secrètes
-- en combat). Checks: wording, ≤ 6 lines, one owner (Gear / Highlight / Quests silent), Maj, cache + invalidation,
-- async item data, combat (no secret compare), zero blocked action, TooltipDataProcessor path, cost per hover.
return function(M, ctx)
  local T = TreeAI
  local check = ctx.check
  local II = T and T.modules and T.modules.ItemInfo
  check(II and II.db and II._active == true, "ItemInfo registered and on")
  check(T.ItemInfo == II and II.Owns and II.Owns(), "T.ItemInfo + Owns()")
  if not (II and II._active) then return end
  check(T.MODULE_INFO.ItemInfo and T.MODULE_INFO.ItemInfo.lite and T.SAFE_MODULES.ItemInfo, "MODULE_INFO + mode sûr")
  M.LeaveCombat()
  M.player.level = 28
  M.pet.level = 25
  M.shift = false
  M.group = {}
  ctx.fire("GROUP_ROSTER_UPDATE")

  local function plain(s) return (tostring(s):gsub("|c%x%x%x%x%x%x%x%x", ""):gsub("|r", "")) end
  local function bagBtn(bag, slot)
    local b = CreateFrame("Button", nil, UIParent)
    b:SetID(slot)
    function b:GetBagID() return bag end
    return b
  end
  local function show(id, owner)
    GameTooltip.__item, GameTooltip.__lines = id, {}
    if owner then GameTooltip.GetOwner = function() return owner end end
    local fn = GameTooltip:GetScript("OnTooltipSetItem")
    if fn then fn(GameTooltip) end
    GameTooltip.GetOwner = nil
    local out, raw = {}, GameTooltip.__lines
    for _, l in ipairs(raw) do out[#out + 1] = plain(l) end
    GameTooltip.__item = nil
    return out, table.concat(out, " | "), raw
  end
  local function has(joined, pat) return joined:find(pat, 1, true) ~= nil end

  -- items (fake) -----------------------------------------------------------------------------------
  local A, S = "ITEM_MOD_AGILITY_SHORT", "ITEM_MOD_STAMINA_SHORT"
  M.AddItem(91001, { name = "Gilet agile", classID = 4, subclassID = 2, equipLoc = "INVTYPE_CHEST", quality = 2,
    sellPrice = 120, minLevel = 25, stats = { [A] = 8, [S] = 5, RESISTANCE0_NAME = 80 } })
  M.AddItem(91002, { name = "Gilet usé", classID = 4, subclassID = 2, equipLoc = "INVTYPE_CHEST", sellPrice = 30,
    stats = { [A] = 2, [S] = 2, RESISTANCE0_NAME = 60 } })
  M.AddItem(91003, { name = "Haubert de mailles", classID = 4, subclassID = 3, equipLoc = "INVTYPE_CHEST", quality = 2,
    sellPrice = 400, stats = { [A] = 10 } })
  M.AddItem(91004, { name = "Arc long", classID = 2, subclassID = 2, equipLoc = "INVTYPE_RANGED", quality = 2,
    sellPrice = 900, stats = { ITEM_MOD_DAMAGE_PER_SECOND_SHORT = 14.5, [A] = 2 } })
  M.AddItem(91005, { name = "Arc court", classID = 2, subclassID = 2, equipLoc = "INVTYPE_RANGED", sellPrice = 200,
    stats = { ITEM_MOD_DAMAGE_PER_SECOND_SHORT = 9.8 } })
  M.AddItem(91006, { name = "Cuirasse lourde", classID = 4, subclassID = 4, equipLoc = "INVTYPE_CHEST", quality = 2,
    sellPrice = 800, stats = { ITEM_MOD_STRENGTH_SHORT = 12 } })
  M.AddItem(91007, { name = "Anneau du faucon", classID = 4, subclassID = 0, equipLoc = "INVTYPE_FINGER", quality = 2,
    sellPrice = 300, stats = { [A] = 3 } })
  M.AddItem(91008, { name = "Anneau de l'ours", classID = 4, subclassID = 0, equipLoc = "INVTYPE_FINGER", quality = 2,
    sellPrice = 300, stats = { [S] = 6 } })
  M.AddItem(91009, { name = "Anneau neuf", classID = 4, subclassID = 0, equipLoc = "INVTYPE_FINGER", quality = 2,
    sellPrice = 300, stats = { [A] = 5 } })
  M.AddItem(91010, { name = "Gilet du vétéran", classID = 4, subclassID = 2, equipLoc = "INVTYPE_CHEST", quality = 2,
    sellPrice = 300, minLevel = 30, stats = { [A] = 10, [S] = 6 } })
  M.AddItem(91011, { name = "Gilet jumeau", classID = 4, subclassID = 2, equipLoc = "INVTYPE_CHEST", sellPrice = 30,
    stats = { [A] = 2, [S] = 2, RESISTANCE0_NAME = 60 } })
  M.AddItem(91012, { name = "Gilet troué", classID = 4, subclassID = 2, equipLoc = "INVTYPE_CHEST", sellPrice = 5,
    stats = { [S] = 1, RESISTANCE0_NAME = 20 } })
  M.AddItem(91013, { name = "Gants précis", classID = 4, subclassID = 2, equipLoc = "INVTYPE_HAND", quality = 2,
    sellPrice = 150, stats = { ITEM_MOD_HIT_RATING_SHORT = 1 } })
  M.AddItem(91014, { name = "Robe de l'érudit", classID = 4, subclassID = 1, equipLoc = "INVTYPE_ROBE", quality = 2,
    sellPrice = 200, stats = { ITEM_MOD_INTELLECT_SHORT = 8, ITEM_MOD_SPIRIT_SHORT = 6 } })
  M.inventory[5] = { id = 91002, dur = 50, durMax = 60 }
  M.inventory[18] = { id = 91005, dur = 50, durMax = 60 }
  M.inventory[11] = { id = 91007 }
  M.inventory[12] = { id = 91008 }
  M.inventory[10] = nil
  M.bags = {}
  for b = 0, 4 do M.bags[b] = { size = 16, slots = {} } end
  ctx.fire("PLAYER_EQUIPMENT_CHANGED", 5, true)
  ctx.fire("BAG_UPDATE_DELAYED")
  local blocked0 = #M.blocked
  local addLine0 = GameTooltip.AddLine
  local hooks = 0
  debug.sethook(function() hooks = hooks + 1 end, "", 100)
  II.Decorate(GameTooltip, { id = 2672 })   -- first item tooltip of the session: indexes built once
  debug.sethook()
  check(hooks / 10 <= 400, ("very first hover (indexes built) ≤ 400 kinstr (%.1f)"):format(hooks / 10))
  GameTooltip.__lines = {}

  -- 1. Amélioration --------------------------------------------------------------------------------
  local lines, j = show(91001)
  check(has(j, "Nettement mieux pour toi : +6 Agi, +3 End"), "upgrade in words + key numbers: " .. j)
  check(j:find("≈ %+%d+ %% de dégâts"), "hunter: ≈ % of ranged damage: " .. j)
  check(not has(j, "que ce que tu portes"), "Gear's old line silent (one owner): " .. j)
  check(#lines <= 6, "≤ 6 lines")
  local _, jWorn = show(91002)
  check(not has(jWorn, "Mieux") and not has(jWorn, "Pareil") and not has(jWorn, "Moins bien"), "worn item: no comparison with itself: " .. jWorn)
  _, j = show(91003)
  check(has(j, "Pas encore : mailles portables au niv 40"), "hunter mail at 40: " .. j)
  _, j = show(91006)
  check(has(j, "Pas pour toi : armure que tu ne peux pas porter"), "plate refused: " .. j)
  _, j = show(91004)
  check(has(j, "Nettement mieux pour toi : DPS 9,8 → 14,5, +2 Agi"), "weapon DPS delta: " .. j)
  check(j:find("≈ %+%d+ %% de dégâts"), "bow: ≈ % damage: " .. j)
  _, j = show(91009)
  check(j:find("[Mm]ieux pour toi :") and has(j, "+5 Agi") and has(j, "-6 End"), "ring compared with the weaker ring (sta): " .. j)
  M.shift = true
  _, j = show(91009)
  check(has(j, "Comparé à : Anneau de l'ours"), "Maj: which worn item: " .. j)
  M.shift = false
  _, j = show(91010)
  check(has(j, "niv 30 requis"), "level requirement: " .. j)
  _, j = show(91011)
  check(has(j, "Pareil pour toi : mêmes stats"), "same stats → Pareil: " .. j)
  _, j = show(91012)
  check(has(j, "Moins bien pour toi : -2 Agi") or has(j, "Moins bien pour toi : -1 End"), "worse → Moins bien: " .. j)
  _, j = show(91013)
  check(has(j, "Mieux pour toi : emplacement vide, +1 Toucher") and not has(j, "≈"), "empty slot; hit not in the damage model → no %: " .. j)
  -- reserved to other classes (client class mask, Data/Gear.lua): never « Mieux »
  local GD = T.Data and T.Data.Gear and T.Data.Gear.items
  if GD then
    M.AddItem(91018, { name = "Gilet du guerrier", classID = 4, subclassID = 2, equipLoc = "INVTYPE_CHEST", quality = 2,
      sellPrice = 200, stats = { [A] = 12 } })
    GD[91018] = { 5, 20, 2, 4, 2, "3:12", 0, 0, 50, 1, 0, "Gilet du guerrier", 1 }   -- masque 1 = guerrier seulement
    _, j = show(91018)
    check(has(j, "Pas pour toi : réservé à d'autres classes") and not has(j, "Mieux"), "class-restricted item: " .. j)
    GD[91018] = nil
  end
  -- ammo for the equipped bow
  _, j = show(2519)
  check(has(j, "Pas pour ton arme : balles (ton arc tire des flèches)"), "bullets refused for a bow: " .. j)
  _, j = show(2515)
  check(not has(j, "Pas pour ton arme"), "arrows fit the bow: " .. j)

  -- 2. Groupe ---------------------------------------------------------------------------------------
  M.group = { { name = "Compagnon", class = "WARRIOR", level = 27, online = true },
    { name = "Furtif", class = "ROGUE", level = 28, online = true } }
  ctx.fire("GROUP_ROSTER_UPDATE")
  M.Advance(3)
  _, j = show(91001)
  check(has(j, "Pour ton groupe : Besoin pour toi (nettement mieux) et Furtif · Cupidité pour les autres"), "group: leather → hunter + rogue: " .. j)
  _, j = show(91003)
  check(has(j, "Pour ton groupe : Besoin pour Compagnon"), "group: mail → warrior < 40: " .. j)
  check(has(j, "→ Donne-le à Compagnon"), "value verdict: give it to the friend who needs it: " .. j)
  _, j = show(91014)
  check(has(j, "Pour ton groupe : personne n'en a besoin → Cupidité pour tous"), "group: cloth nobody wears → Greed: " .. j)
  -- already in your bags: no roll wording; only a friend who needs it
  M.SetBagItem(2, 5, 91003, 1)
  M.SetBagItem(2, 6, 91001, 1)
  M.SetBagItem(2, 7, 91014, 1)
  ctx.fire("BAG_UPDATE_DELAYED")
  _, j = show(91003, bagBtn(2, 5))
  check(has(j, "Pour ton groupe : utile à Compagnon") and has(j, "→ Donne-le à Compagnon") and not has(j, "Besoin"), "in your bags: « utile à » + give: " .. j)
  _, j = show(91001, bagBtn(2, 6))
  check(has(j, "Pour ton groupe : utile à Furtif") and not has(j, "Donne-le"), "in your bags, you need it too: keep, friend named: " .. j)
  _, j = show(91014, bagBtn(2, 7))
  check(not has(j, "Groupe"), "in your bags, nobody else: no group line: " .. j)
  M.SetBagItem(2, 5, nil) M.SetBagItem(2, 6, nil) M.SetBagItem(2, 7, nil)
  -- weapons: proficiency, then stats (a caster staff is not for the hunter)
  M.AddItem(91015, { name = "Bâton du sage", classID = 2, subclassID = 10, equipLoc = "INVTYPE_2HWEAPON", quality = 2,
    sellPrice = 500, stats = { ITEM_MOD_INTELLECT_SHORT = 9, ITEM_MOD_SPIRIT_SHORT = 7, ITEM_MOD_DAMAGE_PER_SECOND_SHORT = 6 } })
  M.AddItem(91016, { name = "Hache du traqueur", classID = 2, subclassID = 1, equipLoc = "INVTYPE_2HWEAPON", quality = 2,
    sellPrice = 600, stats = { [A] = 6, ITEM_MOD_DAMAGE_PER_SECOND_SHORT = 12 } })
  M.inventory[16] = { id = 91016, dur = 50, durMax = 60 }
  ctx.fire("PLAYER_EQUIPMENT_CHANGED", 16, true)
  M.group[#M.group + 1] = { name = "Prêtresse", class = "PRIEST", level = 27, online = true }
  ctx.fire("GROUP_ROSTER_UPDATE")
  M.Advance(3)
  _, j = show(91015)
  check(has(j, "Besoin pour Prêtresse") and not has(j, "Besoin pour toi") and not has(j, "Compagnon"), "caster staff → the priest: " .. j)
  -- an upgrade for you outside your armour type counts for YOU, never for a friend of your class
  M.AddItem(91017, { name = "Robe agile", classID = 4, subclassID = 1, equipLoc = "INVTYPE_ROBE", quality = 2,
    sellPrice = 250, stats = { [A] = 12 } })
  M.group[#M.group + 1] = { name = "Traqueur", class = "HUNTER", level = 28, online = true }
  ctx.fire("GROUP_ROSTER_UPDATE")
  M.Advance(3)
  _, j = show(91017)
  check(has(j, "Besoin pour toi") and not has(j, "Traqueur"), "your upgrade, not your class: " .. j)
  -- buying at a merchant / choosing a quest reward: no roll, no sell-or-keep verdict (facts only)
  local merch = CreateFrame("Button", "MerchantItem1ItemButton", UIParent)
  _, j = show(91003, merch)
  check(not has(j, "Groupe") and not has(j, "→") and has(j, "Pas encore"), "merchant: facts only: " .. j)
  local reward = CreateFrame("Button", "QuestInfoRewardsFrameQuestInfoItem1", UIParent)
  _, j = show(91001, reward)
  check(not has(j, "Groupe") and not has(j, "→") and has(j, "Nettement mieux"), "quest reward: facts only: " .. j)
  _, j = show(91001)
  check(has(j, "Pour ton groupe : Besoin pour toi"), "same item, other context (no cache mix-up): " .. j)
  M.group = {}
  ctx.fire("GROUP_ROSTER_UPDATE")
  M.Advance(3)
  _, j = show(91001)
  check(not has(j, "Groupe"), "solo: no group line")

  -- 3. Quête (journal + surbrillance, sans Questie) -----------------------------------------------------
  local H = T.modules.Highlight
  local Q = T.modules.Quests
  M.AddItem(9201, { name = "Sang de lion", type = "Quête", classID = 12, stack = 20 })
  M.AddItem(9202, { name = "Paquet scellé", type = "Quête", classID = 12 })
  M.AddItem(9203, { name = "Vieux trophée", type = "Quête", classID = 12, questItem = true })
  M.AddItem(9204, { name = "Lettre tachée de sang", type = "Quête", classID = 12, questItem = true, startsQuest = 777 })
  M.quests = {
    { header = "Contreforts de Hautebrande" },
    { title = "Sang de lion", level = 26, objectives = { { text = "Sang de lion : 3/8", done = false, have = 3, need = 8, type = "item" } } },
    { title = "Le courrier", level = 24, complete = true, objectives = { { text = "Paquet scellé : 1/1", done = true, have = 1, need = 1 } } },
  }
  M.SetBagItem(0, 1, 9201, 3)
  M.SetBagItem(0, 2, 9202, 1)
  M.SetBagItem(0, 3, 9203, 1)
  M.SetBagItem(0, 4, 9204, 1)
  ctx.fire("QUEST_LOG_UPDATE")
  M.Advance(2)
  if Q and Q.Update then Q.Update() end
  if H and H.Rebuild then H.Rebuild() end
  _, j = show(9201, bagBtn(0, 1))
  check(has(j, "Objet de quête : « Sang de lion » (3/8)"), "quest objective x/y: " .. j)
  check(not has(j, "Quête : Sang de lion — encore"), "Highlight's old line silent: " .. j)
  _, j = show(9202, bagBtn(0, 2))
  check(has(j, "Objet de quête : « Le courrier » (1/1) — à rendre, garde-le"), "objective done, quest to turn in: " .. j)
  _, j = show(9203, bagBtn(0, 3))
  check(has(j, "Objet de quête : plus utile"), "quest item of no quest in the log: " .. j)
  _, j = show(9204, bagBtn(0, 4))
  check(has(j, "! Commence une quête"), "quest starter (bag quest info): " .. j)
  _, j = show(9203)
  check(not has(j, "plus utile"), "no bag / loot info, no QuestieDB: never « plus utile » on a guess: " .. j)

  -- 4. Métiers (Secourisme connu, recettes lues dans la fenêtre du métier) ------------------------------
  local P = T.modules.Prof
  M.skills = { { name = "Secourisme", rank = 90, max = 150 } }
  M.AddItem(2592, { name = "Étoffe de laine", classID = 7, stack = 20, sellPrice = 33 })
  M.SetBagItem(1, 1, 2592, 6)
  if P and P.char then P.char.known = { [129] = { [3277] = 1, [3278] = 1 } } end
  ctx.fire("SKILL_LINES_CHANGED")
  ctx.fire("BAG_UPDATE_DELAYED")
  _, j = show(2592, bagBtn(1, 1))
  check(has(j, "Métiers : compo de « Bandage en laine » (×6 possibles) et 1 autre recette"), "known recipes + how many: " .. j)
  check(has(j, "Valeur : marchand 33 pc (pile ×6 : 1 pa 98 pc)"), "stack value from the bag slot: " .. j)
  _, j = show(2672)
  check(has(j, "Métiers : sert en Cuisine"), "trade good of a profession you don't have: " .. j)

  -- 5. Familier (régime : viande, poisson ; familier niv 25) -----------------------------------------
  M.AddItem(3770, { name = "Viande de sanglier", classID = 0, stack = 20, sellPrice = 25 })
  M.AddItem(117, { name = "Bœuf coriace", classID = 0, stack = 20, sellPrice = 1 })
  _, j = show(3770)
  check(has(j, "Familier : il aime ça (viande)"), "pet food: full effect: " .. j)
  _, j = show(117)
  check(has(j, "Familier : il en mange (viande), effet réduit"), "pet food: low level: " .. j)
  _, j = show(4540)
  check(has(j, "Familier : il n'en mange pas (il mange : poisson, viande)"), "pet food: wrong diet: " .. j)

  -- 6. Valeur (modèle de liquidité de l'HV, remplacé ici par des valeurs connues) ------------------------
  local Au = T.modules.Auction
  M.AddItem(91020, { name = "Fourrure épaisse", classID = 7, stack = 20, sellPrice = 50 })
  M.AddItem(91021, { name = "Pierre étrange", classID = 7, stack = 20, sellPrice = 40 })
  M.AddItem(91022, { name = "Bague liée", classID = 4, subclassID = 0, equipLoc = "INVTYPE_NECK", sellPrice = 70,
    stats = { [S] = 1 } })
  local oldLiq, oldStats, oldPost = Au and Au.Liq, Au and Au.Stats, Au and Au.PostPrice
  if Au then
    Au.Liq = function(id)
      if id == 91020 then return { ok = true, market = 4500, sold = 6, days = 5, conf = 0.82, reasons = {} } end
      return { ok = false, market = 0, reasons = { { code = "short", text = "historique trop court (1 j sur 3)" } } }
    end
    Au.Stats = function(id)
      if id == 91021 then return { market = 1000, conf = 0.2, src = "TreeAI" } end
      if id == 91020 then return { market = 4500, conf = 0.8, src = "TreeAI" } end
    end
    Au.PostPrice = function(id, n) return { better = "ah", unit = 4400, net = 4180 * (n or 1) } end
    M.SetBagItem(1, 2, 91020, 4)
    ctx.fire("BAG_UPDATE_DELAYED")
    _, j = show(91020, bagBtn(1, 2))
    check(has(j, "HV ≈ 45 pa (vendu ~6/j)") and has(j, "→ vends à l'HV"), "reliable market → AH (4.1: HV first): " .. j)
    _, j = show(91021)
    check(has(j, "HV : pas de marché fiable"), "unreliable market said as such: " .. j)
    M.shift = true
    _, j = show(91021)
    check(has(j, "historique trop court"), "Maj: why the market is not reliable: " .. j)
    M.shift = false
    Au.Liq, Au.Stats, Au.PostPrice = oldLiq, oldStats, oldPost
  end
  _, j = show(4865)
  check(has(j, "→ Vendre au marchand"), "grey → sell to the vendor: " .. j)
  _, j = show(1179)
  check(not has(j, "Valeur"), "unit vendor price alone (the game shows it): no line: " .. j)

  -- 7. Sources (taux MESURÉ par Stats ≥ 10 corps, sinon QuestieDB sans taux) ----------------------------
  local db = TreeAIDB
  db.stats = db.stats or { v = 1 }
  db.stats.v, db.stats.drops, db.stats.names = 1, db.stats.drops or {}, db.stats.names or {}
  db.stats.drops[3002] = { n = 25, nm = "Lion des collines", i = { [9201] = { 25, 8 } }, t = 1 }
  db.stats.names["sang de lion"] = 9201
  _, j = show(9201, bagBtn(0, 1))
  check(has(j, "Source : Lion des collines (8 sur 25 corps, 32 %)"), "measured drop rate (raw counts): " .. j)
  db.stats.drops[3002].i[9201] = { 4, 2 }
  ctx.fire("BAG_UPDATE_DELAYED")
  _, j = show(9201, bagBtn(0, 1))
  check(not has(j, "corps"), "4 corpses only: no rate shown: " .. j)

  -- 8. QuestieDB : niveau d'une quête de départ, monstres sans taux ; Questie écrit déjà la progression
  rawset(_G, "QuestieLoader", { _modules = { QuestieDB = {
    QueryQuestSingle = function(id, key)
      if id == 777 and key == "name" then return "Une lettre inquiétante" end
      if id == 777 and key == "questLevel" then return 27 end
    end,
    QueryItemSingle = function(id, key)
      if key == "npcDrops" and id == 9201 then return { 3002 } end
      if key == "npcDrops" and id == 2592 then return { 3010, 3011 } end
    end,
    QueryNPCSingle = function(id, key)
      if key == "name" then return ({ [3002] = "Lion des collines", [3010] = "Bandit défias", [3011] = "Voleur défias" })[id] end
    end,
  } } })
  ctx.fire("QUEST_LOG_UPDATE")
  M.Advance(2)
  if H and H.Rebuild then H.Rebuild() end
  _, j = show(9204, bagBtn(0, 4))
  check(has(j, "! Commence une quête : « Une lettre inquiétante » (niv 27)"), "starter with quest level (QuestieDB): " .. j)
  _, j = show(9201, bagBtn(0, 1))
  check(not has(j, "Objet de quête"), "Questie present: its own progress line, ours silent: " .. j)
  check(has(j, "Source : butin de Lion des collines"), "QuestieDB source, no invented rate: " .. j)
  M.shift = true
  _, j = show(2592, bagBtn(1, 1))
  check(has(j, "Aussi : butin de Bandit défias et Voleur défias") or has(j, "Source : butin de Bandit défias et Voleur défias"), "Maj: sources of a reagent: " .. j)
  M.shift = false
  rawset(_G, "QuestieLoader", nil)
  ctx.fire("QUEST_LOG_UPDATE")
  M.Advance(1)

  -- 9. Maj : détails ; sans Maj : indice ; une couleur par sens -------------------------------------------
  local _, jj, raw = show(91001)
  check(has(jj, "(Maj : détails)"), "hint when details exist: " .. jj)
  check(raw[1] and raw[1]:find("^|cff%x%x%x%x%x%xNettement mieux pour toi|r : "), "label coloured, text plain: " .. tostring(raw[1]))
  M.shift = true
  local lS, jS = show(91001)
  check(has(jS, "Comparé à : Gilet usé") and has(jS, "estimation : 1 Agi = 2 PA à distance") and not has(jS, "(Maj : détails)"),
    "Maj: details, no hint: " .. jS)
  check(#lS <= 14, "Maj: bounded")
  M.shift = false

  -- 10. cache par objet + invalidation (sacs, équipement, journal, groupe) ------------------------------
  local st = II.stats
  ctx.fire("BAG_UPDATE_DELAYED")
  local b0 = st.built
  show(91001) show(91001) show(91001)
  check(st.built == b0 + 1, "cache: one build for three hovers (" .. (st.built - b0) .. ")")
  ctx.fire("BAG_UPDATE_DELAYED")
  show(91001)
  check(st.built == b0 + 2, "bag change invalidates")
  M.inventory[5] = { id = 91001, dur = 50, durMax = 60 }
  ctx.fire("PLAYER_EQUIPMENT_CHANGED", 5, true)
  _, j = show(91002)
  check(has(j, "Moins bien"), "equipment change invalidates (old vest now worse): " .. j)
  M.inventory[5] = { id = 91002, dur = 50, durMax = 60 }
  ctx.fire("PLAYER_EQUIPMENT_CHANGED", 5, true)
  local b1 = st.built
  ctx.fire("QUEST_LOG_UPDATE")
  show(91001)
  check(st.built == b1 + 1, "quest log change invalidates")
  b1 = st.built
  -- (5.0 STABLE) a real change of the group: a GROUP_ROSTER_UPDATE that changes nothing is noise the core drops
  M.group = { { name = "Compagnon", class = "WARRIOR", level = 27, online = true } }
  ctx.fire("GROUP_ROSTER_UPDATE")
  show(91001)
  check(st.built == b1 + 1, "group change invalidates")
  M.group = {}
  ctx.fire("GROUP_ROSTER_UPDATE")
  M.Advance(1, 0.1)

  -- 11. données d'objet asynchrones : « chargement… » une fois, puis la vraie réponse ----------------------
  M.AddItem(91030, { name = "Bottes lointaines", classID = 4, subclassID = 2, equipLoc = "INVTYPE_FEET", quality = 2,
    sellPrice = 90, stats = { [A] = 4 } })
  local CI = rawget(_G, "C_Item")
  local holder = (type(CI) == "table" and rawget(CI, "GetItemInfo")) and CI or _G
  local realInfo = rawget(holder, "GetItemInfo")
  local requested = 0
  local realReq = type(CI) == "table" and rawget(CI, "RequestLoadItemDataByID")
  if type(CI) == "table" then rawset(CI, "RequestLoadItemDataByID", function() requested = requested + 1 end) end
  rawset(holder, "GetItemInfo", function(x) if tostring(x):find("91030") then return nil end return realInfo(x) end)
  _, j = show(91030)
  check(has(j, "chargement"), "item data not cached yet: « chargement… »: " .. j)
  _, j = show(91030)
  check(not has(j, "chargement"), "« chargement… » once only: " .. j)
  local pend, pn = II.Pending()
  check(pend[91030] and pn == 1 and (type(CI) ~= "table" or requested >= 1), "load requested, waiting for the server")
  rawset(holder, "GetItemInfo", realInfo)
  if type(CI) == "table" then rawset(CI, "RequestLoadItemDataByID", realReq) end
  ctx.fire("ITEM_DATA_LOAD_RESULT", 91030, true)
  pend, pn = II.Pending()
  check(not pend[91030] and pn == 0, "ITEM_DATA_LOAD_RESULT handled (no more waiting)")
  _, j = show(91030)
  check(has(j, "Mieux") and has(j, "+4 Agi"), "after loading: the real lines: " .. j)

  -- 12. combat : rien de secret comparé, lignes du cache ou calculées sans lire la fiche --------------------
  M.EnterCombat()
  _, j = show(91001)
  check(has(j, "Nettement mieux"), "in combat: lines still there: " .. j)
  _, j = show(91010)
  check(has(j, "Mieux") or has(j, "Nettement mieux"), "in combat: new item computed safely: " .. j)
  _, j = show(9201, bagBtn(0, 1))
  M.LeaveCombat()
  M.Advance(1)

  -- 13. TooltipDataProcessor (Forever / Mainline) : post-appel, jamais sur les infobulles de comparaison ---
  local cbs = {}
  local fakeTDP = { AddTooltipPostCall = function(kind, fn) cbs[kind] = fn end }
  check(II.HookProcessor(fakeTDP, { Item = 0 }), "hooked on TooltipDataProcessor")
  check(II.HookProcessor(fakeTDP, { Item = 0 }) and type(cbs[0]) == "function", "once per processor")
  GameTooltip.__lines = {}
  GameTooltip.__item = 91001
  cbs[0](GameTooltip, { id = 91001, type = 0 })
  check(#GameTooltip.__lines >= 1 and plain(GameTooltip.__lines[1]):find("Nettement mieux", 1, true), "post-call adds the lines")
  GameTooltip.__item = nil
  local shop = CreateFrame("GameTooltip", "TreeAITestShoppingTooltip", UIParent)
  shop.__item = 91001
  cbs[0](shop, { id = 91001, type = 0 })
  check(#shop.__lines == 0, "comparison tooltip left alone")
  -- Forever : GUID of the tooltip data → item location (stack count, soulbound) ; loot window slot
  local CIg, guidTested = rawget(_G, "C_Item"), false
  if type(CIg) == "table" and CIg.GetItemLocation and rawget(_G, "GetLootSlotLink") then
    guidTested = true
    M.SetBagItem(1, 3, 91020, 12)
    M.SetBagItem(2, 2, 91003, 1)
    M.itemGUIDs = { ["Item-0-1"] = { id = 91020, bag = 1, slot = 3 }, ["Item-0-2"] = { id = 91003, bag = 2, slot = 2, bound = true },
      ["Item-0-3"] = { id = 91002, equip = 5 } }
    ctx.fire("BAG_UPDATE_DELAYED")
    local function viaTDP(data)
      GameTooltip.__lines = {}
      cbs[0](GameTooltip, data)
      local out = {}
      for _, l in ipairs(GameTooltip.__lines) do out[#out + 1] = plain(l) end
      return table.concat(out, " | ")
    end
    j = viaTDP({ id = 91020, guid = "Item-0-1", type = 0 })
    check(has(j, "marchand 50 pc (pile ×12 : 6 pa)"), "GUID: stack count from the item location: " .. j)
    j = viaTDP({ id = 91002, guid = "Item-0-3", type = 0 })
    check(not has(j, "Pareil") and not has(j, "Mieux"), "GUID: equipped item, no comparison with itself: " .. j)
    M.group = { { name = "Compagnon", class = "WARRIOR", level = 27, online = true } }
    ctx.fire("GROUP_ROSTER_UPDATE")
    M.Advance(3)
    j = viaTDP({ id = 91003, guid = "Item-0-2", type = 0 })
    check(has(j, "Pour ton groupe : utile à Compagnon") and not has(j, "Donne-le"), "soulbound: no « Donne-le à » (cannot be traded): " .. j)
    M.group = {}
    ctx.fire("GROUP_ROSTER_UPDATE")
    M.Advance(3)
    M.loot = { { id = 91020, name = "Fourrure épaisse", count = 3 } }
    local lootBtn = CreateFrame("Button", nil, UIParent)
    lootBtn.slot = 1
    _, j = show(91020, lootBtn)
    check(has(j, "(pile ×3 : 1 pa 50 pc)"), "loot window: stack from the loot slot: " .. j)
    M.loot, M.itemGUIDs = nil, nil
  end
  check(M.flavor ~= "forever" or guidTested, "forever: GUID / loot paths tested")

  -- 14. un seul propriétaire : ItemInfo coupé → l'ancienne ligne de Gear revient ----------------------------
  T:SetModuleEnabled("ItemInfo", false)
  check(not II.Owns(), "off: not the owner")
  _, j = show(91001)
  check(not has(j, "Nettement mieux pour toi :") and (has(j, "que ce que tu portes") or not (T.modules.Gear and T.modules.Gear._active)),
    "ItemInfo off: nothing from it, Gear's own line back: " .. j)
  T:SetModuleEnabled("ItemInfo", true)
  check(II._active and II.Owns(), "back on")

  -- 15. coût : une infobulle déjà vue ≈ rien ; une nouvelle, bornée ---------------------------------------
  local n = 0
  local function count(fn)
    n = 0
    debug.sethook(function() n = n + 1 end, "", 100)
    fn()
    debug.sethook()
    return n / 10   -- kilo-instructions
  end
  ctx.fire("BAG_UPDATE_DELAYED")
  local cold = count(function() II.Decorate(GameTooltip, { id = 91004 }) end)
  local warm = count(function() II.Decorate(GameTooltip, { id = 91004 }) end)
  if os.getenv("ITEMINFO_COST") then io.stderr:write(("cost cold %.1f warm %.1f kinstr\n"):format(cold, warm)) end
  check(warm <= 8, ("cached hover ≤ 8 kinstr (%.1f)"):format(warm))
  check(cold <= 400, ("first hover ≤ 400 kinstr (%.1f)"):format(cold))
  check(#M.blocked == blocked0, "zero blocked action")
  check(GameTooltip.AddLine == addLine0, "GameTooltip methods untouched")
  GameTooltip.__lines = {}
end
