-- Scenario 5.1 CLEAN / B5 — migration of an existing 5.0 player (tests/wow/fixtures/sv50_default_sv.lua): the modules
-- the 5.1 default adds are switched on ONCE, except what he switched off himself ; his own settings are kept ; the
-- settings of the removed modules are wiped once. A second pass changes nothing (he turns Hunter off: it stays off).
return function(M, ctx)
  local T = TreeAI
  local check = ctx.check
  M.Advance(5, 0.5)
  local db = TreeAIDB
  check(T:ModuleProfile() == "sur", "still the default profile")
  check(db.safeV == T.SAFE_V and T.SAFE_V == 5, "migration 5 done once (safeV " .. tostring(db.safeV) .. ")")
  check(T.modules.Hunter._active and T.modules.PathLearn._active, "Hunter and PathLearn switched on by the migration")
  check(T.modules.Hunter.db.ammoWarn == 400, "his own Hunter setting kept")
  check(not T.modules.Talents._active and db.modChoice.Talents == false, "Talents: he switched it off himself — stays off")
  check(not T.modules.Stats._active, "Stats is not part of the default: untouched")
  check(db.gone51 == true and db.modules.Watch == nil and db.modules.Radar == nil, "removed modules: account settings wiped")
  local c = db.chars["Testeur-Royaume-Test"]
  check(c and c.moduleSettings.Horizon == nil and c.modules.Radar == nil, "removed modules: per-character settings wiped")
  -- he switches Hunter off; the next login runs the migration code again: nothing comes back
  T:SetModuleEnabled("Hunter", false)
  check(db.modChoice.Hunter == false, "his choice is recorded")
  T:_ApplyProfileOnce()
  check(not T.modules.Hunter._active, "a second pass never re-enables what he switched off")
end
