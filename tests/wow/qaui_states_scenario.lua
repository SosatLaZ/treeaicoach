-- qaui_states_scenario.lua (QA-UI round 9 → FIX-UI regression): the card « Prochaine étape », « Maintenant » and
-- the pastille tooltip in realistic states, with the checks of UI-06 (dead / ghost : the corpse run first, never
-- an outdoor quest step), UI-07 (in a dungeon with 4 : dungeon rows, Donjons page live section first), UI-09
-- (Groupe page in a group, mode sûr), UI-31 (tooltip = the card's first line), chat empty.
-- QAUI_NOQUESTIE=1 : without QuestieDB. QAUI_VERBOSE=1 : dump.
return function(M, ctx)
  local T = TreeAI
  local check = ctx.check
  local W, H = T.Window, T.Hub
  -- (4.1 STRIP) the « Prochaine étape » card is off by default (the « boutons rapides » strip replaced it); this player
  -- turns it on: its content is what is checked here
  if W and W.db and W.db.card == false then W.db.card = true W:OnOptionChanged("card") end
  local Lb = dofile("tests/wow/qaui_lib.lua")
  Lb.instrument(M)
  local verbose = os.getenv("QAUI_VERBOSE") == "1"
  local function p(...)
    if not verbose then return end
    local t = { ... } for i = 1, #t do t[i] = tostring(t[i]) end io.stderr:write(table.concat(t, " "), "\n")
  end
  local noQ = os.getenv("QAUI_NOQUESTIE") == "1"
  local prof = T:ModuleProfile()
  Lb.world(M, T, { noQuestie = noQ })
  Lb.goTo(1441, 46, 50)
  local X, P = T.Export, T.Planner
  if X and X.Rebuild then X:Rebuild() end
  M.Advance(8, 0.1)
  if P and P.Refresh then P:Refresh(true) end
  M.Advance(5, 0.5)
  local wiz = T.GetWizard and T:GetWizard()
  if wiz and wiz:IsShown() then wiz.skip:Click("LeftButton") end
  if T.Tour and T.Tour.active then T.Tour.Finish("skip") end
  W.ShowCard()
  local chat0 = #M.output
  local function state(tag)
    M.Advance(3, 0.5)
    W.RenderCard(true)
    local c = W.GetCard()
    local rows = {}
    p("== " .. tag .. (noQ and " (sans Questie)" or ""))
    if c and c:IsShown() then
      p("  card: " .. tostring(c.hero.title:GetText()) .. " | " .. tostring(c.hero.sub:GetText()))
      for i, r in ipairs(c.steps) do
        if r:IsShown() then
          rows[#rows + 1] = { n = r.badge.num:GetText(), title = Lb.strip(r.title:GetText()), sub = Lb.strip(r.sub:GetText()) }
          p(("    %d [%s] %s || %s"):format(i, tostring(r.badge.num:GetText()), Lb.strip(r.title:GetText()), Lb.strip(r.sub:GetText())))
        end
      end
      if c.empty:IsShown() then p("    empty: " .. tostring(c.empty:GetText())) end
    else p("  card: hidden") end
    for _, e in ipairs(T:GetTabLines("now")) do
      p(("  now p%d [%s] %s%s"):format(e.prio or 1, tostring(e.id or e.source), Lb.strip(e.text), e.right and ("  ‖ " .. Lb.strip(e.right)) or ""))
    end
    local b = H.Summary()
    local t = {}
    for _, l in ipairs(b) do t[#t + 1] = l[1] end
    p("  minimap tooltip: " .. table.concat(t, " / "))
    return c, rows, b[1] and b[1][1] or ""
  end
  local function questRow(rows)
    for _, r in ipairs(rows) do if r.title:find("^Rendre «") or r.title:find("^Prendre «") then return r end end
  end
  local c, rows, tip = state("Mille pointes, en forme")
  check(c and c:IsShown() and questRow(rows), "alive: the card shows the route steps")
  check(not tip:find("Mode sûr", 1, true), "pastille tooltip: not the mode sûr notice (" .. tip .. ")")
  -- combat
  M.target = { exists = true, name = "Hurleur des falaises", level = 29, health = 900, healthMax = 900, hostile = true,
    classification = "normal", creatureType = "Bête", guid = "Creature-0-0-0-0-7108-0001" }
  M.FireEvent("PLAYER_TARGET_CHANGED")
  M.EnterCombat()
  state("en combat")
  M.LeaveCombat()
  M.target = { exists = false }
  M.FireEvent("PLAYER_TARGET_CHANGED")
  -- dead, then ghost
  M.player.dead = true
  M.FireEvent("PLAYER_DEAD")
  c, rows, tip = state("mort")
  check(rows[1] and rows[1].n == "!" and rows[1].title:find("^Mort"), "dead: « Mort : … » first (" .. tostring(rows[1] and rows[1].title) .. ")")
  check(not questRow(rows), "dead: no quest step on the card")
  check(tip:find("Mort", 1, true), "dead: pastille tooltip says it (" .. tip .. ")")
  M.player.dead, M.player.ghost = false, true
  M.FireEvent("PLAYER_ALIVE")
  c, rows, tip = state("fantôme (course au corps)")
  check(rows[1] and rows[1].n == "!" and (rows[1].title:find("Cadavre", 1, true) or rows[1].title:find("corps", 1, true)),
    "ghost: the corpse run first (" .. tostring(rows[1] and rows[1].title) .. ")")
  check(not questRow(rows), "ghost: no quest step on the card")
  check(tostring(c.hero.sub:GetText()):find("course au corps", 1, true), "ghost: hero says « course au corps »")
  W.Open("now")
  local guidedSteps = 0
  -- (4.1 LAYOUT) « Maintenant » = la page Quêtes : pas de « Prochaine étape » au-dessus de la course au corps
  for _, r in ipairs(W.lastLay.rects) do if r.kind == "header" and r.label == "Prochaine étape" then guidedSteps = guidedSteps + 1 end end
  check(guidedSteps == 0, "ghost: « Maintenant » shows no route steps above the corpse line")
  W.Close()
  M.player.ghost = false
  M.FireEvent("PLAYER_UNGHOST")
  c, rows = state("vivant de nouveau")
  check(questRow(rows), "alive again: route steps back")
  -- bags full
  for b = 0, 4 do for s = 1, M.bags[b].size do if not M.bags[b].slots[s] then M.SetBagItem(b, s, 4865, 1) end end end
  M.FireEvent("BAG_UPDATE", 0); M.FireEvent("BAG_UPDATE_DELAYED")
  state("sacs pleins")
  -- quest log full
  for i = 1, 30 do M.quests[#M.quests + 1] = { id = 9000 + i, title = "Quête remplissage " .. i, level = 27, map = 1413,
    objectives = { { text = "Truc : 0/5", have = 0, need = 5, done = false, type = "item" } } } end
  M.FireEvent("QUEST_LOG_UPDATE")
  state("journal plein")
  W.Open("quests")
  p("  quests status: " .. tostring(W.GetWindow().status:GetText()))
  W.Close()
  -- flight master
  local oldTaxi = rawget(_G, "NumTaxiNodes")
  rawset(_G, "NumTaxiNodes", function() return 3 end)
  M.FireEvent("TAXIMAP_OPENED", 1)
  state("maître de vol ouvert")
  M.FireEvent("TAXIMAP_CLOSED")
  rawset(_G, "NumTaxiNodes", oldTaxi)
  -- AH
  if M.SetAuctionHouse then M.SetAuctionHouse(true) end
  state("hôtel des ventes ouvert")
  W.Open("hv")
  p("  HV page: " .. table.concat(Lb.texts(W.GetWindow().content), " | "))
  if M.SetAuctionHouse then M.SetAuctionHouse(false) end
  W.Close()
  -- dungeon with 4
  M.group = {
    { name = "Amie", class = "PRIEST", level = 27, health = 700, healthMax = 800, online = true },
    { name = "Compagnon", class = "WARRIOR", level = 28, health = 1200, healthMax = 1300, online = true },
    { name = "Camarade", class = "MAGE", level = 27, health = 600, healthMax = 700, online = true },
  }
  M.FireEvent("GROUP_ROSTER_UPDATE")
  -- Groupe page in a group (mode sûr : module Groupe coupé) — UI-09
  W.Open("group")
  local Gm = T.modules.Group
  local txt = table.concat(Lb.texts(W.GetWindow().content), " | ")
  p("  Groupe page (dehors): " .. txt)
  check(not txt:find("Seul pour l'instant", 1, true), "Groupe page in a group: never « Seul pour l'instant »")
  if Gm and not Gm._active then
    local btn
    for _, r in ipairs(W.lastLay.rects) do if r.kind == "button" and r.label == "Allumer « Groupe »" then btn = r end end
    check(txt:find("coupé", 1, true) and btn, "Groupe page: module off → says so + « Allumer « Groupe » »")
  end
  W.Close()
  local oldII, oldGI = rawget(_G, "IsInInstance"), rawget(_G, "GetInstanceInfo")
  rawset(_G, "IsInInstance", function() return true, "party" end)
  rawset(_G, "GetInstanceInfo", function() return "Profondeurs de Brassenoire", "party", 1, "Normal", 5, 0, false, 48 end)
  local oldZone = M.player.zone
  M.player.zone = "Profondeurs de Brassenoire"
  M.FireEvent("ZONE_CHANGED_NEW_AREA")
  M.FireEvent("PLAYER_ENTERING_WORLD", false, false)
  c, rows = state("donjon à 4 (Brassenoire)")
  check(c.mode == "dungeon", "dungeon: card in dungeon mode")
  check(not questRow(rows), "dungeon: no outdoor quest step on the card")
  check(c.hero.title:GetText() == "Profondeurs de Brassenoire", "dungeon: hero = the instance (" .. tostring(c.hero.title:GetText()) .. ")")
  local D = T.Dungeon
  if D and not D._active then
    local off
    for _, r in ipairs(rows) do if r.title:find("Suivi en donjon coupé", 1, true) then off = r end end
    check(off, "dungeon, module off: one row to turn it on")
  elseif D then
    local live
    for _, r in ipairs(rows) do if r.title:find("Prochain", 1, true) or r.title:find("Quête", 1, true) or r.title:find("Boss", 1, true) then live = r end end
    check(live, "dungeon: next boss / quest items on the card")
  end
  W.Open("dungeon")
  local win = W.GetWindow()
  p("  Donjons page title « " .. tostring(win.pageTitle:GetText()) .. " » action « " .. tostring(win.action:IsShown() and win.action.labelText) .. " »")
  for _, r in ipairs(W.lastLay.rects) do p("    " .. r.kind .. " " .. r.label) end
  check(W.char.dungeon == "BFD", "Donjons page: the dungeon you are in opens by itself (" .. tostring(W.char.dungeon) .. ")")
  if D and D._active then
    local iLive, iHero
    for i, r in ipairs(W.lastLay.rects) do
      if r.kind == "header" and r.label == "En direct" and not iLive then iLive = i end
      if r.kind == "hero" and not iHero then iHero = i end
    end
    check(iLive and iHero and iLive < iHero, "Donjons page: « En direct » before the dungeon sheet")
  end
  -- « < Tous les donjons » stays possible inside
  W.char.dungeon = nil
  W.Render()
  check(W.char.dungeon == nil, "inside: the list can still be opened")
  W.Open("group")
  p("  Groupe page: sub « " .. tostring(win.pageSub:GetText()) .. " »")
  for _, r in ipairs(W.lastLay.rects) do p("    " .. r.kind .. " " .. r.label) end
  W.Close()
  rawset(_G, "IsInInstance", oldII)
  rawset(_G, "GetInstanceInfo", oldGI)
  M.player.zone = oldZone
  M.group = {}
  M.FireEvent("GROUP_ROSTER_UPDATE")
  M.FireEvent("ZONE_CHANGED_NEW_AREA")
  local nChat = 0
  for i = chat0 + 1, #M.output do
    p("  chat: " .. M.output[i])
    -- (« Synchro du groupe en pause » : Core.lua, ligne de FIX-SYSTEMS (QA S16), hors de ce test)
    if not M.output[i]:find("^%[UIError%]") and not M.output[i]:find("Synchro du groupe en pause", 1, true) then nChat = nChat + 1 end
  end
  check(nChat == 0, "states: nothing in the chat (" .. nChat .. ")")
end
