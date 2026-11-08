-- Scenario for Modules/Pro.lua (quests 1-15) + Modules/Economy.lua (16-25), fake names only.
-- Run by tests/wow/test_pro_economy.py on the real addon, both API flavours.
return function(M, ctx)
  local T = TreeAI
  local P = T and T.modules and T.modules.Pro
  local E = T and T.modules and T.modules.Economy
  ctx.check(P and P.db and P.db.enabled, "Pro registered + enabled")
  ctx.check(E and E.db and E.db.enabled, "Economy registered + enabled")
  if not (P and E) then return end
  M.LeaveCombat()
  M.player.level = 26
  P.db.maxLines = 40
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
  ctx.check(A and find(A.lines, "Retire « Vieille corvée » : trop facile maintenant"), "2 grey quest to abandon (Abandon module): " .. dump(A and A.lines))
  ctx.check(find(q, "Escorte « Escorter le vieux fermier » : prépare%-toi.*PV"), "5 escort prep")
  ctx.check(find(q, "Chaîne : après « Les loups de la colline » → « La suite des loups » chez Garde Test"), "6 chain via Questie")
  -- 7 useless breadcrumb: explained by the Abandon module (Pro keeps its line only without it)
  local rl = A and find(A.lines, "Retire « Le relais » : elle ne sert plus à rien")
  ctx.check(rl and tostring(rl.tooltip):find("Pour la retirer"), "7 breadcrumb already useless: " .. dump(A and A.lines))
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

  -- 16 vendor upgrade (remembered) -> also in the checklist (9)
  rawset(_G, "GetMerchantNumItems", function() return 2 end)
  rawset(_G, "GetMerchantItemLink", function(i) return M.ItemLink(i == 1 and 91001 or 91003) end)
  rawset(_G, "GetMerchantItemInfo", function(i) return "x", 134400, 2500, 1, -1, true end)
  M.SetMerchant(true)
  M.SetMerchant(false)
  E.Update(true)
  ctx.check(find(E.lines.hv, "Marchand .*Gilet de cuir agile %(%+%d+ niv%. Torse"), "16 vendor upgrade: " .. dump(E.lines.hv))
  ctx.check(not find(E.lines.hv, "Cuirasse"), "16 plate not proposed to a hunter")
  P.Update(true)
  ctx.check(find(P.lines.info, "amélioration%(s%) chez un marchand"), "9 checklist counts vendor upgrades")

  -- 18 gold/hour by activity + 25 session loot
  E.Update(true)
  M.FireEvent("LOOT_OPENED"); M.player.money = M.player.money + 500; M.FireEvent("PLAYER_MONEY")
  M.FireEvent("QUEST_TURNED_IN", 1008, 1000, 1500); M.player.money = M.player.money + 1500; M.FireEvent("PLAYER_MONEY")
  M.FireEvent("CHAT_MSG_LOOT", "Vous recevez le butin : " .. M.ItemLink(4865) .. "x2.")
  M.FireEvent("CHAT_MSG_LOOT", "Amie reçoit le butin : " .. M.ItemLink(4865) .. ".")
  M.Advance(400)
  E.Update(true)
  ctx.check(find(E.lines.hv, "Or/h : .*butin .*quêtes"), "18 gold/h by source: " .. dump(E.lines.hv))
  ctx.check(find(E.lines.hv, "Butin de la session : .*%(objets 14 pc %+ or 5 pa"), "25 session loot value: " .. dump(E.lines.hv))

  -- 17 sell first + 22 when to sell (bags nearly full)
  for b = 0, 4 do for s = 1, M.bags[b].size do
    if not M.bags[b].slots[s] then M.SetBagItem(b, s, 4865, 1) end
  end end
  M.bags[4].slots[10] = nil
  E.Update(true)
  ctx.check(find(E.lines.hv, "Vends/jette d'abord : Griffe cassée"), "17 sell first: " .. dump(E.lines.hv))
  ctx.check(find(E.lines.hv, "Va vendre maintenant : 1 place"), "22 go sell now")

  -- 23 repair + 24 durability before a dungeon (group, level in a dungeon range)
  M.inventory[5] = { id = 91001, dur = 20, durMax = 100 }
  M.inventory[7] = { id = 91002, dur = 50, durMax = 100 }
  M.group = { { name = "Amie", class = "PRIEST", level = 26, online = true } }
  E.Update(true)
  ctx.check(find(E.lines.info, "Répare dès que possible : un objet à 20 %%, coût estimé"), "23 repair: " .. dump(E.lines.info))
  ctx.check(find(E.lines.info, "Avant donjon : répare %(dur%. %d+ %%"), "24 durability before dungeon: " .. dump(E.lines.info))
  M.group = {}

  -- 19 mail helper: one click = ONE attachment, only with the send-mail tab open
  for b = 0, 4 do for s = 1, M.bags[b].size do
    local sl = M.bags[b].slots[s]
    if sl and sl.id == 4865 then M.bags[b].slots[s] = nil end
  end end
  E.char.mailTo = "Amie"
  -- the button only exists next to a visible mail window (never floating)
  if not rawget(_G, "MailFrame") then
    local mf = CreateFrame("Frame", "MailFrame", UIParent)
    rawset(_G, "MailFrame", mf)
    mf:Show()
  end
  M.FireEvent("MAIL_SHOW")
  local btn = E.mailButton
  ctx.check(btn and btn:IsShown(), "19 mail button shown at the mailbox")
  M.lastUsed = nil
  if btn then btn:Click() end
  ctx.check(M.lastUsed == nil, "19 nothing attached while the send tab is closed")
  local smf = CreateFrame("Frame", "SendMailFrame", UIParent)
  smf:Show()
  if btn then btn:Click() end
  ctx.check(M.lastUsed and M.lastUsed.bag == 0 and M.lastUsed.slot == 6, "19 one mat stack attached on click")
  ctx.check(T._suggestions["Economy:mail"] and T._suggestions["Economy:mail"].text:find("Destinataire : Amie"), "19 instructions")
  M.FireEvent("MAIL_CLOSED")
  ctx.check(not btn:IsShown(), "19 button hidden after the mailbox")

  -- 20 cheapest profession skill-up (classic trade skill API)
  M.AddItem(2589, { name = "Étoffe de lin", classID = 7, sellPrice = 13, stack = 20 })
  rawset(_G, "GetNumTradeSkills", function() return 3 end)
  rawset(_G, "GetTradeSkillLine", function() return "Secourisme", 40, 150 end)
  rawset(_G, "GetTradeSkillInfo", function(i)
    if i == 1 then return "Bandage épais", "medium", 0 elseif i == 2 then return "Bandage en lin", "optimal", 5 end
    return "Vieux bandage", "trivial", 9
  end)
  rawset(_G, "GetTradeSkillNumReagents", function() return 1 end)
  rawset(_G, "GetTradeSkillReagentInfo", function(i) return "Étoffe de lin", 1, 1, 5 end)
  rawset(_G, "GetTradeSkillReagentItemLink", function() return M.ItemLink(2589) end)
  M.FireEvent("TRADE_SKILL_SHOW")
  E.Update(true)
  ctx.check(find(E.lines.hv, "Secourisme : monte avec « Bandage en lin »"), "20 cheapest skill-up: " .. dump(E.lines.hv))
  M.FireEvent("TRADE_SKILL_CLOSE")

  -- 21 gathering node memory
  M.spells[2366] = { name = "Herboristerie", known = true }
  M.FireEvent("UNIT_SPELLCAST_SENT", "player", "Pacifique", "Cast-1", 2366)
  M.FireEvent("UNIT_SPELLCAST_SENT", "player", "Ours", "Cast-2", 75)
  E.Update(true)
  local nl = find(E.lines.info, "Nœuds connus ici : Pacifique ×1")
  ctx.check(nl and not find(E.lines.info, "Ours"), "21 node remembered: " .. dump(E.lines.info))

  -- tabs + summary + combat guard
  local tl = T.GetTabLines and T:GetTabLines("quests") or {}
  ctx.check(find(tl, "^Meilleure quête : « Courrier rendu"), "Pro ranking in the « Quêtes » tab, one line: " .. dump(tl))
  local nRank = 0
  for _, l in ipairs(tl) do if tostring(l.text):find("^%d%. ") then nRank = nRank + 1 end end
  ctx.check(nRank == 0, "ranking folded into one line (top 5 in the tooltip)")
  ctx.check(find(T.GetTabLines and T:GetTabLines("hv") or {}, "Or/h"), "Economy lines in the « HV » tab")
  T:Fire("SUMMARY")
  local s = T:Summary()
  ctx.check(s.pro and #s.pro.top > 0 and s.economy and s.economy.lootValue > 0, "summary.pro / summary.economy")
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
