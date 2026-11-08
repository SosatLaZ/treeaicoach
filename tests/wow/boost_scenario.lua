-- Scenario for Modules/Boost.lua = module « Rares » (CLEAN 4.0), run by tests/wow/test_boost.py, fake names only.
return function(M, ctx)
  local T = TreeAI
  local B = T and T.modules and T.modules.Boost
  ctx.check(B and B.db and B.db.enabled and B.title == "Rares", "Rares registered + enabled (internal name Boost kept)")
  if not B then return end
  if T.SetSoundKind then T:SetSoundKind("rare", true) end   -- (4.0.1) sons coupés par défaut sauf le danger
  local S = T._suggestions
  M.LeaveCombat()
  T.settings.soundTips = true  -- rare sound is a prio-2 tip: needs the « sons aussi pour les conseils » option

  -- 1. rare on a nameplate: one line + sound, once per GUID; click = skull out of combat
  M.units.nameplate1 = { name = "Croc-test", guid = "Creature-0-0-0-0-99-0001", classification = "rare",
    hostile = true, level = 27 }
  local snd = #M.sounds
  ctx.fire("NAME_PLATE_UNIT_ADDED", "nameplate1")
  ctx.check(S["Boost:rare"] and S["Boost:rare"].text:find("^Vise le rare Croc%-test %(niv 27%)"), "rare line, verb first")
  ctx.check(#M.sounds > snd, "rare sound played")
  snd = #M.sounds
  ctx.fire("NAME_PLATE_UNIT_ADDED", "nameplate1")
  ctx.check(#M.sounds == snd, "no second sound for the same GUID")
  if S["Boost:rare"] then M.Hw(S["Boost:rare"].onClick) end
  ctx.check(M.raidTargets and M.raidTargets.nameplate1 == 8, "skull marker on click")
  -- no skull in combat
  M.raidTargets = {}
  M.EnterCombat()
  if S["Boost:rare"] then M.Hw(S["Boost:rare"].onClick) end
  ctx.check(not (M.raidTargets and M.raidTargets.nameplate1), "no marker in combat")
  M.LeaveCombat()

  -- 2. an elite is not a rare: no line any more (it was noise)
  M.units.nameplate2 = { name = "Brute-test", guid = "Creature-0-0-0-0-98-0002", classification = "elite", hostile = true, level = 28 }
  ctx.fire("NAME_PLATE_UNIT_ADDED", "nameplate2")
  ctx.check(not S["Boost:elite"], "no elite line")

  -- 3. the 8 other old Boost features are gone (owners elsewhere)
  for _, k in ipairs({ "CheckThreat", "CheckTravel", "PetAdvice", "CheckReminders", "ParseXP", "RankQuestRewards" }) do
    ctx.check(B[k] == nil, "removed from Boost: " .. k)
  end

  -- 4. old contract kept as forwards: Gear (item scoring) and Stats (measured XP/h, nothing under 10 min)
  ctx.check(B.CanUse and B.UpgradePct and B.CanUse(0) ~= nil, "Boost.CanUse forwards to Gear")
  ctx.check(B:XPStats() == nil, "no XP/h without at least 10 measured minutes")
  local St = T.modules.Stats
  local c = St and St.Data and St.Data()
  if c then
    c.win = { { b = 1, x = 6000, k = 30, s = 900, g = 0 } }   -- 15 min, 6 000 XP -> 24 000 XP/h
    local r = B:XPStats()
    ctx.check(r and r.perHour == 24000 and r.minutes == 15, "XP/h from Stats with its measuring time: " .. tostring(r and r.perHour))
  end

  -- compact summary for the app
  T:Fire("SUMMARY")
  local sb = T:Summary().boost
  ctx.check(sb and sb.raresSeen == 1 and sb.lastRare == "Croc-test", "summary.boost written")
end
