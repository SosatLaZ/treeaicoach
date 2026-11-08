-- PVPMARK scenario: hostile attackable PLAYERS on nameplates get our own marker (class-colour outline, arrow,
-- level-diff badge, « Peut te tuer » / « Facile »); NPCs, friendly players, unflagged / dead players and
-- sanctuaries are ignored. Out of combat a marker is created and anchored on the plate; in combat only text /
-- colour / alpha change, a plate that appears in combat goes to the screen-space list (0 blocked actions with
-- protected plates). « Te cible ! » + one « danger » sound per player per 60 s. Fake names only.
return function(M, ctx)
  local T = TreeAI
  local PM = T and T.PvPMark
  ctx.check(PM ~= nil, "PvPMark module loaded")
  if not PM then return end
  ctx.check(PM.db and PM.db.enabled and PM.db.mark, "PvPMark on by default")
  ctx.check(not (T.settings and T.settings.pvp), "old PvP aids stay off by default (independent toggle)")
  M.LeaveCombat()
  M.player.level = 28
  M.protectedPlates = true

  -- pure estimate
  local e = PM.Estimate(28, "HUNTER", 30, "ROGUE")
  ctx.check(e.badge == "+2" and e.kind == "kill" and e.label == "Peut te tuer", "estimate +2 rogue = Peut te tuer")
  e = PM.Estimate(28, "HUNTER", 22, "PRIEST")
  ctx.check(e.badge == "-6" and e.kind == "easy" and e.label == "Facile", "estimate -6 priest = Facile")
  e = PM.Estimate(28, "HUNTER", -1, "WARRIOR")
  ctx.check(e.skull and e.kind == "kill", "estimate ?? = skull")
  ctx.check(PM.Estimate(28, "HUNTER", 34, "MAGE").skull, "estimate +6 = skull")
  ctx.check(PM.Estimate(28, "HUNTER", 28, "HUNTER").kind == "even", "estimate same level = Combat serré")

  local function add(unit, d) M.units[unit] = d ctx.fire("NAME_PLATE_UNIT_ADDED", unit) end
  local function rem(unit) ctx.fire("NAME_PLATE_UNIT_REMOVED", unit) M.units[unit] = nil end
  local sounds0 = PM.sounds or 0
  local log0 = #(T:SoundLog() or {})

  -- out of combat ------------------------------------------------------------------------------------------
  add("nameplate1", { name = "Ennemi Un", isPlayer = true, hostile = true, class = "ROGUE", level = 30, guid = "Player-1-A" })
  add("nameplate2", { name = "Loup gris", hostile = true, level = 27 })                                 -- NPC
  add("nameplate3", { name = "Ami Deux", isPlayer = true, hostile = false, class = "MAGE", level = 28 }) -- friendly
  add("nameplate4", { name = "Ennemi Trois", isPlayer = true, hostile = true, pvp = false, class = "WARRIOR", level = 28 }) -- unflagged
  add("nameplate5", { name = "Ennemi Mort", isPlayer = true, hostile = true, dead = true, class = "MAGE", level = 28 })
  add("nameplate6", { name = "Ennemi Six", isPlayer = true, hostile = true, class = "PRIEST", level = 22, guid = "Player-1-B" })
  M.Advance(1)
  ctx.check(PM:Count() == 2, "only the 2 attackable enemy players are tracked (got " .. PM:Count() .. ")")
  ctx.check(PM:Info("nameplate1") and PM:Info("nameplate6") and not PM:Info("nameplate2") and not PM:Info("nameplate3")
    and not PM:Info("nameplate4") and not PM:Info("nameplate5"), "NPC / friend / unflagged / dead ignored")
  local p1 = C_NamePlate.GetNamePlateForUnit("nameplate1")
  local m1 = PM._marks[p1]
  ctx.check(m1 and m1:IsShown() and m1:GetAlpha() == 1, "marker shown on enemy plate 1")
  ctx.check(m1 and m1.badge:GetText() == "+2" and m1.est:GetText() == "Peut te tuer", "plate 1 badge +2, Peut te tuer: "
    .. tostring(m1 and m1.badge:GetText()) .. " / " .. tostring(m1 and m1.est:GetText()))
  local m6 = PM._marks[C_NamePlate.GetNamePlateForUnit("nameplate6")]
  ctx.check(m6 and m6.est:GetText() == "Facile" and m6.badge:GetText() == "-6", "plate 6 Facile -6")
  ctx.check(PM._marks[C_NamePlate.GetNamePlateForUnit("nameplate2")] == nil, "no marker on the NPC plate")
  ctx.check(PM.sounds == sounds0 + 1, "one danger sound for the player who can kill you (got " .. tostring((PM.sounds or 0) - sounds0) .. ")")
  ctx.check(#(T:SoundLog() or {}) == log0 + 1 and T:SoundLog()[#T:SoundLog()].kind == "danger", "Sound kind danger")
  -- re-seen within 60 s: no new sound
  rem("nameplate1")
  ctx.check(not m1:IsShown(), "marker hidden when the plate goes away (out of combat)")
  add("nameplate1", { name = "Ennemi Un", isPlayer = true, hostile = true, class = "ROGUE", level = 30, guid = "Player-1-A" })
  ctx.check(PM.sounds == sounds0 + 1, "no second sound within 60 s for the same player")

  -- targets you ------------------------------------------------------------------------------------------------
  M.units.nameplate6.targetsPlayer = true
  ctx.fire("UNIT_TARGET", "nameplate6")
  ctx.check(m6.tme:GetText() == "Te cible !", "« Te cible ! » when the enemy targets you")
  ctx.check(PM.sounds == sounds0 + 2, "sound when an enemy starts targeting you")

  -- faction / flag change ---------------------------------------------------------------------------------------
  M.units.nameplate4.pvp = true
  ctx.fire("PLAYER_FLAGS_CHANGED", "nameplate4")
  ctx.check(PM:Info("nameplate4") ~= nil, "flag change: newly flagged enemy marked")
  M.units.nameplate4.pvp = false
  ctx.fire("UNIT_FACTION", "nameplate4")
  ctx.check(PM:Info("nameplate4") == nil, "flag change: unflagged enemy unmarked")

  -- combat ----------------------------------------------------------------------------------------------------
  M.EnterCombat()
  local b0 = #M.blocked
  for r = 1, 10 do
    local u = "nameplate" .. (10 + r)
    add(u, { name = "Ennemi " .. r, isPlayer = true, hostile = true, class = (r % 2 == 0) and "MAGE" or "WARRIOR",
      level = 26 + r % 5, targetsPlayer = r == 3 })
    add("nameplate" .. (30 + r), { name = "Loup gris", hostile = true, level = 27 })
    M.units.nameplate6.targetsPlayer = (r % 2 == 0)
    ctx.fire("UNIT_TARGET", "nameplate6")
    ctx.fire("UNIT_HEALTH", "nameplate6")
    if r % 3 == 0 then rem("nameplate" .. (9 + r)) end
    M.Advance(1, 0.1)
  end
  rem("nameplate1")   -- a marked plate leaves in combat: alpha only
  ctx.check(#M.blocked == b0, "combat: 0 blocked actions (got " .. (#M.blocked - b0) ..
    (M.blocked[b0 + 1] and (", first " .. tostring(M.blocked[b0 + 1].fn)) or "") .. ")")
  ctx.check(m1:GetAlpha() == 0, "combat: leaving marked plate faded (alpha 0), not hidden")
  ctx.check(PM._noMark.nameplate20 and PM.listLines and #PM.listLines >= 1, "combat: plate seen in combat goes to the top list")
  local list = _G.TreeAIPvPMarkList
  ctx.check(list and list:IsShown(), "combat list shown")
  ctx.check(PM.listLines and PM.listLines[1]:find("te cible"), "list: the one targeting you first: " .. tostring(PM.listLines and PM.listLines[1]))
  ctx.check(PM.listLines[1]:find("niv %d+") and PM.listLines[1]:find("portée") or PM.listLines[1]:find("proche"),
    "list line has level + distance band")
  -- the existing marker still updates in combat (texts only)
  if os.getenv("PVPMARK_DEBUG") then io.stderr:write("PVPMARK list: " .. table.concat(PM.listLines or {}, " | ") .. "\n") end
  ctx.check(m6.tme:GetText() == "Te cible !", "combat: existing marker text updated")
  -- dead in combat
  M.units.nameplate6.dead = true
  ctx.fire("UNIT_HEALTH", "nameplate6")
  ctx.check(PM:Info("nameplate6") == nil and m6:GetAlpha() == 0, "combat: dead enemy unmarked (alpha)")
  ctx.check(#M.blocked == b0, "combat: still 0 blocked actions")

  -- after combat: markers rebuilt, list hidden ------------------------------------------------------------------
  M.LeaveCombat()
  ctx.fire("PLAYER_REGEN_ENABLED")
  M.Advance(1)
  ctx.check(not list:IsShown(), "after combat: list hidden")
  local m20 = PM._marks[C_NamePlate.GetNamePlateForUnit("nameplate20")]
  ctx.check(m20 and m20:IsShown() and m20:GetAlpha() == 1, "after combat: marker built for the plate seen in combat")
  ctx.check(next(PM._noMark) == nil, "after combat: nothing left in the fallback list")

  -- sanctuary: nothing ----------------------------------------------------------------------------------------
  M.zonePvP = "sanctuary"
  ctx.fire("ZONE_CHANGED_NEW_AREA")
  ctx.check(PM:Count() == 0, "sanctuary: nothing marked")
  M.zonePvP = nil
  ctx.fire("ZONE_CHANGED_NEW_AREA")
  ctx.check(PM:Count() > 0, "contested again: marked")

  -- option off
  PM.db.mark = false
  PM:OnOptionChanged("mark", false)
  ctx.check(PM:Count() == 0 and not m20:IsShown(), "option off: markers cleared")
  PM.db.mark = true
  PM:OnOptionChanged("mark", true)
  ctx.check(PM:Count() > 0, "option on again")

  -- PvP aids: their « ennemi proche » alert defers to PvPMark (one owner)
  local P = T.PvP
  if P then
    T.settings.pvp = true
    P.enemies = { Gros = { name = "Gros", class = "WARRIOR", level = 34, seen = GetTime() },
      Gros2 = { name = "Gros2", class = "MAGE", level = 33, seen = GetTime() } }
    P:CheckDanger()
    local found = false
    for _, s in ipairs(T:GetSuggestions() or {}) do if tostring(s.id):find("PvP:danger", 1, true) then found = true end end
    ctx.check(not found, "PvP danger alert defers to PvPMark")
    T.settings.pvp = false
  end
  for k in pairs(M.units) do if k:match("^nameplate") then M.units[k] = nil end end
end
