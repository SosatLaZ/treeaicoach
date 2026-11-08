-- qaui_leak_scenario.lua (QA-UI round 9 → FIX-UI regression, P0 UI-01): the « Donjons » cards come from a pool
-- registered by Window.lua, released at every render / page change: no frame growth while the page stays open
-- (panel refreshes ≤ 1/s for 60 s), no card left drawn (or clickable) on another page.
-- lua5.1 tests/wow/harness.lua -q --no-storm --profile sur --flavor forever --class HUNTER --level 28 \
--   --allow LibQuestieDB --allow QuestDB --allow NpcDB --allow ObjectDB --allow ItemDB --allow TreeAIPlan \
--   --allow IsQuestFlaggedCompleted --scenario tests/wow/qaui_leak_scenario.lua wow_addon/TreeAI   (QAUI_VERBOSE=1: dump)
return function(M, ctx)
  local T = TreeAI
  local W = T.Window
  local check = ctx.check
  local verbose = os.getenv("QAUI_VERBOSE") == "1"
  local function p(...) if verbose then io.stderr:write(table.concat({ ... }, " "), "\n") end end
  local Lb = dofile("tests/wow/qaui_lib.lua")
  Lb.world(M, T)
  Lb.goTo(1441, 46, 50)
  M.Advance(5, 0.5)
  W.char.dgAll, W.char.dungeon, W.char.collapsed = nil, nil, {}
  W.Open("dungeon")
  local win = W.GetWindow()
  local function cards()
    local n, vis = 0, 0
    for _, c in ipairs(win.content.__children) do
      if c.__kind == "Button" and c.name and c.lv then   -- a dungeon card (name + level font strings)
        n = n + 1
        if c:IsVisible() then vis = vis + 1 end
      end
    end
    return n, vis
  end
  local listed = 0
  for _, r in ipairs(W.lastLay.rects) do if r.kind == "dcard" then listed = listed + 1 end end
  local n1, v1 = cards()
  p(("after 1st render of Donjons : %d card frames, %d visible, %d listed"):format(n1, v1, listed))
  check(listed > 0 and n1 == listed and v1 == listed, ("first render: one frame per listed dungeon (%d frames, %d visible, %d listed)"):format(n1, v1, listed))
  for _ = 1, 10 do W.Render() end
  local n2, v2 = cards()
  p(("after 10 more renders      : %d card frames, %d visible"):format(n2, v2))
  check(n2 == n1 and v2 == listed, ("10 renders: cards recycled, not recreated (%d → %d frames)"):format(n1, n2))
  -- the window stays open on Donjons while the panel refreshes (W.Dirty ≤ 1/s): one minute
  local before = #M.frames
  for _ = 1, 60 do T:RefreshPanel(); M.Advance(1, 0.5) end
  local n3, v3 = cards()
  p(("after 60 s open on Donjons  : %d card frames, %d visible, +%d frames created"):format(n3, v3, #M.frames - before))
  check(n3 == n1 and v3 == listed, ("60 s open: still %d card frames (%d)"):format(n1, n3))
  check(#M.frames - before == 0, ("60 s open on Donjons: frame count stable (+%d frames)"):format(#M.frames - before))
  W.Select("hv")
  local n4, v4 = cards()
  p(("page HV                    : %d card frames, %d still visible on the HV page"):format(n4, v4))
  check(v4 == 0, ("page HV: no dungeon card left drawn (%d visible)"):format(v4))
  W.Select("tools")
  local _, v5 = cards()
  p(("page Outils                : %d cards still visible"):format(v5))
  check(v5 == 0, ("page Outils: no dungeon card left (%d visible)"):format(v5))
  -- back and forth (each page already drawn once: its widgets exist): the same frames come back, none created
  W.Select("now")
  local beforeSwitch = #M.frames
  for _ = 1, 5 do W.Select("dungeon") W.Select("hv") W.Select("tools") W.Select("now") end
  W.Select("dungeon")
  local n6, v6 = cards()
  check(n6 == n1 and v6 == listed and #M.frames - beforeSwitch == 0,
    ("switching pages ×5: cards reused (%d frames, +%d frames total)"):format(n6, #M.frames - beforeSwitch))
  W.Close()
end
