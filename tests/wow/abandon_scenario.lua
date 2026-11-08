-- Scenario for Modules/Abandon.lua (« Quêtes à retirer ») on the real addon, both API flavours. Fake names only.
-- Fake log of 19/20 quests: grey filler, an elite for 3, a useless breadcrumb, a quest a friend also has,
-- a chain opener, a quest ready to turn in. Checks verdicts, explanations, the « À retirer » block, the click
-- (instructions only, never abandons / never selects in the quest log), /tai abandon, combat guard, summary.
return function(M, ctx)
  M.RunSlash("/tai module Planner off")   -- the planner pins its step in « Maintenant » too
  local T = TreeAI
  local A = T and T.modules and T.modules.Abandon
  ctx.check(A and A.db and A.db.enabled and A.Core, "Abandon registered + enabled")
  if not A then return end
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
  local function byTitle(title)
    for _, e in ipairs(A.evals or {}) do if e.q.title == title then return e end end
  end

  -- forbidden on the quest log: record any call
  local bad = {}
  local saved = {}
  local FORBID = { "SelectQuestLogEntry", "SetAbandonQuest", "AbandonQuest", "QuestLogPushQuest" }
  for _, fn in ipairs(FORBID) do
    saved[fn] = rawget(_G, fn)
    rawset(_G, fn, function() bad[#bad + 1] = fn end)
  end
  local CQ = rawget(_G, "C_QuestLog")
  local savedSel = CQ and CQ.SetSelectedQuest
  if CQ then CQ.SetSelectedQuest = function() bad[#bad + 1] = "C_QuestLog.SetSelectedQuest" end end

  -- Questie stub: 1003 opens a chain of 2, 1004 is a breadcrumb towards 1003 (already in the log)
  local QDB = {
    [1003] = { nextQuestInChain = 2001, name = "Le camp des gnolls" },
    [2001] = { name = "Le chef gnoll", nextQuestInChain = 2002, questLevel = 27 },
    [2002] = { name = "La fin des gnolls", questLevel = 28 },
    [1004] = { breadcrumbForQuestId = 1003 },
  }
  rawset(_G, "QuestieLoader", { _modules = { QuestieDB = {
    QueryQuestSingle = function(id, key) return QDB[id] and QDB[id][key] end,
    QueryNPCSingle = function() return nil end,
  } } })

  local Z = "Contreforts de Hautebrande"
  M.quests = {
    { header = Z },
    { title = "Vieille corvée", level = 17, xp = 120, objectives = { { text = "Sanglier tué : 0/8", have = 0, need = 8 } } },  -- 1002
    { title = "Le camp des gnolls", level = 26, xp = 2100, objectives = { { text = "Gnoll tué : 2/10", have = 2, need = 10 } } }, -- 1003
    { title = "Le relais", level = 26, xp = 300, objectives = {} },                                                                 -- 1004
    { title = "La tête du chef", level = 27, xp = 2300, elite = true, group = 3,
      objectives = { { text = "Gnoll-Chef tué : 0/1", have = 0, need = 1 } } },                                                    -- 1005
    { title = "Courrier rendu", level = 25, xp = 1800, complete = true, objectives = {} },                                        -- 1006
    { title = "Les loups de la colline", level = 26, xp = 2100, objectives = { { text = "Loup tué : 4/10", have = 4, need = 10 } } }, -- 1007
  }
  -- filler to reach cap - 1 (19/20 on the old mock; FIDELITY: the real Forever log holds 40 quests, the game says so
  -- through C_QuestLog.GetMaxNumQuestsCanAccept — Constants.QuestLogConsts.MAXIMUM_NUM_QUESTS_LOG_CAN_ACCEPT)
  local CQx = rawget(_G, "C_QuestLog")
  local CAP = (CQx and CQx.GetMaxNumQuestsCanAccept and CQx.GetMaxNumQuestsCanAccept()) or 20
  local FULL = ("Journal %d/%d"):format(CAP - 1, CAP)
  for i = 1, CAP - 7 do
    M.quests[#M.quests + 1] = { title = "Quête n°" .. i, level = 26, xp = 2000, objectives = { { text = "Ours tué : 5/8", have = 5, need = 8 } } }
  end
  -- a friend in the group (no TreeAI) has « Les loups de la colline »
  M.group = { { name = "Bravo", class = "PRIEST", level = 26, online = true } }
  local hadUOQ = CQ and CQ.IsUnitOnQuest
  if CQ then CQ.IsUnitOnQuest = function(unit, qid) return unit == "party1" and qid == 1007 end end

  A.Update(true)
  ctx.check(A.ctx and A.ctx.logCount == CAP - 1 and A.ctx.logMax == CAP, "log " .. FULL .. " counted")
  local grey = byTitle("Vieille corvée")
  ctx.check(grey and grey.verdict == "abandon", "grey filler → abandon")
  ctx.check(grey and grey.text:find("^Abandonne « Vieille corvée » : grise %(tu as 9 niveaux de plus%), %d+ XP seulement pour ~%d+ min"),
    "grey explanation: " .. tostring(grey and grey.text))
  ctx.check(grey and grey.text:find("XP/min contre %d+ XP/min pour tes autres quêtes"), "compared with the other quests")
  local relay = byTitle("Le relais")
  ctx.check(relay and relay.verdict == "abandon" and relay.text:find("relais inutile", 1, true), "useless breadcrumb → abandon")
  local chain = byTitle("Le camp des gnolls")
  ctx.check(chain and chain.verdict == "keep" and chain.text:find("elle ouvre une suite (« Le chef gnoll », 2 quêtes", 1, true),
    "chain opener kept: " .. tostring(chain and chain.text))
  local elite = byTitle("La tête du chef")
  ctx.check(elite and elite.verdict ~= "keep" and elite.text:find("prévue pour 3 (vous êtes 2)", 1, true),
    "elite for 3, we are 2: " .. tostring(elite and elite.text))
  local friend = byTitle("Les loups de la colline")
  if hadUOQ ~= nil or (CQ and CQ.GetInfo) then   -- IsUnitOnQuest: modern API only
    ctx.check(friend and friend.verdict == "keep" and friend.text:find("Bravo l'a aussi", 1, true), "friend has it → keep: "
      .. tostring(friend and friend.text))
  end
  local ready = byTitle("Courrier rendu")
  ctx.check(ready and ready.verdict == "keep" and ready.score >= 90, "ready to turn in → keep")
  ctx.check(A.evals[1].score <= A.evals[#A.evals].score, "sorted worst first")

  -- « À retirer » block in the « Quêtes » tab + pinned alert (log nearly full)
  local tl = T.GetTabLines and T:GetTabLines("quests") or A.lines
  ctx.check(find(tl, FULL .. " : %d quêtes? à retirer"), "header (nearly full): " .. dump(tl))
  local line = find(tl, "Retire « Vieille corvée » : trop facile maintenant", true)
  ctx.check(line and line.onClick and tostring(line.tooltip):find("ouvre ton journal (L)", 1, true), "abandon line + tooltip: " .. dump(tl))
  local Pl = T.Plain
  for _, l in ipairs(A.lines or {}) do
    local okp, w = Pl.IsPlain(l.text)
    ctx.check(okp, "plain abandon line (no " .. tostring(w) .. "): " .. tostring(l.text))
  end
  T.settings.panel.nowMax = 30   -- other modules pin lines too: look past the top 5
  ctx.check(find(T.GetTabLines and T:GetTabLines("now") or {}, FULL, true), "pinned in « Maintenant » " .. dump(T:GetTabLines("now")))

  -- click = instructions only
  local n0 = #M.output
  if line and line.onClick then line.onClick() end
  local said = table.concat(M.output, "\n", n0 + 1)
  ctx.check(said:find("Retire « Vieille corvée » : trop facile maintenant", 1, true) and said:find("puis « Abandonner » et confirme", 1, true),
    "click prints why + how: " .. said)

  -- /tai abandon (full report) and /tai abandon 1
  n0 = #M.output
  M.RunSlash("/tai abandon")
  said = table.concat(M.output, "\n", n0 + 1)
  ctx.check(said:find(FULL, 1, true) and said:find("Libérer de la place : 1. ", 1, true), "/tai abandon report")
  ctx.check(said:find("Garde « ", 1, true) and said:find("Retire « ", 1, true) and not said:find("XP/min", 1, true), "every quest has a plain verdict: " .. said)
  n0 = #M.output
  M.RunSlash("/tai abandon detail")
  said = table.concat(M.output, "\n", n0 + 1)
  ctx.check(said:find("[Garder, ", 1, true) and said:find("[À retirer, ", 1, true), "detail report keeps the numbers")
  n0 = #M.output
  M.RunSlash("/tai abandon 1")
  said = table.concat(M.output, "\n", n0 + 1)
  ctx.check(said:find("Pour la retirer", 1, true), "/tai abandon 1 = how-to")
  ctx.check(#bad == 0, "never abandons / never touches the quest log selection: " .. table.concat(bad, ","))

  -- freeing room: with a short log the header is calm and nothing is pinned
  for i = #M.quests, 9, -1 do M.quests[i] = nil end
  A.Update(true)
  ctx.check(find(A.lines, "— À retirer —", true) and not A.lines[1].pin, "short log: calm header: " .. dump(A.lines))

  -- combat guard + summary + option
  M.EnterCombat()
  local before = A.evals
  A.Update(true)
  ctx.check(A.evals == before, "no recompute in combat")
  M.LeaveCombat()
  T:Fire("SUMMARY")
  local s = T:Summary()
  ctx.check(s.abandon and s.abandon.count >= 2 and s.abandon.list[1].why, "summary.abandon")
  A.db.showLater = false
  A.Update(true)
  ctx.check(not find(A.lines, "^Plus tard"), "showLater off")
  A.db.showLater = true

  -- cleanup (the harness reports new globals as leaks)
  for _, fn in ipairs(FORBID) do rawset(_G, fn, saved[fn]) end
  if CQ then CQ.SetSelectedQuest, CQ.IsUnitOnQuest = savedSel, hadUOQ end
  rawset(_G, "QuestieLoader", nil)
  M.group = {}
end
