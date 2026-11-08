-- TAINT scenario: nothing TreeAI does during combat may be blocked by the game (ADDON_ACTION_BLOCKED, counted in
-- the AddOns list as « Échec des actions d'interface à cause du programme d'interface personnalisé »).
-- The strict mock marks Blizzard protected frames (TargetFrame, action buttons, party frames), protected
-- nameplates, and protected functions; a Show / Hide / SetPoint… on a protected frame (or on OUR frame anchored
-- to one) in combat is dropped and recorded in M.blocked. Also: the « Blocages » diagnostic (counts kept per
-- function + module, /tai bloque). Fake names only.
return function(M, ctx)
  local T = TreeAI
  local H = T and T.modules and T.modules.Highlight
  local Q = T and T:GetModule("Quests")
  ctx.check(type(T.FrameLocked) == "function", "TreeAI:FrameLocked exists")
  ctx.check(M.ProtectedOf(TargetFrame), "mock: TargetFrame is protected")
  M.LeaveCombat()

  -- mock self-test: anchoring our frame on a protected frame makes it protected (implicit protection)
  local mine = CreateFrame("Frame", nil, UIParent)
  mine:SetPoint("BOTTOMLEFT", TargetFrame, "TOPLEFT", 0, 0)
  ctx.check(mine:IsProtected(), "mock: our frame anchored on TargetFrame is protected")
  M.EnterCombat()
  local before = #M.blocked
  mine:Hide()
  ctx.check(#M.blocked == before + 1, "mock: Hide() of an anchored-protected frame in combat is blocked")
  ctx.check(T:FrameLocked(mine) and not T:FrameLocked(CreateFrame("Frame", nil, UIParent)), "FrameLocked: anchored yes, plain no")
  -- clean the self-test out of the counters
  for i = #M.blocked, before + 1, -1 do M.blocked[i] = nil end
  for i = #M.violations, 1, -1 do if M.violations[i].kind == "blocked" then table.remove(M.violations, i) end end
  M.LeaveCombat()
  if type(T.ResetBlocked) == "function" then T:ResetBlocked() end

  -- Highlight: quest mob = target / nameplate, bag overlay, loot -------------------------------------------
  if H and Q then
    M.AddItem(9201, { name = "Croc de loup", type = "Quête", classID = 12, stack = 20 })
    M.bags = {}
    for b = 0, 4 do M.bags[b] = { size = 16, slots = {} } end
    M.SetBagItem(0, 1, 9201, 2)
    M.quests = {
      { header = "Contreforts" },
      { title = "Les loups", level = 25, objectives = { { text = "Loup gris tué : 2/8", done = false, have = 2, need = 8 } } },
      { title = "Les crocs", level = 25, objectives = { { text = "Croc de loup : 2/6", done = false, have = 2, need = 6 } } },
    }
    -- a Blizzard bag button that is protected (worst case: secure item buttons)
    local cf = CreateFrame("Frame", "ContainerFrame1", UIParent)
    cf:SetID(0)
    local bb = CreateFrame("Button", "ContainerFrame1Item1", cf)
    bb:SetID(1)
    bb.__protected = true
    ctx.fire("QUEST_LOG_UPDATE")
    M.Advance(2)
    Q.Update()
    H.Rebuild()
    H.RefreshBags()
    M.units.nameplate1 = { name = "Loup gris", guid = "Creature-0-0-0-0-3101-1", hostile = true, level = 25 }
    M.target = { exists = true, name = "Loup gris", guid = "Creature-0-0-0-0-3101-1", hostile = true, level = 25 }
    ctx.fire("PLAYER_TARGET_CHANGED")
    ctx.fire("NAME_PLATE_UNIT_ADDED", "nameplate1")
    M.Advance(1)
    ctx.check(H.targetText and H.targetText:find("Les loups"), "target badge out of combat: " .. tostring(H.targetText))

    -- combat-heavy minute: target switches, plates in / out, loot of a quest item, bag updates
    M.EnterCombat()
    for r = 1, 12 do
      M.units["nameplate" .. (r + 1)] = { name = "Loup gris", guid = "Creature-0-0-0-0-3101-" .. (r + 1), hostile = true, level = 25 }
      ctx.fire("NAME_PLATE_UNIT_ADDED", "nameplate" .. (r + 1))
      M.target = (r % 3 == 0) and { exists = false } or
        { exists = true, name = "Loup gris", guid = "Creature-0-0-0-0-3101-" .. (r + 1), hostile = true, level = 25 }
      ctx.fire("PLAYER_TARGET_CHANGED")
      M.SetBagItem(0, 1, 9201, 2 + r)
      ctx.fire("BAG_UPDATE", 0)
      ctx.fire("BAG_UPDATE_DELAYED")
      if r % 4 == 0 then
        ctx.fire("NAME_PLATE_UNIT_REMOVED", "nameplate" .. (r - 1))
        M.units["nameplate" .. (r - 1)] = nil
      end
      M.Advance(1, 0.1)
    end
    H.RefreshPlates()
    H.UpdateTarget()
    M.Advance(2, 0.1)
    ctx.check(#M.blocked == 0, "Highlight in combat: 0 blocked actions (got " .. #M.blocked ..
      (M.blocked[1] and (", first " .. tostring(M.blocked[1].fn)) or "") .. ")")
    M.LeaveCombat()
    M.Advance(2, 0.1)
    local soft = H.SoftHidden and H.SoftHidden() or {}
    local left = 0
    for _ in pairs(soft) do left = left + 1 end
    ctx.check(left == 0, "after combat: frames hidden by alpha are really hidden again (" .. left .. " left)")
    M.target = { exists = true, name = "Loup gris", guid = "Creature-0-0-0-0-3101-1", hostile = true, level = 25 }
    ctx.fire("PLAYER_TARGET_CHANGED")
    ctx.check(H.targetText ~= nil, "target badge works again after combat")
  end

  -- the quest item secure button is never touched in combat (RunOutOfCombat)
  if Q and Q.UpdateButton then
    M.EnterCombat()
    pcall(Q.UpdateButton)
    ctx.check(#M.blocked == 0, "Quests secure item button: nothing in combat")
    M.LeaveCombat()
  end

  -- every module refresh + panel clicks in combat: still nothing blocked
  M.EnterCombat()
  for _, mod in pairs(T.modules or {}) do
    if type(mod) == "table" and type(mod.AutoRefresh) == "function" then pcall(mod.AutoRefresh, mod) end
  end
  M.Advance(5, 0.25)
  ctx.check(#M.blocked == 0, "module refresh in combat: 0 blocked (got " .. #M.blocked ..
    (M.blocked[1] and (", first " .. tostring(M.blocked[1].fn)) or "") .. ")")
  M.LeaveCombat()

  -- « Blocages » diagnostic: counts per function + module, kept beyond the last 20 ---------------------------
  T._ctx = "Essai:test"
  for _ = 1, 25 do M.FireEvent("ADDON_ACTION_BLOCKED", "TreeAI", "Frame:SetPoint()") end
  M.FireEvent("ADDON_ACTION_FORBIDDEN", "TreeAI", "CreateMacro()")
  M.FireEvent("ADDON_ACTION_BLOCKED", "AutreAddon", "Frame:Show()")   -- not ours: ignored
  T._ctx = nil
  local top, total, session = T:BlockedTop(5)
  ctx.check(top[1] and top[1].func == "Frame:SetPoint()" and top[1].n == 25 and top[1].ctx == "Essai:test",
    "BlockedTop: counted per function + module: " .. tostring(top[1] and top[1].n))
  ctx.check(total == 26 and session == 26, "BlockedTop: totals " .. tostring(total) .. "/" .. tostring(session))
  ctx.check(#TreeAIDB.diag.blocked <= 20, "diag.blocked stays the last 20")
  local ST = T:GetModule("SelfTest")
  if ST and ST.BlockedLines then
    local lines = ST.BlockedLines(5)
    ctx.check(lines[1] and lines[1]:find("Frame:SetPoint%(%) ×25") and lines[1]:find("Essai:test"), "Blocages line: " .. tostring(lines[1]))
  end
  M.output = {}
  T:HandleSlash("bloque")
  local said = table.concat(M.output or {}, "\n")
  ctx.check(said:find("Blocages") and said:find("×25"), "/tai bloque lists the top functions")
  T:HandleSlash("bloque reset")
  local _, total2 = T:BlockedTop(5)
  ctx.check(total2 == 0, "/tai bloque reset")
  -- the deliberate events above are not real blocked calls
  for i = #M.violations, 1, -1 do if M.violations[i].kind == "blocked" then table.remove(M.violations, i) end end
end
