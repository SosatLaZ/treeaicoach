-- Scenario 5.1 CLEAN — TUTO_FR.md: every « /tai x » it cites exists in the default profile (list in TREEAI_TUTO_CMDS,
-- given by tests/wow/test_clean51.py). A command that answers « inconnue » or is missing fails.
return function(M, ctx)
  local T = TreeAI
  local check = ctx.check
  M.Advance(5, 0.5)
  local list = os.getenv("TREEAI_TUTO_CMDS") or ""
  local n = 0
  for c in list:gmatch("%S+") do
    n = n + 1
    check(T:HasSlash(c), "/tai " .. c .. " (cited by TUTO_FR.md) is a registered command")
  end
  check(n >= 15, "the list of cited commands reached the scenario (" .. n .. ")")
  print("TUTO commands checked: " .. n)
end
