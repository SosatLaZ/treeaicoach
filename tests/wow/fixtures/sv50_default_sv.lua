-- 5.1 CLEAN fixture: SavedVariables of a 5.0.0 player in the default profile (« sur », one-time migrations up to
-- SAFE_V 4 done). Hunter / Talents / PathLearn were off because 5.0 shipped them off; he switched Talents off HIMSELF
-- (modChoice). He also has settings of the modules removed in 5.1 (Watch, Radar, Horizon: account and per character).
-- Used by tests/wow/test_clean51.py (tests/wow/migrate51_scenario.lua). Fake names.
TreeAIDB = {
  profileV = 2, moduleProfile = "sur", safeV = 4, onboarded = true, tour = { done = 999 }, v5hBar = true,
  modChoice = { Talents = false },
  modules = {
    Hunter = { enabled = false, ammoWarn = 400 },
    Talents = { enabled = false },
    PathLearn = { enabled = false },
    Stats = { enabled = false },
    Watch = { enabled = false, shown = false },
    Radar = { enabled = false, size = 200 },
  },
  chars = {
    ["Testeur-Royaume-Test"] = {
      moduleSettings = { Horizon = { enabled = false, shown = true } },
      modules = { Radar = { point = "TOP" } },
    },
  },
}
