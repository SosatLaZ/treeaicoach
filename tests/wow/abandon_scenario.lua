-- Scenario for Modules/Abandon.lua (« Nettoyer mon journal », JOURNAL 4.0.1) on the real addon, every API flavour.
-- Fake names only. A level-26 hunter in a duo with a log of 19 quests: grey filler, a useless breadcrumb, an elite for
-- 3, a quest the friend also has, a chain opener (grey but its sequel is not), a lost delivery letter, two exclusive
-- quests, a failed timed quest, a quest ready to turn in. Checks: every reason comes from a FACT of the game (never an
-- estimated duration), the suivi's « À retirer » = the same facts, the « Quêtes » block (click = the Journal page, no
-- chat), the card hint when the log is loaded, /tai abandon, summary.abandon, combat guard, no abandon / no selection
-- outside a click.
return function(M, ctx)
  M.RunSlash("/tai module Planner off")   -- the planner pins its step in « Maintenant » too
  local T = TreeAI
  local A = T and T.modules and T.modules.Abandon
  ctx.check(A and A.db and A.Core and A.Core.Facts and A.Core.Cleanup, "Abandon registered (facts engine)")
  if not A then return end
  ctx.check(not T:IsBeta("Abandon"), "Nettoyer mon journal = fonction sûre (faits du jeu, deux clics)")
  local P = T.modules.Pro
  ctx.check(P and P.Update and not P.AbandonList, "Pro still loaded, its old abandon list removed")
  M.LeaveCombat()
  M.player.level = 26
  local function find(lines, pat, plain)
    for _, l in ipairs(lines or {}) do if tostring(l.text):find(pat, 1, plain) then return l end end
  end
  local function dump(lines)
    local t = {}
    for _, l in ipairs(lines or {}) do t[#t + 1] = l.text end
    return table.concat(t, " || ")
  end
  local function item(title)
    for _, gr in ipairs((A.cleanup or {}).groups or {}) do
      for _, it in ipairs(gr.items) do if it.q.title == title then return it, gr end end
    end
  end
  local function eval(title)
    for _, e in ipairs(A.evals or {}) do if e.q.title == title then return e end end
  end

  -- every abandon / quest log selection API is recorded (none may run here: no click in this scenario)
  local bad, saved = {}, {}
  local FORBID = { "SelectQuestLogEntry", "SetAbandonQuest", "AbandonQuest" }
  for _, fn in ipairs(FORBID) do
    saved[fn] = rawget(_G, fn)
    if saved[fn] then rawset(_G, fn, function() bad[#bad + 1] = fn end) end
  end
  local CQ = rawget(_G, "C_QuestLog")
  local savedCQ = {}
  for _, fn in ipairs({ "SetSelectedQuest", "SetAbandonQuest", "AbandonQuest" }) do
    savedCQ[fn] = CQ and CQ[fn]
    if CQ and CQ[fn] then CQ[fn] = function() bad[#bad + 1] = "C_QuestLog." .. fn end end
  end

  -- Questie stub (QuestieLoader, read only): 4003 opens a chain of 2 (levels 27/28), 4004 is a breadcrumb towards
  -- 4003 (already in the log), 4009 gave a letter (item 9001) and has no objective, 4010 and 4011 exclude each other,
  -- 4002 (grey) opens a grey sequel only, 4008 (grey) opens a level-27 sequel
  local QDB = {
    [4002] = { nextQuestInChain = 4103 }, [4103] = { name = "Corvée suivante", questLevel = 18 },
    [4003] = { nextQuestInChain = 4101, name = "Le camp des gnolls" },
    [4101] = { name = "Le chef gnoll", nextQuestInChain = 4102, questLevel = 27 },
    [4102] = { name = "La fin des gnolls", questLevel = 28 },
    [4004] = { breadcrumbForQuestId = 4003 },
    [4008] = { nextQuestInChain = 4104 }, [4104] = { name = "La suite du marais", questLevel = 27 },
    [4009] = { sourceItemId = 9001 },
    [4010] = { exclusiveTo = { 4011 } }, [4011] = { exclusiveTo = { 4010 } },
  }
  rawset(_G, "QuestieLoader", { _modules = { QuestieDB = {
    QueryQuestSingle = function(id, key) return QDB[id] and QDB[id][key] end,
    QueryNPCSingle = function() return nil end,
    QueryItemSingle = function(id, key) if id == 9001 and key == "name" then return "Lettre scellée" end end,
  } } })

  local Z = "Contreforts de Hautebrande"
  M.quests = {
    { header = Z },
    { id = 4002, title = "Vieille corvée", level = 17, xp = 120, objectives = { { text = "Sanglier tué : 0/8", have = 0, need = 8 } } },
    { id = 4003, title = "Le camp des gnolls", level = 26, xp = 2100, objectives = { { text = "Gnoll tué : 2/10", have = 2, need = 10 } } },
    { id = 4004, title = "Le relais", level = 26, xp = 300, objectives = {} },
    { id = 4005, title = "La tête du chef", level = 27, xp = 2300, elite = true, group = 3,
      objectives = { { text = "Gnoll-Chef tué : 0/1", have = 0, need = 1 } } },
    { id = 4006, title = "Courrier rendu", level = 25, xp = 1800, complete = true, objectives = {} },
    { id = 4007, title = "Les loups de la colline", level = 26, xp = 2100, objectives = { { text = "Loup tué : 4/10", have = 4, need = 10 } } },
    { id = 4008, title = "Le marais d'antan", level = 18, xp = 150, objectives = { { text = "Crocilisque tué : 0/6", have = 0, need = 6 } } },
    { id = 4009, title = "Livraison oubliée", level = 25, xp = 900, objectives = {} },
    { id = 4010, title = "Le choix du guerrier", level = 26, xp = 1500, objectives = { { text = "Ogre tué : 0/5", have = 0, need = 5 } } },
    { id = 4011, title = "Le choix du sage", level = 26, xp = 1500, objectives = { { text = "Ogre tué : 0/5", have = 0, need = 5 } } },
    { id = 4012, title = "Course contre la montre", level = 26, xp = 1700, failed = true, timer = { 600, 600 },
      objectives = { { text = "Colis livré : 0/1", have = 0, need = 1 } } },
  }
  -- filler to reach cap - 1 (the real Forever log holds 40 quests: C_QuestLog.GetMaxNumQuestsCanAccept)
  local CAP = (CQ and CQ.GetMaxNumQuestsCanAccept and CQ.GetMaxNumQuestsCanAccept()) or 20
  for i = 1, CAP - 12 do
    M.quests[#M.quests + 1] = { id = 4200 + i, title = "Quête n°" .. i, level = 26, xp = 2000,
      objectives = { { text = "Ours tué : 5/8", have = 5, need = 8 } } }
  end
  -- a friend in the group (no TreeAI) has « Les loups de la colline » (known log: nothing else)
  M.group = { { name = "Bravo", class = "PRIEST", level = 26, online = true, quests = { [4007] = true } } }
  local hadUOQ = CQ and CQ.IsUnitOnQuest
  if CQ then CQ.IsUnitOnQuest = function(unit, qid) return unit == "party1" and qid == 4007 end end

  A.Update(true)
  local cl = A.cleanup or {}
  ctx.check(A.ctx and A.ctx.logCount == CAP - 1 and A.ctx.logMax == CAP, "log counted: " .. tostring(A.ctx and A.ctx.logCount))
  -- 1. facts → reasons
  local it, gr = item("Vieille corvée")
  ctx.check(it and gr.key == "grey" and it.strong and it.facts[1].text:find("^grise : 120 XP au rendu %(niv 17, toi 26%)"),
    "grey: the game's colour + the real XP reward: " .. tostring(it and it.facts[1].text))
  it, gr = item("Le relais")
  ctx.check(it and gr.key == "blocked" and it.facts[1].text == "relais vers « Le camp des gnolls », déjà dans ton journal",
    "useless breadcrumb (the sequel is in the log): " .. tostring(it and it.facts[1].text))
  it, gr = item("Livraison oubliée")
  ctx.check(it and gr.key == "blocked" and it.facts[1].text == "« Lettre scellée » n'est plus dans tes sacs",
    "delivery letter lost (QuestieDB sourceItemId + bags): " .. tostring(it and it.facts[1].text))
  it, gr = item("Le choix du sage")
  ctx.check(it and gr.key == "dup" and it.facts[1].text:find("même suite que « Le choix du guerrier »", 1, true)
    and not item("Le choix du guerrier"), "two exclusive quests: the second is the duplicate, the first stays")
  it, gr = item("Course contre la montre")
  ctx.check(it and gr.key == "failed" and (it.facts[1].text == "échouée : chrono écoulé"
    or (M.flavor == "legacy" and it.facts[1].text == "échouée")), "failed timed quest: " .. tostring(it and it.facts[1].text))
  it, gr = item("La tête du chef")
  ctx.check(it and gr.key == "hard" and not it.strong and it.facts[1].text == "prévue pour 3, vous êtes 2",
    "elite for 3, we are 2 (game's suggested group vs party size): " .. tostring(it and it.facts[1].text))
  ctx.check(not item("Le marais d'antan"), "grey quest opening a non-grey sequel: kept (no reason)")
  ctx.check(not item("Les loups de la colline"), "the friend has it: no reason to drop it")
  ctx.check(not item("Courrier rendu") and not item("Le camp des gnolls") and not item("Quête n°1"),
    "ready to turn in / chain opener / ordinary quests: never listed (no estimate-only reason)")
  ctx.check(cl.count == 6 and cl.strong == 5, ("6 to clean, 5 on a sure fact (%s / %s)"):format(tostring(cl.count), tostring(cl.strong)))
  -- 2. one truth for « À retirer »: the suivi (Tracker / Todo / Plain) reads the same facts
  local e = eval("Vieille corvée")
  ctx.check(e and e.verdict == "abandon" and e.plain:find("^grise"), "suivi: grey = « À retirer » with the fact")
  e = eval("La tête du chef")
  ctx.check(e and e.verdict ~= "abandon", "suivi: elite for 3 is never « À retirer » (group-dependent)")
  for _, x in ipairs(A.evals or {}) do
    if x.verdict == "abandon" then
      ctx.check(item(x.q.title) and item(x.q.title).strong, "« À retirer » only on a sure fact: " .. tostring(x.q.title))
    end
  end
  -- 3. « Quêtes » block (old text panel): header + lines, click = the Journal page (never the chat)
  local tl = T.GetTabLines and T:GetTabLines("quests") or A.lines
  ctx.check(find(tl, "^Nettoyer mon journal : 6 quêtes"), "quests block header: " .. dump(tl))
  local line = find(tl, "Retire « Vieille corvée » : grise", true)
  ctx.check(line and line.onClick and tostring(line.tooltip):find("Grises : ", 1, true), "block line + tooltip: " .. dump(tl))
  local Pl = T.Plain
  for _, l in ipairs(A.lines or {}) do
    local okp, w = Pl.IsPlain(l.text)
    ctx.check(okp, "plain line (no " .. tostring(w) .. "): " .. tostring(l.text))
  end
  local n0 = #M.output
  if line then T:UserAction(line.onClick) end
  local W = T.Window
  ctx.check(#M.output == n0, "click on a block line: nothing in the chat")
  if W and W._active then ctx.check(W.IsOpen() and W.CurrentPage() == "journal", "click on a block line = the Journal page") end
  if W and W.IsOpen() then W.Close() end
  -- 4. card hint: log loaded (≥ 20 quests) → « Maintenant » line + card notice
  T.settings.panel.nowMax = 30   -- other modules pin lines too: look past the top 5
  local AbM = T.modules.Abandon   -- (4.1 LAYOUT) rappel coupé d'office : le joueur l'allume (Options)
  if AbM and AbM.db then AbM.db.hint = true if AbM.Hint then pcall(AbM.Hint) end end
  local hint = find(T:GetTabLines("now"), "^Journal chargé %(" .. (CAP - 1) .. "/" .. CAP .. "%) : 6 quêtes à nettoyer")
  ctx.check(hint and hint.onClick, "card hint in « Maintenant »: " .. dump(T:GetTabLines("now")))
  if W and W.CARD_NOTICE then
    local nt = W.CARD_NOTICE["Abandon:journal"]
    ctx.check(nt and nt[1] == ("Journal : %d quêtes, 6 à nettoyer"):format(CAP - 1), "card notice text: " .. tostring(nt and nt[1]))
  end

  -- 5. /tai abandon: the Journal page with the window, nothing in the chat; detail keeps the numbers (typed command)
  n0 = #M.output
  M.RunSlash("/tai abandon")
  if W and W._active then
    ctx.check(W.IsOpen() and W.CurrentPage() == "journal" and #M.output == n0, "/tai abandon = the Journal page, no chat")
    W.Close()
  end
  n0 = #M.output
  M.RunSlash("/tai abandon detail")
  local said = table.concat(M.output, "\n", n0 + 1)
  ctx.check(said:find("[À retirer, ", 1, true) and said:find("[Garder, ", 1, true), "detail report keeps the numbers")
  n0 = #M.output
  M.RunSlash("/tai abandon 1")
  said = table.concat(M.output, "\n", n0 + 1)
  ctx.check(said:find("Ouvre ton journal (L)", 1, true), "/tai abandon 1 = where to click: " .. said)
  ctx.check(#bad == 0, "no click here → no abandon, no quest log selection: " .. table.concat(bad, ","))

  -- 6. a short log: no card hint (below 20 quests), the block stays
  for i = #M.quests, 14, -1 do M.quests[i] = nil end
  A.Update(true)
  ctx.check(not find(T:GetTabLines("now"), "^Journal chargé"), "short log: no card hint")
  ctx.check(find(A.lines, "^Nettoyer mon journal"), "short log: the block stays")

  -- 7. combat guard + summary + option
  M.EnterCombat()
  local before = A.evals
  A.Update(true)
  ctx.check(A.evals == before, "no recompute in combat")
  M.LeaveCombat()
  T:Fire("SUMMARY")
  local s = T:Summary()
  local ab = s.abandon or {}
  local sure, soft = 0, 0
  for _, x in ipairs(ab.list or {}) do
    if x.verdict == "abandon" then sure = sure + 1 elseif x.verdict == "later" then soft = soft + 1 end
    ctx.check(x.title and x.why and x.reason, "summary entry: title + why + reason")
  end
  ctx.check(sure == 5 and soft == 1 and ab.strong == 5, ("summary.abandon: %d sure, %d to see"):format(sure, soft))
  A.db.hint = false
  for i = 1, 12 do M.quests[#M.quests + 1] = { id = 4300 + i, title = "Encore " .. i, level = 26, objectives = {} } end
  A.Update(true)
  ctx.check(not find(T:GetTabLines("now"), "^Journal chargé"), "hint option off")
  A.db.hint = true

  -- cleanup (the harness reports new globals as leaks)
  for _, fn in ipairs(FORBID) do rawset(_G, fn, saved[fn]) end
  for fn, f in pairs(savedCQ) do if CQ then CQ[fn] = f end end
  if CQ then CQ.IsUnitOnQuest = hadUOQ end
  rawset(_G, "QuestieLoader", nil)
  M.group = {}
end
