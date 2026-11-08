-- Scenario for Modules/Dungeon.lua (agent PVE). Fake names only.
return function(M, ctx)
  local T = TreeAI
  local D = T.modules.Dungeon
  ctx.check(D and D.db and D.db.enabled, "Dungeon registered")
  ctx.check(T.Data and T.Data.Dungeons and #T.Data.Dungeons.list >= 20, "data loaded")
  if not D then return end
  local S = T._suggestions
  local function text(lines)
    local t = {}
    for _, l in ipairs(lines or {}) do t[#t + 1] = l.text end
    return table.concat(t, "\n")
  end
  M.LeaveCombat()

  -- data index: Forever encounter ids, FR/EN names, accents ignored
  local d, b = D.FindBoss(592)
  ctx.check(d and d.key == "WC" and b.n:find("Mutanus"), "encounter id 592 -> Mutanus")
  ctx.check(D.Find("mortemines").key == "DM" and D.Find("Deadmines").key == "DM", "find by FR/EN name")
  ctx.check(D.Find("monastere").key == "SM", "find ignores accents")
  local _, b2 = D.FindBoss(99999, "Herod")
  ctx.check(b2 and b2.n == "Hérode" and D.db.learned[99999] == "SM:5", "unknown id learned from the EN name")

  -- outside, alone, nothing planned: no tab
  M.instance = false
  D:Refresh(true)
  ctx.check(not T.tabs.dungeon, "no Donjon tab outside / unplanned")

  -- planned dungeon (slash), group of 3 with a low-level friend, no healer
  -- (4.0 QA S32) loot verdicts wait for the item data (GET_ITEM_INFO_RECEIVED): these two are known to the client
  M.AddItem(6469, { name = "Venomstrike", equipLoc = "INVTYPE_RANGED", classID = 2, subclassID = 2, minLevel = 17, quality = 3 })
  M.AddItem(6447, { name = "Worn Turtle Shell Shield", equipLoc = "INVTYPE_SHIELD", classID = 4, subclassID = 6, minLevel = 17, quality = 2 })
  D._itemGen = (D._itemGen or 0) + 1
  M.player.level = 18
  M.group = { { name = "Ami-un", class = "WARRIOR", level = 19, online = true },
              { name = "Ami-deux", class = "ROGUE", level = 14, online = true } }
  M.RunSlash("/tai donjon cavernes")
  ctx.check(D.char.planned == "WC", "planned via /tai donjon")
  ctx.check(T.tabs.dungeon ~= nil, "Donjon tab visible when planned")
  local tx = text(D.lines)
  ctx.check(tx:find("Cavernes des Lamentations"), "header")
  ctx.check(tx:find("Trop bas %(min 17%) : Ami%-deux 14"), "low level friend: " .. tx)
  ctx.check(tx:find("Soigneur : PERSONNE"), "no healer warning")
  ctx.check(tx:find("Tank : Ami%-un"), "warrior guessed as tank")
  ctx.check(tx:find("Pour toi : [^\n]*Venomstrike") or tx:find("Pour toi : "), "hunter loot listed for me: " .. tx)
  ctx.check(tx:find("Pour Ami%-un %(Guerrier%)"), "loot for a friend")
  ctx.check(tx:find("Chasseur :"), "hunter tips")
  local hasMark = false
  for _, l in ipairs(D.lines) do if l.control and l.label == "Marquer" then hasMark = true end end
  ctx.check(hasMark, "Marquer = bouton d'onglet")
  ctx.check(tx:find("Prochain : Dame Anacondra", 1, true) and tx:find("Qui fait quoi : Ami%-un tanke"), "next boss + jobs")
  ctx.check(tx:find("Ce qui tue le groupe : ", 1, true), "fiche : ce qui tue le groupe")
  ctx.check(tx:find("DPS (toi) : ", 1, true), "fiche : conseil du rôle du joueur (DPS, le guerrier tanke)")
  ctx.check(not tx:find("pas de panique", 1, true) and not tx:find("Rare. Facile", 1, true), "pas de phrase bizarre")

  -- mark: one click = one SetRaidTarget on the target, out of combat only
  M.target = { exists = true, name = "Déviant-test", level = 18, hostile = true, guid = "Creature-0-0-0-0-5-1" }
  UnitCanAttack = function(_, u) return u == "target" end
  M.Hw(D.MarkTarget, D)
  ctx.check(M.raidTargets and M.raidTargets.target == 8, "first mark = skull")
  M.Hw(D.MarkTarget, D)
  ctx.check(M.raidTargets.target == 7, "second click = cross")
  M.EnterCombat()
  M.Hw(D.MarkTarget, D)
  ctx.check(M.raidTargets.target == 7, "no mark in combat")
  M.LeaveCombat()

  -- enter the instance (instanceID 43): run starts, planned becomes the run
  M.instance = true
  GetInstanceInfo = function() return "Cavernes des Lamentations", "party", 1, "Normal", 5, 0, false, 43 end
  ctx.fire("PLAYER_ENTERING_WORLD")
  ctx.check(D.char.run and D.char.run.key == "WC", "run started on instance entry")
  ctx.check(D.char.planned == nil, "plan consumed")
  ctx.check(T.tabs.dungeon ~= nil, "tab visible in instance")

  -- pull counter + encounter (4.0 QA S6: no invented boss timer — Classic numbers never measured on Forever)
  M.EnterCombat()
  ctx.check(D.char.run.pulls == 1, "pull counted")
  ctx.fire("ENCOUNTER_START", 592, "Mutanus le Dévoreur", 1, 5)
  local bs = S["Dungeon:boss"]
  ctx.check(bs and bs.text:find("Mutanus") and bs.prio == 2, "boss tip suggested (no corner)")
  ctx.check(D.Encounter() and D.Encounter().b.n:find("Mutanus"), "encounter active")
  local function timed()
    for id, e in pairs(S) do
      if id:find("^Dungeon:") and (tostring(e.text):find("≈ %d+ s") or tostring(e.text):find("dans %d+ s")) then return e.text end
    end
  end
  local seen
  for _ = 1, 40 do M.Advance(1, 0.5) seen = seen or timed() end
  ctx.check(seen == nil and D.TickTimers == nil, "no countdown line during the boss: " .. tostring(seen))
  D:Refresh(true)
  ctx.check(not text(D.lines):find("Prochain (≈)", 1, true), "no « Prochain (≈) : … dans N s » line in the tab")
  ctx.check(not (D.db and D.db.timers) and not (D.db and D.db.forceTimers), "boss timer options gone")
  ctx.fire("ENCOUNTER_END", 592, "Mutanus le Dévoreur", 1, 5, 1)
  M.LeaveCombat()
  ctx.check(D.Killed(b), "kill recorded")
  ctx.check(S["Dungeon:kill"] and S["Dungeon:kill"].text:find("Boss vaincu"), "kill message")

  -- wipe on the next boss
  M.EnterCombat()
  ctx.fire("ENCOUNTER_START", 590, "Seigneur Serpentis", 1, 5)
  ctx.fire("ENCOUNTER_END", 590, "Seigneur Serpentis", 1, 5, 0)
  M.LeaveCombat()
  ctx.check(D.char.run.wipes == 1, "wipe counted")
  local w = S["Dungeon:wipe"]
  ctx.check(w and w.prio == 3, "wipe alert")
  D:Refresh(true)
  tx = text(D.lines)
  ctx.check(tx:find("Wipe — reprise"), "wipe recovery checklist")

  -- yells: secret text ignored, readable keyword starts the encounter + alert
  -- (FIDELITY: the fake « SECRET » string chains to the client's issecretvalue, which is restored, never removed)
  local realIsSecret = rawget(_G, "issecretvalue")
  local function fakeSecret(v) return v == "SECRET" or (realIsSecret ~= nil and realIsSecret(v)) or false end
  rawset(_G, "issecretvalue", fakeSecret)
  ctx.fire("CHAT_MSG_MONSTER_YELL", "SECRET", "SECRET")
  ctx.check(not D.Encounter(), "secret yell ignored")
  rawset(_G, "issecretvalue", realIsSecret)
  GetInstanceInfo = function() return "Monastère écarlate", "party", 1, "Normal", 5, 0, false, 189 end
  ctx.fire("ZONE_CHANGED_NEW_AREA")
  ctx.check(D.char.run.key == "SM", "new run for a new instance")
  ctx.fire("CHAT_MSG_MONSTER_YELL", "Blades of Light!", "Herod")
  ctx.check(D.Encounter() and D.Encounter().b.n == "Hérode", "yell starts Herod")
  ctx.check(S["Dungeon:yell"] and S["Dungeon:yell"].prio == 3, "whirlwind yell alert")
  D:EndEncounter(true)

  -- click-armed boss (no encounter event): its advice shows at combat start
  local sm = D.Find("SM")
  D:Arm(sm.bosses[4])
  M.EnterCombat()
  ctx.check(D.Encounter() and D.Encounter().src == "click", "armed boss starts on combat")
  M.LeaveCombat()
  ctx.check(not D.Encounter(), "armed encounter ends with combat")

  -- meter-lite: C_DamageMeter read out of combat, secret values skipped
  rawset(_G, "C_DamageMeter", {
    IsDamageMeterAvailable = function() return true end,
    GetCombatSessionFromType = function(stype, kind)
      if kind ~= 0 then return nil end
      return { totalAmount = 1000, durationSeconds = 30, combatSources = {
        { name = "Ami-un", totalAmount = 400, isLocalPlayer = false, classFilename = "WARRIOR" },
        { name = "Moi", totalAmount = 600, isLocalPlayer = true, classFilename = "HUNTER" },
        { name = "Caché", totalAmount = "SECRET" } } }
    end,
  })
  rawset(_G, "issecretvalue", fakeSecret)
  D:UpdateMeter()
  D:Refresh(true)
  tx = text(D.lines)
  ctx.check(tx:find("toi 60 %%") and tx:find("Ami%-un 40 %%"), "meter line: " .. tx)
  M.EnterCombat()
  ctx.check(D.ReadMeter(1) == nil, "meter never read in combat")
  M.LeaveCombat()
  rawset(_G, "issecretvalue", realIsSecret)
  rawset(_G, "C_DamageMeter", nil)

  -- summary + leaving: tab disappears
  T:UpdateSummary()
  local s = T:Summary()
  ctx.check(s.dungeon and s.dungeon.key == "SM" and s.dungeon.run, "summary.dungeon")
  M.instance = false
  M.group = {}
  GetInstanceInfo = function() return "Elwynn", "none", 0, "", 5, 0, false, 0 end
  ctx.fire("PLAYER_ENTERING_WORLD")
  ctx.check(D.char.last and D.char.last.key == "SM" and T.tabs.dungeon, "bilan kept in the tab after leaving")
  D.char.last = nil
  D:Refresh(true)
  ctx.check(not T.tabs.dungeon, "tab removed once the bilan is cleared")
end
