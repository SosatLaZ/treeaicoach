-- lite_scenario.lua — LITE: the « essentiel » module profile (default for everyone, applied once), gates
-- (group / auction house / instance), real event + ticker unregistration when a module is off, « Plus →
-- Modules » tab, /tai perf. Run with: lua5.1 tests/wow/harness.lua --profile essentiel --scenario <this> wow_addon/TreeAI
return function(M, ctx)
  local T = TreeAI
  local check = ctx.check
  local function mod(n) return T.modules[n] end
  local function has(list, v) for _, x in ipairs(list) do if x == v then return true end end return false end

  -- (3.9.5) new players keep everything on; « essentiel » is opt-in
  check(TreeAIDB.profileV == T.PROFILE_V, "profile version flag set once")
  T:SetModuleProfile("essentiel")
  check(T:ModuleProfile() == "essentiel" and T:Lite(), "essentiel profile on request")
  for _, n in ipairs({ "Boost", "Fight", "HunterPro", "Comfort", "Pro", "Economy", "Session", "Errands", "Gear",
    "Talents", "Prof", "PvP", "Radar", "SafePath", "Binds", "Leveling", "Inventory", "Classes", "Predict" }) do
    local m = mod(n)
    if m then
      check(m._active ~= true and not m:IsEnabled(), n .. " off by default")
      check(m._suspended == true, n .. " suspended (events / tickers really gone)")
      check(T:ModuleTickers(m) == 0, n .. " has no running ticker")
    end
  end
  for _, n in ipairs({ "Quests", "Tracker", "Todo", "Compass", "Planner", "Export", "Macros", "Highlight", "Abandon", "Theme" }) do
    local m = mod(n)
    if m then check(m._active == true, n .. " on in essentiel") end
  end
  -- Hunter: class-gated (only on a hunter)
  if mod("Hunter") then check((mod("Hunter")._active == true) == (T:PlayerClass() == "HUNTER"), "Hunter only for hunters") end

  -- notice (one line, clickable « Tout réactiver »)
  T._liteNotice = true
  T:_LiteNotice()
  M.Advance(4, 0.5)
  local lines = T:GetTabLines("now")
  local notice
  for _, e in ipairs(lines) do if e.id == "core:lite" then notice = e end end
  check(notice and notice.onClick and notice.text:find("Clic : tout réactiver"), "one-line notice with « Tout réactiver »")

  -- the game no longer sends us the events only off modules wanted (none of their handlers remain)
  local evs = T:RegisteredEvents()
  check(not has(evs, "COMBAT_LOG_EVENT_UNFILTERED"), "no combat log")

  -- « Plus → Modules » tab: one line per module, click = toggle
  local ml = T:GetTabLines("modules")
  check(#ml >= 30, "modules tab lists the modules (" .. #ml .. ")")
  local fightLine
  for _, e in ipairs(ml) do if e.id == "mod:Fight" then fightLine = e end end
  check(fightLine and fightLine.text:find("OFF") and fightLine.text:find("—"), "Fight line: state + one-line description")
  T:UserAction(fightLine.onClick)
  check(mod("Fight")._active == true and not mod("Fight")._suspended, "click: Fight on, no /reload")
  M.Advance(3, 0.5)
  T:SetModuleEnabled("Fight", false)
  check(mod("Fight")._suspended == true and T:ModuleTickers(mod("Fight")) == 0, "Fight off again: tickers cancelled")

  -- gates: Squad / Missions / Group need a group; Auction the auction house; Dungeon an instance
  M.group = {}
  ctx.fire("GROUP_ROSTER_UPDATE")
  M.Advance(1, 0.5)
  for _, n in ipairs({ "Squad", "Missions", "Group" }) do
    if mod(n) then check(not mod(n):IsEnabled() and mod(n)._suspended, n .. " sleeps when solo") end
  end
  M.group = { { name = "Brindille", class = "PRIEST", level = 26, health = 800, healthMax = 900, online = true } }
  ctx.fire("GROUP_ROSTER_UPDATE")
  M.Advance(1, 0.5)
  for _, n in ipairs({ "Squad", "Missions" }) do
    if mod(n) then check(mod(n)._active == true and mod(n):IsEnabled(), n .. " wakes up in a group") end
  end
  if mod("Auction") then
    check(not mod("Auction"):IsEnabled(), "Auction asleep while the AH is closed")
    ctx.fire("AUCTION_HOUSE_SHOW")
    M.Advance(1, 0.5)
    check(mod("Auction")._active == true, "Auction awake at the AH")
    ctx.fire("AUCTION_HOUSE_CLOSED")
    M.Advance(1, 0.5)
    check(not mod("Auction"):IsEnabled() and mod("Auction")._suspended, "Auction asleep again")
  end

  -- /tai perf
  M.RunSlash("/tai perf")
  check(ctx.out("modules actifs"), "/tai perf prints the summary")
  check(ctx.out("ms/min"), "/tai perf lists the top modules")

  -- « Tout réactiver »: everything on, live
  T:UserAction(notice.onClick)
  check(T:ModuleProfile() == "tout", "profile tout")
  for _, n in ipairs({ "Boost", "Pro", "Economy", "Predict" }) do
    if mod(n) then check(mod(n)._active == true, n .. " on after « Tout réactiver »") end
  end
  check(not T:GetTabLines("now")[1] or T:GetTabLines("now")[1].id ~= "core:lite", "notice gone")
  M.Advance(10, 0.5)
  -- and back
  T:SetModuleProfile("essentiel")
  check(mod("Boost")._suspended == true and T:ModuleTickers(mod("Boost")) == 0, "back to essentiel: Boost asleep")
  M.Advance(10, 0.5)
end
