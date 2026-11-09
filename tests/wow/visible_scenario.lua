-- Harness scenario (4.3.6, agent V5H): « tout ce qui n'apparaît pas ». Every TreeAI element expected on screen by
-- default (boutons rapides strip, compass, minimap button, alert box when an alert fires) must be shown, on screen,
-- with alpha > 0 and a size > 0 — after login, after /reload (run again with --savedvars) and during combat.
-- Run by tests/wow/test_visible.py on the real-client geometry (--geometry ready=1), mode sûr profile.
-- Also: a strip saved off screen / module switched off / strip option off comes back with « /tai barre ».
return function(M, ctx)
  local T = TreeAI
  local check = ctx.check
  local geo = M.geo
  if not check(geo ~= nil, "visible_scenario needs --geometry") then return end
  local function out(s) io.write("VISIBLE ", s, "\n") end
  M.LeaveCombat()
  -- a quest with a target so the compass has something to point at
  M.quests = {
    { header = "Les Tarides" },
    { id = 901, title = "Crocs de test", level = 28, objectives = { { text = "Croc : 1/6", done = false, have = 1, need = 6 } } },
  }
  ctx.fire("QUEST_LOG_UPDATE")
  M.Advance(6, 0.1)

  local function alphaOf(f)
    local a, p = 1, f
    while p do
      local v = p.GetAlpha and p:GetAlpha()
      if type(v) == "number" then a = a * v end
      p = p.GetParent and p:GetParent() or nil
    end
    return a
  end
  local function audit(tag, names)
    for _, n in ipairs(names) do
      local f = rawget(_G, n)
      local ok = f ~= nil and f:IsVisible() and geo.OnScreen(f, 4)
      local r = f and geo.Rect(f)
      ok = ok and r ~= nil and r.w > 1 and r.h > 1 and alphaOf(f) > 0.05
      check(ok, ("%s : %s affiché, à l'écran, visible (%s)"):format(tag, n,
        f and (("shown=%s vis=%s on=%s a=%.2f r=%s"):format(tostring(f:IsShown()), tostring(f:IsVisible()),
          tostring(geo.OnScreen(f, 4)), alphaOf(f), r and ("%d,%d %dx%d"):format(r.l, r.b, r.w, r.h) or "nil")) or "absent"))
      if ok then out(tag .. " ok " .. n) end
    end
  end
  local ALWAYS = { "TreeAIStrip", "TreeAICompassFrame", "TreeAIMinimapButton" }
  audit("connexion", ALWAYS)
  T:ShowAlert({ id = "v5h", text = "Alerte de test", prio = 3 })
  M.Advance(0.5, 0.1)
  audit("alerte", { "TreeAIAlert" })

  -- combat: everything stays, nothing hidden
  M.target = { exists = true, name = "Hyène de test", level = 27, health = 500, healthMax = 500, hostile = true }
  ctx.fire("PLAYER_TARGET_CHANGED")
  M.EnterCombat() ctx.fire("PLAYER_REGEN_DISABLED")
  M.Advance(3, 0.1)
  audit("combat", ALWAYS)
  M.LeaveCombat() ctx.fire("PLAYER_REGEN_ENABLED")
  M.Advance(2, 0.1)
  audit("après combat", ALWAYS)

  if os.getenv("TREEAI_VISIBLE_MODE") == "reload" then return end
  -- broken states from a real client: saved off screen, strip option off, module off → /tai barre fixes them all
  local S = T.Shortcuts
  local function barre() SlashCmdList["TREEAI"]("barre") end
  if not check(S and T:HasSlash("barre"), "/tai barre exists") then return end
  S.char.strip = { "TOPLEFT", "TOPLEFT", 5000, -3000 }
  S.PlaceStrip()
  M.Advance(1, 0.1)
  audit("position enregistrée hors écran", { "TreeAIStrip" })
  S.db.strip = false S:OnOptionChanged("strip")
  M.Advance(0.5, 0.1)
  check(not TreeAIStrip:IsShown(), "option coupée : bande cachée")
  barre()
  M.Advance(1, 0.1)
  audit("/tai barre (option coupée)", { "TreeAIStrip" })
  T:SetModuleEnabled("Shortcuts", false)
  M.Advance(0.5, 0.1)
  barre()
  M.Advance(1, 0.1)
  audit("/tai barre (module coupé)", { "TreeAIStrip" })
  check(#M.violations == 0, "aucune violation")
end
