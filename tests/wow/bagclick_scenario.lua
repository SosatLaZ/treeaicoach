-- Harness scenario (4.1 BUGHUNT): « je peux plus bouger les trucs de l'inventaire » (real game, 4.0.4 → 4.0.6).
-- Runs after the storm on the real-client geometry (harness --geometry ready=1). Two checks:
--  1. no TreeAI frame that is shown (IsVisible — an alpha-0 frame counts: it still eats the clicks) and takes the
--     mouse covers the bags (bottom-right: the default ContainerFrame1-5 stack, combined bags) once they are open.
--     « Takes the mouse » like the real client: EnableMouse(true), a Button / CheckButton / EditBox / Slider (mouse on
--     by default), or a frame given a mouse script (OnEnter / OnMouseDown / OnClick / OnDragStart / OnReceiveDrag…)
--     without EnableMouse(false);
--  2. no TreeAI code path touches the cursor (ClearCursor, Pickup*, PlaceAction, EquipCursorItem, DeleteCursorItem…)
--     without a player click: the bag / cursor / lock events a drag fires are replayed with every such call recorded.
-- Mode = env TREEAI_BAGCLICK_DUMP=1 prints every mouse-taking visible frame with its rect (diagnosis).
-- lua5.1 tests/wow/harness.lua -q --geometry ready=1 --flavor forever --class HUNTER --level 29 --profile tout \
--   --scenario tests/wow/bagclick_scenario.lua wow_addon/TreeAI
return function(M, ctx)
  local T = TreeAI
  local check = ctx.check
  local geo = M.geo
  if not check(geo ~= nil, "bagclick_scenario needs --geometry") then return end
  local UIP = UIParent
  local dump = os.getenv("TREEAI_BAGCLICK_DUMP") == "1"
  local function r0(v) return math.floor((tonumber(v) or 0) + 0.5) end

  -- 1) cursor instrumentation ---------------------------------------------------------------------------------
  local CURSOR = { "ClearCursor", "PickupContainerItem", "PickupItem", "PickupInventoryItem", "PlaceAction",
    "PickupAction", "PickupSpell", "PickupMacro", "PickupPetAction", "PickupBagFromSlot", "PickupMerchantItem",
    "EquipCursorItem", "AutoEquipCursorItem", "DeleteCursorItem", "PutItemInBackpack", "PutItemInBag",
    "SplitContainerItem", "EquipItemByName", "ClickAuctionSellItemButton", "ResetCursor", "SetCursor",
    "SpellStopTargeting", "CloseAllBags", "CloseBag", "SortBags" }
  local calls = {}
  local function wrap(tbl, k, label)
    local f = rawget(tbl, k)
    if type(f) ~= "function" then return end
    rawset(tbl, k, function(...)
      if not M.hardware then
        local info = debug.getinfo(2, "Sl")
        calls[#calls + 1] = label .. " @ " .. (info and (tostring(info.short_src):gsub("^.*TreeAI[/\\]", "") .. ":" .. tostring(info.currentline)) or "?")
      end
      return f(...)
    end)
  end
  for _, k in ipairs(CURSOR) do wrap(_G, k, k) end
  for _, ns in ipairs({ "C_Container", "C_Item", "C_Spell" }) do
    local t = rawget(_G, ns)
    if type(t) == "table" then for _, k in ipairs(CURSOR) do wrap(t, k, ns .. "." .. k) end end
  end

  -- 2) TreeAI frames the player can have on the right side (4.0.4 defaults or one click away): the Suivi TreeAI
  --    (storm: on), the « À faire » window (RIGHT -60, 40), the compact tracker, the important-alert box (HIGH strata,
  --    « Fais de la place : sacs pleins… » — exactly when the player sorts his bags)
  local mods = T.modules or {}
  local strataBefore = {}
  local function showRisk()
    local Td = mods.Todo
    if Td and Td.CreateFrame then
      pcall(Td.CreateFrame)
      local f = rawget(_G, "TreeAITodoFrame")
      if f then f:ClearAllPoints() f:SetPoint("RIGHT", UIP, "RIGHT", -60, 40) f:Show() end   -- its default place
    end
    local al = T.settings and T.settings.alert
    if al then al.pos, al.corner = nil, "TOPRIGHT" end   -- its default corner: under the minimap, right column
    if T.ShowAlert then T:ShowAlert({ text = "Fais de la place : sacs pleins, le butin est perdu", prio = 3, id = "bagclick" }, true) end
    local al = T.GetAlert and T:GetAlert()
    if al then al.left = 600 end   -- the box stays (mouse over it = it never fades)
  end
  showRisk()
  for _, n in ipairs({ "TreeAITodoFrame", "TreeAIAlert", "TreeAIWatchFrame" }) do
    local f = rawget(_G, n)
    if f then strataBefore[n] = f:GetFrameStrata() end
  end

  -- 3) the bags, opened like the default UI (bottom-right stack) ----------------------------------------------
  local SW, SH = geo.Screen()
  local bags = {}
  local prev
  for i = 1, 5 do
    local cf = rawget(_G, "ContainerFrame" .. i) or CreateFrame("Frame", "ContainerFrame" .. i, UIP)
    cf:SetID(i - 1)
    cf:SetFrameStrata("MEDIUM")
    cf.__w, cf.__h = 178, (i == 1) and 240 or 200
    cf:ClearAllPoints()
    if prev then cf:SetPoint("BOTTOMRIGHT", prev, "TOPRIGHT", 0, 6) else cf:SetPoint("BOTTOMRIGHT", UIP, "BOTTOMRIGHT", -8, 92) end
    if i == 4 then cf:ClearAllPoints() cf:SetPoint("BOTTOMRIGHT", bags[1], "BOTTOMLEFT", -10, 0) end
    cf:Show()
    for j = 1, 16 do
      local b = rawget(_G, cf:GetName() .. "Item" .. j) or CreateFrame("Button", cf:GetName() .. "Item" .. j, cf)
      b:SetID(j)
      b.__w, b.__h = 37, 37
      b:ClearAllPoints()
      b:SetPoint("TOPLEFT", cf, "TOPLEFT", 8 + ((j - 1) % 4) * 41, -30 - math.floor((j - 1) / 4) * 41)
      b:Show()
    end
    bags[i] = cf
    prev = (i ~= 4) and cf or prev
  end
  local bagBar = CreateFrame("Frame", "MicroButtonAndBagsBar", UIP)
  bagBar.__w, bagBar.__h = 300, 88
  bagBar:SetPoint("BOTTOMRIGHT", UIP, "BOTTOMRIGHT", 0, 0)
  for _, fn in ipairs({ "OpenAllBags", "ToggleBackpack" }) do local f = rawget(_G, fn) if type(f) == "function" then pcall(f) end end
  ctx.fire("BAG_UPDATE_DELAYED")
  M.Advance(1, 0.1)

  -- a drag: pick up an item (lock + cursor), move it, drop it (bag update) — out of combat, and again with the
  -- bank, the merchant and the mailbox open (the profession assistant reads them)
  local function drag()
    ctx.fire("ITEM_LOCK_CHANGED", 0, 3); ctx.fire("CURSOR_CHANGED", false, 1, 0); ctx.fire("CURSOR_UPDATE")
    M.Advance(0.3, 0.1)
    ctx.fire("ITEM_LOCK_CHANGED", 0, 3); ctx.fire("BAG_UPDATE", 0); ctx.fire("CURSOR_CHANGED", true, 0, 1)
    ctx.fire("BAG_UPDATE_DELAYED"); ctx.fire("UNIT_INVENTORY_CHANGED", "player")
    M.Advance(1.5, 0.1)
  end
  drag()
  M.SetBank(true) M.Advance(0.5, 0.1) drag() M.SetBank(false)
  M.SetMerchant(true) M.Advance(0.5, 0.1) drag() M.SetMerchant(false)
  ctx.fire("MAIL_SHOW") drag() ctx.fire("MAIL_CLOSED")
  ctx.fire("QUEST_LOG_UPDATE") M.Advance(3, 0.5)
  drag()

  local seen = {}
  for _, c in ipairs(calls) do
    -- the merchant's auto-sell (QoL / Quick « vendre les gris ») is UseContainerItem, not listed above: never a cursor call
    if not seen[c] then seen[c] = true check(false, "curseur touché sans clic du joueur : " .. c) end
  end

  -- 4) every TreeAI frame that takes the mouse, shown (alpha 0 included), and its rect -------------------------
  local RANK = { BACKGROUND = 1, LOW = 2, MEDIUM = 3, HIGH = 4, DIALOG = 5, FULLSCREEN = 6, FULLSCREEN_DIALOG = 7, TOOLTIP = 8 }
  local MOUSE_KIND = { Button = true, CheckButton = true, EditBox = true, Slider = true, ItemButton = true }
  local MOUSE_SCRIPT = { "OnEnter", "OnLeave", "OnMouseDown", "OnMouseUp", "OnClick", "OnDragStart", "OnReceiveDrag",
    "OnMouseWheel", "OnDoubleClick" }
  local blizz = {}
  for i = 1, (M.firstAddonFrame or 1) - 1 do blizz[M.frames[i]] = true end
  for _, b in ipairs(bags) do blizz[b] = true end
  local function ours(f)
    local w, d = f, 0
    while w and d < 40 do
      if blizz[w] or w == bagBar then return false end
      if w == UIP then return true end
      w, d = rawget(w, "__parent"), d + 1
    end
    return true
  end
  local function takesMouse(f)
    local m = rawget(f, "__mouse")
    if m ~= nil then return m end
    if MOUSE_KIND[rawget(f, "__kind")] then return true end
    for _, s in ipairs(MOUSE_SCRIPT) do if f.__scripts[s] then return true end end
    return false
  end
  local function alpha(f)
    local a, w, d = 1, f, 0
    while w and d < 40 do a = a * (tonumber(rawget(w, "__alpha")) or 1) w, d = rawget(w, "__parent"), d + 1 end
    return a
  end
  local function label(f)
    local n = rawget(f, "__name")
    local w, path = f, {}
    while not n and w and #path < 6 do
      w = rawget(w, "__parent")
      path[#path + 1] = rawget(w or {}, "__kind") or "?"
      n = w and rawget(w, "__name")
    end
    return (n or "?") .. (#path > 0 and (" >" .. #path .. " " .. tostring(rawget(f, "__kind"))) or "")
  end
  -- the bag area: union of the open container frames + the bag bar (UIParent units)
  local area = {}
  for _, b in ipairs(bags) do area[#area + 1] = geo.Rect(b) end
  area[#area + 1] = geo.Rect(bagBar)
  local function overlaps(r, a)
    return r.l < a.l + a.w - 1 and a.l < r.l + r.w - 1 and r.b < a.b + a.h - 1 and a.b < r.b + r.h - 1
  end
  local takers, covering = 0, {}
  for i = M.firstAddonFrame or 1, #M.frames do
    local f = M.frames[i]
    if ours(f) and rawget(f, "__kind") ~= "GameTooltip" and f:IsVisible() and takesMouse(f) then
      local r = geo.Rect(f)
      if r and r.w > 0 and r.h > 0 then
        takers = takers + 1
        if dump then
          io.write(("MOUSE %-48s %5d,%-5d %4dx%-4d alpha %.2f strata %s"):format(label(f), r0(r.l), r0(r.b), r0(r.w), r0(r.h),
            alpha(f), tostring(f:GetFrameStrata())) .. "\n")
        end
        for _, a in ipairs(area) do
          -- under the bags (lower strata) = the bags get the click; same strata or above = our frame may take it
          if a and overlaps(r, a) and (RANK[f:GetFrameStrata()] or 3) >= RANK.MEDIUM then
            covering[#covering + 1] = ("%s (%d,%d %dx%d, alpha %.2f)"):format(label(f), r0(r.l), r0(r.b), r0(r.w), r0(r.h), alpha(f))
            break
          end
        end
      end
    end
  end
  for _, c in ipairs(covering) do check(false, "cadre TreeAI qui prend la souris sur les sacs : " .. c) end
  -- alpha-0 frames left shown that take the mouse anywhere (an invisible click trap)
  for i = M.firstAddonFrame or 1, #M.frames do
    local f = M.frames[i]
    if ours(f) and f:IsVisible() and takesMouse(f) and alpha(f) < 0.05 and geo.Rect(f) then
      check(false, "cadre TreeAI invisible (alpha 0) mais montré qui prend la souris : " .. label(f))
    end
  end
  -- 6) a TreeAI frame closed in a fade whose OnHide opens another one in a fade (Theme): no « invalid key to 'next' »,
  --    the second one ends fully visible — never left shown at alpha 0 (an invisible click trap)
  do
    local Th = T.Theme
    local a = CreateFrame("Frame", "TreeAIBagclickA", UIP)
    local b = CreateFrame("Frame", "TreeAIBagclickB", UIP)
    b:EnableMouse(true) b:Hide()
    Th:Window(b, { id = "bagclickb", header = false })
    a:SetScript("OnHide", function() b:Show() end)
    Th:FadeOut(a, 0.1)
    M.Advance(1, 0.05)
    check(not a:IsShown() and b:IsShown() and b:GetAlpha() > 0.99 and not Th:IsAnimating(b),
      ("fondu enchaîné : b montré à alpha %.2f (attendu 1)"):format(b:GetAlpha()))
    b:Hide()
  end

  -- 5) bags closed: our frames get their strata back
  for _, b in ipairs(bags) do b:Hide() end
  for _, fn in ipairs({ "CloseAllBags" }) do local f = rawget(_G, fn) if type(f) == "function" then pcall(f) end end
  ctx.fire("BAG_UPDATE_DELAYED")
  M.Advance(2, 0.1)
  for n, st in pairs(strataBefore) do
    local f = rawget(_G, n)
    check(f:GetFrameStrata() == st, ("%s : strate rendue à la fermeture des sacs (%s, attendu %s)"):format(n, tostring(f:GetFrameStrata()), st))
  end
  local BG = T.BagGuard
  check(BG and BG.moved > 0, "garde des sacs : des cadres TreeAI sont passés sous les sacs ouverts")
  check(BG and next(BG.lowered) == nil, "garde des sacs : plus rien de rabaissé, sacs fermés")
  io.write(("BAGCLICK takers=%d covering=%d cursorCalls=%d screen=%dx%d"):format(takers, #covering, #calls, r0(SW), r0(SH)) .. "\n")
end
