-- Scenario for Modules/Theme.lua: every TreeAI frame gets the common theme, factories work, theme switch
-- re-applies, Blizzard / secure frames are never skinned. Fake names only.
return function(M, ctx)
  local T = TreeAI
  local Th = T and T.Theme
  ctx.check(Th and Th.Skin, "TreeAI.Theme exposed")
  if not Th then return end
  M.LeaveCombat()
  ctx.check(Th:Name() == "Sombre", "default theme Sombre")

  local function try(label, fn, ...)
    local ok, err = pcall(fn, ...)
    ctx.check(ok, label .. (ok and "" or (" : " .. tostring(err))))
  end

  -- build every module frame ------------------------------------------------------------------------
  try("panel", function() T:ShowPanel() end)
  try("alert", function() T:ShowAlert({ text = "Test alerte", prio = 3 }, true) end)
  local C = T:GetModule("Compass")
  if C and C.Show then try("compass", C.Show, true) end
  local Tr = T:GetModule("Tracker")
  if Tr and Tr.RenderFrame then try("tracker", Tr.RenderFrame) end
  local A = T:GetModule("Auction")
  if A and A.CreateWindow then try("auction", A.CreateWindow) end
  local Mc = T:GetModule("Macros")
  if Mc and (Mc.ToggleList or Mc.TogglePanel) then try("macros", Mc.ToggleList or Mc.TogglePanel, Mc) end   -- (MACRO3) list = panel
  M.FireEvent("START_AUTOREPEAT_SPELL")
  local HP = T:GetModule("HunterPro")
  if HP and HP.OnTrapCast then try("hunterpro", HP.OnTrapCast, HP, "Piège givrant") end
  local H = T:GetModule("Highlight")
  if H and H.CheckLoot then try("highlight", H.CheckLoot) end
  M.Advance(2, 0.5)

  -- which frames carry the theme ----------------------------------------------------------------------
  local byKind = {}
  for _, f in ipairs(M.frames) do
    local k = Th:SkinOf(f)
    if k then byKind[k] = (byKind[k] or 0) + 1 end
  end
  local p = T:GetPanel()
  ctx.check(p and Th:SkinOf(p) == "window", "main panel skinned")
  local al = T:GetAlert()
  ctx.check(al and Th:SkinOf(al) == "alert", "alert box skinned")
  if C then ctx.check(_G.TreeAICompassFrame and Th:SkinOf(_G.TreeAICompassFrame) == "hud", "compass skinned") end
  if Tr and Tr.RenderFrame then ctx.check(_G.TreeAITrackerFrame and Th:SkinOf(_G.TreeAITrackerFrame) == "hud", "tracker skinned") end
  if A then ctx.check(A.win and Th:SkinOf(A.win) == "window", "auction window skinned") end
  if Mc then ctx.check(Mc.panel and Th:SkinOf(Mc.panel) == "window", "macros panel skinned") end
  if T:PlayerClass() == "HUNTER" then ctx.check((byKind.bar or 0) >= 2, "hunter + trap bars skinned") end
  if H then ctx.check((byKind.hud or 0) >= 3, "highlight loot panel skinned (hud)") end
  ctx.check(Th:Count() >= 6, "skinned frames: " .. Th:Count())

  -- never Blizzard / secure frames ---------------------------------------------------------------------
  local blizz = CreateFrame("Frame", "MerchantFrameFake", UIParent)
  ctx.check(not Th:Skin(blizz, "panel"), "named non-TreeAI frame refused")
  local sec = CreateFrame("Button", nil, UIParent, "SecureActionButtonTemplate")
  ctx.check(not Th:Skin(sec, "button"), "secure frame refused")
  ctx.check(not Th:Skin(UIParent, "panel"), "UIParent refused")

  -- factories ---------------------------------------------------------------------------------------------
  local host = Th:Frame(UIParent, "window", 200, 120)
  ctx.check(host and Th:SkinOf(host) == "window", "Frame factory")
  local b = Th:Button(host, "OK", 80, 20, function() end, "aide")
  ctx.check(b and Th:SkinOf(b) == "button", "Button")
  local ib = Th:IconButton(host, 134400, 20, nil, "Icône", "aide")
  ctx.check(ib and Th:IsSkinned(ib), "IconButton")
  local bar = Th:ProgressBar(host, 120, 10)
  ctx.check(bar and Th:SkinOf(bar) == "bar", "ProgressBar")
  bar:SetProgress(3, 10, 2, "3/10")
  ctx.check(bar:GetValue() == 3 and bar.text:GetText() == "3/10", "ProgressBar:SetProgress")
  ctx.check(Th:Header(host, "Titre", "L"):GetText() == "Titre", "Header")
  ctx.check(Th:Separator(host, 100), "Separator")
  local flag = false
  local cb = Th:Checkbox(host, "Option", function() return flag end, function(v) flag = v end)
  cb:SetChecked(true); cb:Click()
  ctx.check(flag == true, "Checkbox set")
  local val
  local sl = Th:Slider(host, "Taille", 0, 2, 0.1, function() return 1 end, function(v) val = v end)
  M.Script(sl, "OnValueChanged", 1.5)
  ctx.check(val == 1.5, "Slider set")
  local tab = Th:Tab(host, "Onglet", 80)
  tab:SetSelected(true)
  ctx.check(tab and Th:IsSkinned(tab), "Tab")
  local badge = Th:Badge(host, "3", 3)
  ctx.check(badge and Th:SkinOf(badge) == "badge" and badge.text:GetText() == "3", "Badge")
  Th:Tooltip(b, "Titre", { "ligne", { "muet", "muted" } })
  M.Script(b, "OnEnter"); M.Script(b, "OnLeave")
  local r, g = Th:ClassColor("HUNTER")
  ctx.check(r and g, "ClassColor")
  ctx.check(Th:Hex("gold"):match("^ff%x%x%x%x%x%x$"), "Hex")
  ctx.check(select(1, Th:PrioColor(3)) == 1, "prio3 red")

  -- theme switch + scale: re-applied everywhere -----------------------------------------------------------
  T:HandleSlash("theme clair")
  ctx.check(Th:Name() == "Clair", "slash theme clair")
  local _, idx = Th:SkinOf(p)
  ctx.check(idx == 2, "panel re-skinned in Clair")
  Th.module.db.theme = 3
  Th.module.db.scale = 1.2
  Th:Refresh()
  ctx.check(select(2, Th:SkinOf(A and A.win or p)) == 3, "Blizzard theme applied")
  ctx.check(Th:Scale() == 1.2, "scale")
  Th.module.db.skin = false
  ctx.check(not Th:Skin(Th:Frame(UIParent) or host, "panel"), "skin off: no skinning")
  Th.module.db.skin = true
  Th.module.db.theme = 1

  -- options page: the « choice » type is rendered
  if T.InitOptions then try("options", T.InitOptions, T) end
end
