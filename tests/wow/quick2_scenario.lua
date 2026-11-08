-- QUICK2 scenario: « Raccourcis » bar (Modules/QuickFight.lua). Secure buttons appear only when useful, are
-- created / shown / moved only out of combat, nothing is blocked in combat (strict mock), the downtime timer and
-- the pull-ready indicator, ammo purchase only on a click at an open merchant. Fake names only.
return function(M, ctx)
  local T = TreeAI
  local Q = T and T.QuickFight
  ctx.check(Q ~= nil, "QuickFight module loaded")
  if not Q then return end
  local hunter = T:PlayerClass() == "HUNTER"
  local function hc(cond, msg) if hunter then ctx.check(cond, msg) end end
  local hunterKeys = { pull = true, pet = true, mend = true, feed = true, aspect = true }
  local function noHunterButton(P)
    if hunter or not P then return end
    for k in pairs(P.buttons) do ctx.check(not hunterKeys[k], "non-hunter: no hunter button (" .. k .. ")") end
  end
  local function blockedNow() return #M.blocked end
  local function settle() M.Advance(1) end
  local function shown(key)
    local b = Q._buttons[key]
    return b and b:IsShown() and true or false
  end
  M.LeaveCombat()
  M.Advance(2)

  -- pure helpers
  ctx.check(Q.EatDuration(61) == 18 and Q.EatDuration(552) == 24 and Q.EatDuration(1933) == 30, "EatDuration tiers")
  ctx.check(math.abs(Q.RestTime(276, 552) - 12) < 0.01, "RestTime: half a 552 food = 12 s")
  ctx.check(Q.RestTime(0, 552) == 0, "RestTime: no deficit")

  -- full health, happy pet, no target: the bar stays quiet (no button)
  M.player.health, M.player.power = 1000, 600
  M.pet.happiness, M.pet.health = 3, 600
  M.target = { exists = false }
  table.insert(M.auras.player, { name = "Aspect du faucon", spellId = 13165, duration = 0, expirationTime = 0, kind = "buff" })
  Q:InvalidatePicks()
  Q:Refresh()
  settle()
  local P = Q._lastPlan
  ctx.check(P and next(P.buttons) == nil, "full + happy: no button (" .. tostring(P and next(P.buttons)) .. ")")

  -- low HP + low mana out of combat: Boire / Manger appears with a secure macro, rest timer text
  M.player.health, M.player.power = 400, 150
  Q:Refresh()
  settle()
  P = Q._lastPlan
  ctx.check(P.buttons.eat ~= nil, "low HP/mana: eat button planned")
  noHunterButton(P)
  ctx.check(shown("eat"), "eat button shown out of combat")
  local eb = Q._buttons.eat
  ctx.check(eb and eb:GetAttribute("type") == "macro" and tostring(eb:GetAttribute("macrotext")):find("/use item:"),
    "eat button: secure macro with /use item: (" .. tostring(eb and eb:GetAttribute("macrotext")) .. ")")
  ctx.check(eb and eb.__protected, "eat button is a SecureActionButtonTemplate")
  ctx.check(P.status and P.status:find("^Repos %d+ s : "), "rest timer text: " .. tostring(P.status))
  ctx.check(not P.ready, "not ready while low")

  -- unhappy pet + food in bags: Nourrir le familier
  M.pet.happiness = 2
  Q:InvalidatePicks()
  Q:Refresh()
  settle()
  P = Q._lastPlan
  local H = T:GetModule("Hunter")
  local food = H and H:BestFood()
  if food then
    ctx.check(P.buttons.feed and P.buttons.feed.macro:find("/use item:" .. food), "feed button uses the best pet food")
  else
    ctx.note = ctx.note
  end
  hc(P.reasons and table.concat(P.reasons, ","):find("familier pas content"), "reason: pet unhappy")

  -- pet hurt: Soigner le familier
  M.pet.health = 200
  Q:Refresh()
  settle()
  hc(Q._lastPlan.buttons.mend ~= nil and shown("mend"), "mend button when pet at 33 %")

  -- hostile target: pull (mark + pet attack)
  M.target = { exists = true, name = "Ours gris", guid = "Creature-0-0-0-0-1186-1", hostile = true, level = 25 }
  ctx.fire("PLAYER_TARGET_CHANGED")
  Q:Refresh()
  settle()
  local pull = Q._lastPlan.buttons.pull
  hc(pull and pull.macro:find("Marque du chasseur") and pull.macro:find("/petattack"), "pull macro: mark + petattack")

  -- combat: no Show / Hide / SetPoint / SetAttribute on secure buttons, only alpha
  local b0 = blockedNow()
  local v0 = #M.violations
  M.EnterCombat()
  M.player.health = 300
  M.pet.health = 600
  for _ = 1, 5 do
    ctx.fire("UNIT_HEALTH", "player")
    ctx.fire("UNIT_HEALTH", "pet")
    ctx.fire("UNIT_AURA", "player")
    ctx.fire("PLAYER_TARGET_CHANGED")
    M.Advance(1)
    Q:Refresh()
  end
  ctx.check(blockedNow() == b0, "no blocked action in combat (" .. (blockedNow() - b0) .. ")")
  local prot = 0
  for i = v0 + 1, #M.violations do if M.violations[i].kind == "protected" or M.violations[i].kind == "blocked" then prot = prot + 1 end end
  ctx.check(prot == 0, "no protected violation in combat")
  ctx.check(Q._lastPlan.status == nil, "no status text in combat")
  M.LeaveCombat()
  M.Advance(2)

  -- dead pet: Ressusciter le familier
  M.pet.dead, M.pet.health = true, 0
  M.target = { exists = false }
  Q:Refresh()
  settle()
  local pb = Q._lastPlan.buttons.pet
  hc(pb and pb.macro:find("Ressusciter le familier"), "pet dead: revive macro (" .. tostring(pb and pb.macro) .. ")")
  M.pet.dead, M.pet.health = false, 600

  -- bandage beats eating when only a little HP is missing and no mana is needed
  M.AddItem(3530, { name = "Bandage en laine", stack = 20, type = "Consommable", subType = "Bandage", classID = 0, subclassID = 7 })
  M.player.health, M.player.power = 600, 600
  Q:InvalidatePicks()
  local s = Q:Snapshot()
  s.picks = { food = 1119, bandage = 3531 }   -- 552-HP food (24 s), heavy wool bandage (301 HP)
  s.hp, s.hpMax = 700, 1000                    -- 300 missing: 13 s of eating vs 8 s of bandage
  local P2 = Q:Plan(s, function(k) if k == "hp" then return 90 end return Q.db[k] end)
  ctx.check(P2.bandWins and P2.buttons.bandage and not P2.buttons.eat, "bandage wins over food for 300 HP")
  ctx.check(P2.buttons.bandage and P2.buttons.bandage.macro:find("%[@player%] item:3531"), "bandage macro targets self")
  s.bandaged = true
  local P3 = Q:Plan(s, function(k) if k == "hp" then return 90 end return Q.db[k] end)
  ctx.check(not P3.buttons.bandage and P3.buttons.eat, "Recently Bandaged: back to eating")

  -- eating: countdown « plein dans N s »
  s.bandaged = nil
  s.eating, s.eatLeft = true, 20
  local P4 = Q:Plan(s, function(k) if k == "hp" then return 90 end return Q.db[k] end)
  ctx.check(P4.status and P4.status:find("plein dans %d+ s"), "eating: countdown text " .. tostring(P4.status))

  -- ready: « Prêt à engager »
  M.player.health, M.player.power = 1000, 600
  M.pet.happiness, M.pet.health = 3, 600
  Q:Refresh()
  ctx.check(Q._lastPlan.ready, "ready when full and pet happy")

  -- options: a disabled button never appears
  Q.db.eat = false
  M.player.health, M.player.power = 400, 100
  Q:Refresh()
  ctx.check(Q._lastPlan.buttons.eat == nil, "option eat=false hides the eat button")
  Q.db.eat = true

  -- merchant: ammo purchase only on a click
  local bought = {}
  M.AddItem(2504, { name = "Arc renforcé", type = "Arme", subType = "Arcs", classID = 2, subclassID = 2, equipLoc = "INVTYPE_RANGED" })
  M.inventory[18] = { id = 2504, dur = 50, durMax = 60 }
  M.player.money = 100000
  rawset(_G, "GetMerchantNumItems", function() return 2 end)
  rawset(_G, "GetMerchantItemID", function(i) return i == 1 and 2515 or 4540 end)
  rawset(_G, "GetMerchantItemInfo", function(i)
    if i == 1 then return "Flèche acérée", 132382, 100, 200, -1 end
    return "Pain", 1, 25, 1, -1
  end)
  rawset(_G, "GetMerchantItemMaxStack", function() return 200 end)
  rawset(_G, "BuyMerchantItem", function(i, q)
    if not M.hardware and not T:InUserAction() then M.Violation("automation", "BuyMerchantItem outside a click") end
    bought[#bought + 1] = { i, q }
  end)
  Q.db.ammoBuy = 1000
  M.SetMerchant(true)
  M.Advance(1)
  hc(Q._ammoOffer and Q._ammoOffer.id == 2515, "merchant: arrows found")
  ctx.check(#bought == 0, "nothing bought without a click")
  local need = Q:AmmoToBuy()
  ctx.check(need > 0 and need % 200 == 0, "ammo to buy rounded to stacks: " .. need)
  local ab = Q.GetAmmoButton()
  hc(ab and ab:IsShown(), "ammo button shown at the merchant")
  if ab then ab:Click() end
  local total = 0
  for _, r in ipairs(bought) do total = total + r[2] end
  hc(total == need, "click bought " .. total .. " / " .. need)
  if not hunter then ctx.check(#bought == 0 and not Q._ammoOffer, "non-hunter: no ammo offer") end
  noHunterButton(Q._lastPlan)
  M.SetMerchant(false)
  M.Advance(1)
  ctx.check(not (ab and ab:IsShown()), "ammo button hidden after the merchant closes")

  -- real client: PLAYER_REGEN_DISABLED arrives just before the lockdown → combat buttons laid out then
  M.player.health, M.player.power = 1000, 600
  M.pet.health = 200
  Q:Refresh()
  local v1 = #M.violations
  ctx.fire("PLAYER_REGEN_DISABLED")
  hc(shown("mend") and not shown("eat"), "combat prep: mend kept, eat hidden")
  ctx.check(#M.violations == v1, "combat prep: no violation")
  M.pet.health = 600
  M.Advance(1)

  -- Fight's « Avant d'engager » line yields to the bar
  ctx.check(Q:OwnsPrePull(), "QuickFight owns the pre-pull indicator")
  rawset(_G, "GetMerchantNumItems", function() return 0 end)
  for _, n in ipairs({ "GetMerchantItemID", "GetMerchantItemInfo", "GetMerchantItemMaxStack", "BuyMerchantItem" }) do
    rawset(_G, n, nil)
  end
end
