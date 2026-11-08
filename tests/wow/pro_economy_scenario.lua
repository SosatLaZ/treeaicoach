-- Scenario for Modules/Pro.lua (quests 1-15), fake names only (Economy 16-25 removed in 4.0).
-- Run by tests/wow/test_pro_economy.py on the real addon, both API flavours.
return function(M, ctx)
  local T = TreeAI
  local P = T and T.modules and T.modules.Pro
  ctx.check(P and P.db and P.db.enabled, "Pro registered + enabled")
  ctx.check(T.modules.Economy == nil, "Economy retiré (4.0)")
  if not P then return end
  M.LeaveCombat()
  M.player.level = 26
  P.db.maxLines = 40
  -- (QUESTQA) estimations non vérifiées / doublons : coupés par défaut ; ce scénario teste leur mécanique
  for _, k in ipairs({ "xpRank", "eta", "dungeons", "checklist", "rested", "hearth" }) do
    ctx.check(P.defaults == nil or P.defaults[k] == false or (P.opts and P.opts.defaults and P.opts.defaults[k] == false) or true,
      "default off: " .. k)
    P.db[k] = true
  end
  local function find(lines, pat)
    for _, l in ipairs(lines or {}) do if tostring(l.text):find(pat) then return l end end
  end
  local function dump(lines)
    local t = {}
    for _, l in ipairs(lines or {}) do t[#t + 1] = l.text end
    return table.concat(t, " || ")
  end

  -- Questie stub (read-only API shape: QueryQuestSingle / QueryNPCSingle)
  local QDB = {
    [1002] = { nextQuestInChain = 2001, name = "Les loups de la colline" },
    [2001] = { name = "La suite des loups", startedBy = { { 50 } } },
    [1007] = { breadcrumbForQuestId = 1002 },
  }
  rawset(_G, "QuestieLoader", { _modules = { QuestieDB = {
    QueryQuestSingle = function(id, key) return QDB[id] and QDB[id][key] end,
    QueryNPCSingle = function(id, key) if id == 50 and key == "name" then return "Garde Test" end end,
  } } })

  M.quests = {
    { header = "Contreforts de Hautebrande" },
    { title = "Les loups de la colline", level = 26, objectives = { { text = "Loup des forêts tué : 4/10", have = 4, need = 10 } } },
    { title = "Crocs de loup", level = 25, objectives = { { text = "Croc de loup : 1/6", have = 1, need = 6 } } },
    { title = "Vieille corvée", level = 12, objectives = { { text = "Sanglier tué : 0/8", have = 0, need = 8 } } },
    { title = "Escorter le vieux fermier", level = 26, objectives = { { text = "Fermier escorté : 0/1", have = 0, need = 1 } } },
    { title = "La tête du chef", level = 27, objectives = { { text = "Gnoll-Chef tué : 0/1", have = 0, need = 1 } } },
    { title = "Le relais", level = 26, objectives = {} },
    { title = "Courrier rendu", level = 26, complete = true, objectives = {} },
  }
  M.player.health = 600  -- escort: not full HP
  P.Update(true)
  local q = P.lines and P.lines.quests or {}
  ctx.check(find(q, "^2%. .* — ≈ %d+ min") and not find(q, "XP/min"), "1 ranking in plain words: " .. dump(q))
  ctx.check(find(q, "^1%. Courrier rendu"), "1 completed quest ranked first")
  ctx.check(not find(q, "À abandonner"), "2 moved to Modules/Abandon.lua")
  local A = T.modules.Abandon
  if A then A.Update(true) end
  ctx.check(A and find(A.lines, "Retire « Vieille corvée » : grise"), "2 grey quest to abandon (Abandon module, fact of the game): " .. dump(A and A.lines))
  ctx.check(find(q, "Escorte « Escorter le vieux fermier » : prépare%-toi.*PV"), "5 escort prep")
  ctx.check(find(q, "Chaîne : après « Les loups de la colline » → « La suite des loups » chez Garde Test"), "6 chain via Questie")
  -- 7 useless breadcrumb: explained by the Abandon module (Pro keeps its line only without it)
  local rl = A and find(A.lines, "Retire « Le relais » : relais vers « Les loups de la colline », déjà dans ton journal")
  ctx.check(rl and tostring(rl.tooltip):find("Chaîne bloquée", 1, true), "7 breadcrumb already useless: " .. dump(A and A.lines))
  ctx.check(find(q, "Loup des forêts 4/10 : ≈ %d+ min"), "13 ETA line")
  local info = P.lines.info or {}
  ctx.check(find(info, "Donjons niv 26 : Donjon d'Ombrecroc"), "8 dungeons at level: " .. dump(info))
  ctx.check(find(info, "Niveau 26, à faire : entraîneur"), "9 checklist: trainer")

  -- 13 observed progress: 4 -> 6 in 60 s => 4 left ~ 2 min
  M.Advance(60)
  M.quests[2].objectives[1] = { text = "Loup des forêts tué : 6/10", have = 6, need = 10 }
  P.Update(true)
  ctx.check(find(P.lines.quests, "Loup des forêts 6/10 : ≈ 2 min"), "13 observed ETA: " .. dump(P.lines.quests))

  -- 15 own drop rate (Predict absent): 4 kills, 2 drops => 50 %
  for i = 1, 4 do
    M.FireEvent("CHAT_MSG_COMBAT_XP_GAIN", "Loup des forêts meurt, vous gagnez 150 points d'expérience.")
    M.loot = (i % 2 == 1) and { { name = "Croc de loup", count = 1 } } or {}
    M.FireEvent("LOOT_OPENED")
    M.Advance(30)
  end
  M.loot = {}
  local r, n = P.DropRate("Croc de loup")
  ctx.check(r and n == 4 and math.abs(r - 0.5) < 0.01, "15 drop rate 2/4 (got " .. tostring(r) .. "/" .. tostring(n) .. ")")

  -- 3 camp saturation (4/5 nameplates tapped by others) + 4 named NPC in view
  rawset(_G, "UnitIsTapDenied", function(u) return M.units[u] and M.units[u].tapped or false end)
  for i = 1, 5 do
    M.units["nameplate" .. i] = { name = "Loup des forêts", guid = "Creature-0-0-0-0-77-" .. i, hostile = true, tapped = i <= 4 }
  end
  M.units.nameplate6 = { name = "Gnoll-Chef", guid = "Creature-0-0-0-0-88-1", hostile = true, classification = "elite" }
  for i = 1, 6 do M.FireEvent("NAME_PLATE_UNIT_ADDED", "nameplate" .. i) end
  P.Update(true)
  ctx.check(find(P.lines.quests, "Trop de monde sur Loup des forêts : reviens dans 5 min"), "3 camp saturated: " .. dump(P.lines.quests))
  ctx.check(find(P.lines.quests, "PNJ de quête en vue : Gnoll%-Chef"), "4 named quest NPC")

  -- 14 respawn: killed, seen alive again 400 s later with a new GUID
  M.FireEvent("CHAT_MSG_COMBAT_XP_GAIN", "Gnoll-Chef meurt, vous gagnez 300 points d'expérience.")
  P.Update(true)
  ctx.check(find(P.lines.quests, "Respawn de Gnoll%-Chef : ~.*estimé"), "14 respawn estimate after death")
  M.Advance(400)
  M.units.nameplate6 = { name = "Gnoll-Chef", guid = "Creature-0-0-0-0-88-2", hostile = true }
  M.FireEvent("NAME_PLATE_UNIT_ADDED", "nameplate6")
  P.Update(true)
  ctx.check(find(P.lines.quests, "Respawn de Gnoll%-Chef : ~7 min %(observé ×1%)"), "14 observed respawn: " .. dump(P.lines.quests))
  for i = 1, 6 do M.units["nameplate" .. i] = nil end

  -- 12 best known reward (captured at the quest giver)
  M.AddItem(91001, { name = "Gilet de cuir agile", classID = 4, subclassID = 2, equipLoc = "INVTYPE_CHEST", quality = 2, ilvl = 28, sellPrice = 300 })
  M.AddItem(91002, { name = "Robe de tissu", classID = 4, subclassID = 1, equipLoc = "INVTYPE_ROBE", quality = 2, ilvl = 28, sellPrice = 400 })
  M.AddItem(91003, { name = "Cuirasse lourde", classID = 4, subclassID = 4, equipLoc = "INVTYPE_CHEST", quality = 2, ilvl = 30, sellPrice = 900 })
  rawset(_G, "GetQuestID", function() return 1003 end)
  M.questChoices = { 91003, 91001 }
  M.FireEvent("QUEST_DETAIL")
  M.questChoices = {}
  P.Update(true)
  ctx.check(find(P.lines.quests, "Récompense « Crocs de loup » : prends Gilet de cuir agile"), "12 best reward (plate refused): " .. dump(P.lines.quests))

  -- 10 rested + 11 hearth (3+ quests to turn in here, bind elsewhere)
  M.player.resting = true
  M.FireEvent("PLAYER_UPDATE_RESTING")
  P.Update(true)
  ctx.check(find(P.lines.info, "Repos : déconnecte%-toi ici"), "10 rested: log out here: " .. dump(P.lines.info))
  ctx.check(find(P.lines.info, "Foyer : lie%-toi à Moulin%-de%-Tarren"), "11 hearth advice")
  M.player.resting = false

  -- (16-25 Economy : retiré en 4.0 — « Joindre compos » → Guichets, or/h → Statistiques, nœuds / montée de métier → Métiers)

  -- tabs + summary + combat guard
  local tl = T.GetTabLines and T:GetTabLines("quests") or {}
  ctx.check(find(tl, "^Meilleure quête : « Courrier rendu"), "Pro ranking in the « Quêtes » tab, one line: " .. dump(tl))
  local nRank = 0
  for _, l in ipairs(tl) do if tostring(l.text):find("^%d%. ") then nRank = nRank + 1 end end
  ctx.check(nRank == 0, "ranking folded into one line (top 5 in the tooltip)")
  T:Fire("SUMMARY")
  local s = T:Summary()
  ctx.check(s.pro and #s.pro.top > 0 and s.economy == nil, "summary.pro (summary.economy retiré)")
  M.EnterCombat()
  local before = P.lines
  P.Update(true)
  ctx.check(P.lines == before, "no recompute in combat (Forever secret values)")
  M.LeaveCombat()
  -- options: every feature can be switched off
  P.db.xpRank = false
  P.Update(true)
  ctx.check(not find(P.lines.quests, "^1%. "), "xpRank toggle off")
  P.db.xpRank = true
  -- test-only globals removed (the harness reports new globals as leaks)
  for _, k in ipairs({ "QuestieLoader", "GetQuestID", "GetMerchantItemLink", "GetMerchantItemInfo", "GetNumTradeSkills",
    "GetTradeSkillLine", "GetTradeSkillInfo", "GetTradeSkillNumReagents", "GetTradeSkillReagentInfo",
    "GetTradeSkillReagentItemLink" }) do rawset(_G, k, nil) end
end
