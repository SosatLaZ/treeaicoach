-- PLACEBUG (4.0.1) fixture: SavedVariables written by 4.0.0 for a player who dragged his frames on a client that
-- re-anchors a moved frame « TOPLEFT relative to BOTTOMLEFT » (4.0.0 kept only the point → restored off screen,
-- clamped to the top edge = « dans un coin »), plus incomplete records (x / y missing) and one valid record.
-- Used by tests/wow/test_placement.py (mode « olddata » of tests/wow/placement_scenario.lua). Fake names.
TreeAIDB = {
  profileV = 999, moduleProfile = "tout", onboarded = true, tour = { done = 999 }, gone40 = true,
  settings = {
    alert = { enabled = true, corner = "TOPRIGHT", pos = { point = "TOPRIGHT", corner = "TOPRIGHT" } },
  },
  chars = {
    ["Testeur-Royaume-Test"] = {
      layout = { sig = "1920x1080@0.71", v = 3, manual = { compass = true } },
      movers = { horizon = { point = "TOP" } },
      panel = { point = "TOPLEFT", x = 0, y = -422, tab = "now", mini = true },
      modules = {
        Window = { cardPos = { point = "TOPLEFT", x = 1500, y = 858 } },
        Compass = { point = "TOPLEFT", x = 348, y = 808 },
        Shortcuts = { pos = { "BOTTOMLEFT", "BOTTOMLEFT", 700, 260 } },
      },
    },
  },
}
