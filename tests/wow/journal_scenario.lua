-- journal_scenario.lua (JOURNAL 4.0.1) — « Nettoyer mon journal » + « les donjons que j'ai déjà faits », on the real
-- addon, forever + modern (+ legacy) flavours. Fake names only (quest ids of dungeon quests are the real ones: data).
-- Level-28 Horde hunter (orc) in a duo, log loaded.
--  A. donjons déjà faits, one source at a time: quests flagged completed (RFC 6/6, WC 2/9), TreeAI's kill history
--     with the Dungeon module OFF (SFK, BOSS_KILL) and ON (BFD, ENCOUNTER_END), a run archived before 4.0.1 (STOCKS),
--     the game's instance lock (RFK all bosses, Gnomeregan 2/5) ; contracts Dungeon.IsDone / QuestDungeon / QuestIDs
--     (faction, race) ; a done dungeon is never planned on its own ; the Donjons cards (« Fait le … », « Quêtes faites
--     x/y », « Aucune quête pour toi »), done ones sorted last.
--  B. the Journal page: reasons from facts only (failed timer, useless breadcrumb, grey, dungeon already done, elite
--     for 3, other continent), the card hint, the Quêtes section, the 2-click abandon (confirm, expiry, combat refusal,
--     selection + Blizzard's abandon target restored), « Tout ce qui est gris » (1 confirmation, then one abandon per
--     click), guided fallback (option off, API absent, the game refused once), never outside a click, zero blocked
--     action, nothing in the chat.
return function(M, ctx)
  local T = TreeAI
  local check = ctx.check
  local D, A, W = T.Dungeon, T.modules.Abandon, T.Window
  check(D and D.IsDone and D.QuestDungeon and D.QuestIDs and D.DoneInfo, "Dungeon contracts: IsDone, QuestDungeon, QuestIDs, DoneInfo")
  check(A and A.Request and A.BatchRequest and A.AbandonNow, "Abandon: Request / BatchRequest / AbandonNow")
  check(W and W._active and W.pageIndex.journal, "Window: « Journal » page registered")
  if not (D and A and W and W.pageIndex.journal) then return end
  M.LeaveCombat()
  M.RunSlash("/tai module Planner off")
  local flavor = M.flavor
  local CQ = rawget(_G, "C_QuestLog")
  local v0 = #M.violations
  local hadIQFC = rawget(_G, "IsQuestFlaggedCompleted")   -- legacy: SetCompleted defines this global
  -- Horde orc, level 28, duo (the friend's log is known: none of these quests)
  local oldFaction = UnitFactionGroup
  UnitFactionGroup = function() return "Horde", "Horde" end
  M.player.race, M.player.raceFile, M.player.level = "Orc", "Orc", 28
  M.group = { { name = "Ami", class = "WARRIOR", level = 28, online = true, quests = {} } }
  D.ForgetDone()

  -- ===================================================================== A. donjons déjà faits
  -- A1. contracts: the quests of a dungeon for THIS character, the dungeon of a quest
  local function ids(key) return table.concat(D.QuestIDs(key), ",") end
  check(#D.QuestIDs("DM") == 0, "Horde: no Deadmines quest (Alliance only): " .. ids("DM"))
  check(ids("BFD") == "6561,6562,6563,6564,6565,6921", "Horde: the 6 Horde BFD quests: " .. ids("BFD"))
  check(D.QuestIDs("SM").set[1049], "orc: « Compendium of the Fallen » (orc / tauren / troll only) is his")
  check(#D.QuestIDs("WC") == 9, "WC for the Horde: 4 Horde + 5 both = 9 (" .. #D.QuestIDs("WC") .. ")")
  check(D.QuestDungeon(5722) and D.QuestDungeon(5722).key == "RFC", "QuestDungeon: 5722 → Gouffre de Ragefeu")
  check(D.QuestDungeon(1102) and D.QuestDungeon(1102).key == "RFK", "QuestDungeon: 1102 → Kraal (quest sort 1717, not the area 491)")
  check(D.QuestDungeon(999999) == nil, "QuestDungeon: unknown quest → nil")

  -- A2. quests flagged completed by the game: RFC all 6 → done ; WC 2 of 9 → not done
  M.SetCompleted({ [5722] = true, [5723] = true, [5724] = true, [5725] = true, [5728] = true, [5761] = true,
    [959] = true, [1486] = true })
  D.ForgetDone()
  local done, info = D.IsDone("RFC")
  check(done and info.src == "quests" and info.qDone == 6 and info.qTotal == 6 and info.label == "Quêtes toutes faites",
    "RFC done from its quests (6/6): " .. tostring(info and info.label))
  done, info = D.IsDone("WC")
  check(not done and info.qDone == 2 and info.qTotal == 9, "WC: 2/9 quests, not done")

  -- A3. TreeAI's history with the Dungeon module OFF: every SFK boss killed (BOSS_KILL) → done « Fait le … »
  T:SetModuleEnabled("Dungeon", false)
  check(not D._active, "Dungeon module off")
  for id = 2748, 2755 do M.FireEvent("BOSS_KILL", id, "boss") end
  done, info = D.IsDone("SFK")
  check(done and info.src == "run" and tostring(info.label):find("^Fait le %d+") and info.kills == 8,
    "SFK done from the kill history, module off: " .. tostring(info and info.label))
  -- A4. module ON, in the instance: only Ghamoo-Ra, Sarevess and Aku'mai (the last boss) → done
  T:SetModuleEnabled("Dungeon", true)
  local oldGII = GetInstanceInfo
  M.instance = true
  GetInstanceInfo = function() return "Profondeurs de Brassenoire", "party", 1, "Normal", 5, 0, false, 48 end
  M.FireEvent("PLAYER_ENTERING_WORLD")
  for _, e in ipairs({ 2916, 2915, 2910 }) do
    M.FireEvent("ENCOUNTER_START", e, "boss", 1, 5)
    M.FireEvent("ENCOUNTER_END", e, "boss", 1, 5, 1)
  end
  done, info = D.IsDone("BFD")
  check(done and info.src == "run" and info.kills == 3, "BFD done: its last boss died (module on, ENCOUNTER_END)")
  M.instance = false
  GetInstanceInfo = oldGII
  M.FireEvent("PLAYER_ENTERING_WORLD")
  -- A5. a run archived before 4.0.1 (boss count only) with every boss: done, dated
  D.char.runs = D.char.runs or {}
  D.char.runs[#D.char.runs + 1] = { key = "STOCKS", start = 1700000000, kills = 5, time = 3000 }
  D.ForgetDone()
  done, info = D.IsDone("STOCKS")
  check(done and info.src == "run" and info.label == "Fait le " .. D.DayText(1700000000), "old archived run: " .. tostring(info and info.label))
  check(D.DayText(1700000000):find("2023$"), "another year is written: " .. tostring(D.DayText(1700000000)))
  -- A6. the game's instance lock: RFK every boss → done ; Gnomeregan 2/5 → « Verrou : 2/5 boss »
  local asked = 0
  local rri = RequestRaidInfo
  RequestRaidInfo = function() asked = asked + 1 return rri() end
  M.savedInstances = { { name = "Kraal de Tranchebauge", mapID = 47, total = 6, done = 6 },
    { name = "Gnomeregan", mapID = 90, total = 5, done = 2 } }
  M.Advance(130, 1)
  M.FireEvent("PLAYER_ENTERING_WORLD")
  M.Advance(1, 0.5)
  check(asked >= 1, "PLAYER_ENTERING_WORLD asks the game for the locks (RequestRaidInfo)")
  RequestRaidInfo = rri
  done, info = D.IsDone("RFK")
  check(done and info.src == "lock" and info.label == "Fait (verrou en cours)", "RFK done from the game's lock: " .. tostring(info and info.label))
  done, info = D.IsDone("GNOMER")
  check(not done and info.label == "Verrou : 2/5 boss", "Gnomeregan partial lock: " .. tostring(info and info.label))
  -- A7. a done dungeon is never planned on its own (duo + its quest in the log)
  M.quests = { { header = "Donjon d'Ombrecroc" },
    { id = 1013, title = "Le livre d'Ur", level = 26, tag = 81, objectives = { { text = "Livre d'Ur : 0/1", have = 0, need = 1 } } },
    { header = "Les Paluns" },
    { id = 4500, title = "Une affaire de famille", level = 27, tag = 81, objectives = { { text = "Boss : 0/1", have = 0, need = 1 } } } }
  local Q = T.modules.Quests
  if Q and Q.Update then Q.Update() end
  D.Invalidate()
  local planned = D.Planned()
  check(not planned, "SFK done: not planned on its own (" .. tostring(planned and planned.key) .. ")")
  check(D.QuestDungeon(4500) == nil, "a dungeon-tagged quest under « Les Paluns » is NOT « Excavations : les Paluns »")

  -- A8. the Donjons page: « Fait le … », « Quêtes faites x/y », « Aucune quête pour toi », done ones last
  W.char.dungeon, W.char.dgAll = nil, true
  W.Open("dungeon")
  local cards, order = {}, {}
  for _, f in ipairs(W.GetWindow().content.__children) do
    if f.name and f.lv and f.q and f:IsVisible() then cards[f.key] = f end
  end
  for _, r in ipairs(W.lastLay.rects) do if r.kind == "dcard" then order[#order + 1] = r.label end end
  local sfk, wc, dm = cards.SFK, cards.WC, cards.DM
  check(sfk and tostring(sfk.done:GetText()):find("Fait le", 1, true) and sfk.check:IsShown(), "SFK card: « Fait le … » + check")
  check(wc and wc.q:GetText() == "Quêtes faites 2/9" and not wc.check:IsShown(), "WC card: « Quêtes faites 2/9 » (" .. tostring(wc and wc.q:GetText()) .. ")")
  check(dm and dm.q:GetText() == "Aucune quête pour toi", "DM card (Alliance quests): « Aucune quête pour toi »")
  check(cards.GNOMER and tostring(cards.GNOMER.done:GetText()):find("Verrou : 2/5 boss", 1, true), "Gnomeregan card: partial lock")
  local firstDone, lastUndone = nil, 0
  local doneSet = { ["Gouffre de Ragefeu"] = true, ["Donjon d'Ombrecroc"] = true, ["Profondeurs de Brassenoire"] = true,
    ["Prison de Hurlevent"] = true, ["Kraal de Tranchebauge"] = true }
  for i, n in ipairs(order) do
    if doneSet[n] then firstDone = firstDone or i else lastUndone = i end
  end
  check(firstDone and firstDone > lastUndone, "done dungeons sorted after the others (" .. table.concat(order, ", ") .. ")")
  W.char.dungeon = "SFK"
  W.Render()
  local hero = W.lastLay.rects[2]
  check(hero and hero.kind == "hero", "SFK detail: hero")
  local deja = false
  for _, r in ipairs(W.lastLay.rects) do if r.kind == "step" and r.label:find("(déjà tué)", 1, true) then deja = true end end
  check(deja, "SFK detail: bosses « (déjà tué) » from the history")
  W.char.dungeon, W.char.dgAll = nil, nil
  W.Close()
  M.Advance(0.5, 0.1)   -- the fade-out ends before the window is opened again

  -- ===================================================================== B. « Nettoyer mon journal »
  local cap = (CQ and CQ.GetMaxNumQuestsCanAccept and CQ.GetMaxNumQuestsCanAccept()) or 20
  local Z = "Mille pointes"
  M.mapRects[1411] = { top = 2000, left = -1000, w = 5000, h = 3500, continent = 1 }
  local function questieStub()
    local QDB = { [5004] = { breadcrumbForQuestId = 5005 } }
    rawset(_G, "QuestieLoader", { _modules = { QuestieDB = {
      QueryQuestSingle = function(id, key) return QDB[id] and QDB[id][key] end,
      QueryNPCSingle = function() return nil end, QueryItemSingle = function() return nil end } } })
  end
  questieStub()
  M.quests = {
    { header = Z },
    { id = 5001, title = "Vieille corvée", level = 17, xp = 85, objectives = { { text = "Sanglier tué : 0/8", have = 0, need = 8 } } },
    { id = 5002, title = "Sangliers de la plaine", level = 19, xp = 120, objectives = { { text = "Sanglier tué : 1/8", have = 1, need = 8 } } },
    { id = 5003, title = "Course contre la montre", level = 27, xp = 1700, failed = true, timer = { 600, 600 },
      objectives = { { text = "Colis livré : 0/1", have = 0, need = 1 } } },
    { id = 5004, title = "Le relais", level = 27, xp = 300, objectives = {} },
    { id = 5005, title = "Le camp", level = 28, xp = 2400, objectives = { { text = "Centaure tué : 2/10", have = 2, need = 10 } } },
    { id = 5007, title = "La tête du chef", level = 29, xp = 2600, elite = true, group = 3,
      objectives = { { text = "Chef tué : 0/1", have = 0, need = 1 } } },
    { header = "Donjon d'Ombrecroc" },
    { id = 1013, title = "Le livre d'Ur", level = 26, xp = 2000, tag = 81, objectives = { { text = "Livre d'Ur : 0/1", have = 0, need = 1 } } },
    { header = "Durotar" },
    { id = 5006, title = "Les murlocs de la côte", level = 27, xp = 2200, map = 1411,
      objectives = { { text = "Murloc tué : 0/10", have = 0, need = 10 } } },
    { header = Z },
  }
  local n = 8
  local total = math.min(25, cap)
  for i = 1, total - n do
    M.quests[#M.quests + 1] = { id = 5100 + i, title = "Quête n°" .. i, level = 28, xp = 2400,
      objectives = { { text = "Ours tué : 3/8", have = 3, need = 8 } } }
  end
  local function idx(qid) for i, q in ipairs(M.quests) do if q.id == qid then return i end end end
  local function inLog(qid) return idx(qid) ~= nil end
  if Q and Q.Update then Q.Update() end
  A.Update(true)
  local cl = A.cleanup or {}
  local byKey = {}
  for _, gr in ipairs(cl.groups or {}) do byKey[gr.key] = gr end
  local function first(key) local gr = byKey[key] return gr and gr.items[1] end
  check(first("failed") and first("failed").q.id == 5003, "Échouées: « Course contre la montre »")
  check(first("blocked") and first("blocked").facts[1].text == "relais vers « Le camp », déjà dans ton journal", "Chaîne bloquée: the breadcrumb")
  check(byKey.grey and #byKey.grey.items == 2 and byKey.grey.items[1].q.id == 5001, "Grises: 2, the least paid first")
  check(first("dungeon") and first("dungeon").q.id == 1013 and first("dungeon").facts[1].text:find("^Donjon d'Ombrecroc : fait le "),
    "Donjon déjà fait: « Le livre d'Ur » (SFK done): " .. tostring(first("dungeon") and first("dungeon").facts[1].text))
  check(first("hard") and first("hard").facts[1].text == "prévue pour 3, vous êtes 2", "Trop dures: elite for 3, duo")
  check(first("far") and first("far").facts[1].text == "autre continent (Kalimdor)", "Autre continent: Durotar is in Kalimdor")
  check(cl.count == 7, "7 to clean, the 18 others have no reason (" .. tostring(cl.count) .. ")")

  -- card hint (log loaded) on the « Prochaine étape » card (one notice at a time: the quest automations question is
  -- answered first, as the player does once)
  T.settings.panel.nowMax = 30
  local Qk = T.modules.Quick
  if Qk and Qk.db then Qk.db.autoAsked = true end
  T:ClearSuggestion("mod:quick:auto")
  W.db.card = true
  W:OnOptionChanged("card")
  W.ShowCard()
  W.RenderCard(true)
  local c = W.GetCard()
  local seen
  for _, r in ipairs(c and c.steps or {}) do
    if r:IsShown() and tostring(r.title:GetText()):find("^Journal : %d+ quêtes, 7 à nettoyer") then seen = r end
  end
  check(seen and seen.badge.num:GetText() == "i", "card: the « journal chargé » notice (badge i)")

  -- the Quêtes page: « Nettoyer mon journal » section → the Journal page
  W.Open("quests")
  W.char.collapsed["q:clean"] = false
  W.Render()
  local qsec, qrow = false, nil
  for _, r in ipairs(W.lastLay.rects) do
    if r.kind == "header" and r.label == "Nettoyer mon journal" then qsec = true end
    if r.kind == "quest" and r.label:find("Course contre la montre", 1, true) then qrow = r end
  end
  check(qsec and qrow, "Quêtes page: « Nettoyer mon journal » section with the quests")

  -- the Journal page
  W.Open("journal")
  local win = W.GetWindow()
  local function rect(label, kind)
    for _, r in ipairs(W.lastLay.rects) do if (not kind or r.kind == kind) and r.label:find(label, 1, true) then return r end end
  end
  check(rect(("Journal : %d/%d quêtes"):format(total, cap), "hero"), "Journal hero: log count")
  for _, h in ipairs({ "Échouées", "Chaîne bloquée", "Grises", "Donjon déjà fait", "Trop dures pour ton groupe actuel",
    "Autre continent ou très loin" }) do
    check(rect(h, "header"), "Journal page: group « " .. h .. " »")
  end
  local H = T.Hub
  local hub
  for _, e in ipairs(H.Entries()) do if e.id == "tab:journal" then hub = e end end
  check(hub and hub.title == "Journal", "Hub: « Journal » entry (window page)")
  local navI, questsI
  for i, p in ipairs(W.PAGES) do if p.id == "journal" then navI = i elseif p.id == "quests" then questsI = i end end
  check(navI == questsI + 1, "navigation: « Journal » right after « Quêtes »")

  local function row(qid)
    for _, f in ipairs(win.content.__children) do
      if f.btn and f.btn.qid == qid and f:IsVisible() then return f end
    end
  end
  local function click(qid)
    local f = row(qid)
    if not f then return false end
    f.btn:Click("LeftButton")
    return true
  end
  local function chat(from)   -- lines of the chat since `from` (the game's red message is not the chat)
    local n = 0
    for i = from + 1, #M.output do if not M.output[i]:find("^%[UIError%]") then n = n + 1 end end
    return n
  end
  local out0, blocked0 = #M.output, #M.blocked

  -- B1. two clicks: confirm, then abandon; the selection and Blizzard's abandon target come back
  if flavor == "forever" then M.selQuest, M.abandonTarget = 5005, 5007 else M.questSel = idx(5005) end
  local selBefore = (flavor == "forever") and M.selQuest or M.questSel
  check(row(5003) and row(5003).btn.state == "go" and row(5003).btn.label:GetText() == "Abandonner", "button « Abandonner »")
  check(click(5003), "click « Abandonner » (Course contre la montre)")
  local r1 = row(5003)
  check(r1 and r1.btn.state == "confirm" and r1.btn.label:GetText() == "Confirmer"
    and tostring(r1.t:GetText()):find("Confirmer l'abandon de « Course contre la montre » ?", 1, true),
    "1st click: « Confirmer l'abandon de « Course contre la montre » ? »")
  check(tostring(win.status:GetText()):find("Confirmer l'abandon", 1, true), "footer asks for the confirmation")
  check(inLog(5003) and #M.abandoned == 0, "1st click: nothing abandoned yet")
  check(click(5003), "click « Confirmer »")
  check(not inLog(5003) and M.abandoned[1] == 5003, "2nd click: « Course contre la montre » abandoned")
  if flavor == "forever" then
    check(M.selQuest == selBefore and M.abandonTarget == 5007, ("selection restored (%s) + Blizzard's abandon target kept (%s)")
      :format(tostring(M.selQuest), tostring(M.abandonTarget)))
  else
    check(M.questSel == selBefore, "Era: quest log selection restored (" .. tostring(M.questSel) .. ")")
  end
  check(tostring(win.status:GetText()):find("« Course contre la montre » abandonnée.", 1, true), "footer: abandoned")
  check(not row(5003), "the abandoned quest leaves the list at once")
  M.Advance(2, 0.5)

  -- B2. the confirmation expires (8 s)
  check(click(5004), "click « Abandonner » (Le relais)")
  M.Advance(9, 1)
  W.Render()
  check(row(5004) and row(5004).btn.state == "go", "after 8 s the confirmation is gone")
  check(click(5004) and inLog(5004) and row(5004).btn.state == "confirm", "a late click asks again (no abandon)")

  -- B3. combat: the armed confirmation is dropped, the button says « Hors combat », a click is refused (red message)
  M.EnterCombat()
  W.Render()
  check(row(5004).btn.state == "combat" and row(5004).btn.label:GetText() == "Hors combat", "combat: « Hors combat »")
  local o1 = #M.output
  click(5004)
  check(inLog(5004), "combat: nothing abandoned")
  local red = false
  for i = o1 + 1, #M.output do if M.output[i]:find("^%[UIError%] Abandon : hors combat seulement") then red = true end end
  check(red, "combat: the game's red message, not the chat")
  M.LeaveCombat()
  W.Render()
  check(row(5004).btn.state == "go", "after combat: back to « Abandonner » (the old confirmation did not survive)")
  -- never outside a click: a direct call refuses, no API touched
  local st = A.AbandonNow(5004, "Le relais")
  check(st == "refused" and inLog(5004), "AbandonNow outside a click: refused")

  -- B4. « Tout ce qui est gris » : one confirmation, then ONE abandon per click
  W.Render()
  local act = win.action
  check(act:IsShown() and act.labelText == "Tout ce qui est gris (2)", "footer: « Tout ce qui est gris (2) » (" .. tostring(act.labelText) .. ")")
  act:Click("LeftButton")
  check(act.labelText == "Confirmer : 2 grises ?" and inLog(5001) and inLog(5002), "batch: 1st click = confirmation only")
  act:Click("LeftButton")
  check(not inLog(5001) and inLog(5002), "batch: 2nd click = the first grey (least paid) abandoned, only one")
  check(tostring(act.labelText):find("^Suivante : « Sangliers"), "batch: « Suivante : « Sangliers… » » (" .. tostring(act.labelText) .. ")")
  act:Click("LeftButton")
  check(not inLog(5002), "batch: 3rd click = the second grey")
  M.Advance(2, 0.5)
  W.Render()
  check(not (act:IsShown() and tostring(act.labelText):find("gris")), "batch done: no grey left")

  -- B5. guided fallback: option off → « Où cliquer ? », the steps in the footer, no API call
  A.db.direct = false
  W.Render()
  check(row(5004).btn.state == "guided" and row(5004).btn.label:GetText() == "Où cliquer ?", "option off: « Où cliquer ? »")
  click(5004)
  check(inLog(5004) and tostring(win.status:GetText()):find("Ouvre ton journal (L) > « Le relais » > « Abandonner ».", 1, true),
    "option off: where to click, nothing abandoned")
  A.db.direct = true
  -- API absent → guided too
  local tbl, key = (flavor == "forever") and CQ or _G, "AbandonQuest"
  local realAb = rawget(tbl, key)
  rawset(tbl, key, nil)
  W.Render()
  check(row(5004).btn.state == "guided", "API absent: « Où cliquer ? »")
  rawset(tbl, key, realAb)
  check(chat(out0) == 0 and #M.blocked == blocked0, "clicks so far: nothing in the chat, no blocked action")
  -- the game refuses once (ADDON_ACTION_FORBIDDEN) → guided for good, nothing abandoned
  rawset(tbl, key, function() M.FireEvent("ADDON_ACTION_FORBIDDEN", "TreeAI", "AbandonQuest()") end)
  W.Render()
  click(5004)
  click(5004)
  check(inLog(5004) and A.db.refused == true and tostring(win.status:GetText()):find("Le jeu a refusé l'abandon direct", 1, true),
    "the game refused: guided from now on")
  W.Render()
  check(row(5004).btn.state == "guided", "after a refusal: « Où cliquer ? »")
  rawset(tbl, key, realAb)
  A.db.refused = nil

  check(#M.blocked == 0, "zero blocked action (" .. #M.blocked .. ")")
  check(#M.violations == v0, "no violation")
  W.Close()
  -- cleanup
  UnitFactionGroup = oldFaction
  rawset(_G, "QuestieLoader", nil)
  M.SetCompleted({})
  if not hadIQFC then rawset(_G, "IsQuestFlaggedCompleted", nil) end
  M.savedInstances = {}
  M.group = {}
  D.ForgetDone()
end
