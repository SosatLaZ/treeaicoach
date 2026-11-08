-- Scenario for Modules/Team.lua: 3 party members WITHOUT TreeAI (Blizzard APIs only). Fake names only.
-- lua5.1 tests/wow/harness.lua --scenario tests/wow/team_scenario.lua --class HUNTER wow_addon/TreeAI
return function(M, ctx)
  local T = TreeAI
  local check = ctx.check
  local Tm = T and T.modules and T.modules.Team
  check(Tm and Tm.db and Tm.db.enabled, "Team registered + enabled")
  if not Tm then return end
  local cls = T:PlayerClass()
  M.LeaveCombat()
  T:ApplyPreset("tout")

  local function text()
    local out = {}
    for _, l in ipairs(T:GetTabLines("group")) do
      if l.source == "Team" then out[#out + 1] = T.PlainText(l.text) end
    end
    return table.concat(out, "\n")
  end
  local function has(pat) return text():find(pat) ~= nil end
  local function settle(sec)
    Tm._test.reset()
    ctx.fire("GROUP_ROSTER_UPDATE")
    ctx.fire("QUEST_LOG_UPDATE")
    M.Advance(sec or 4, 0.5)
  end

  -- solo: nothing
  M.group = {}
  settle()
  check(not has("Bravo"), "solo: no Team line")

  M.AddItem(99101, { name = "Hache-test rouillée", quality = 0, ilvl = 5, subType = "Haches à une main", equipLoc = "INVTYPE_WEAPON" })
  M.AddItem(99102, { name = "Casque-test", quality = 2, ilvl = 22, equipLoc = "INVTYPE_HEAD" })
  M.quests = {
    { id = 7001, title = "Sang-test de lion", level = 26, objectives = {} },
    { id = 7002, title = "Quête-test rouge", level = 30, objectives = {} },
    { id = 7003, title = "Quête-test finie", level = 25, complete = true, objectives = {} },
  }
  local gear = {}
  for _, s in ipairs({ 1, 2, 3, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 17, 18 }) do gear[s] = 99102 end
  gear[16] = 99101   -- grey weapon, no chest (5)
  M.group = {
    { name = "Bravo-test", class = "DRUID", level = 24, online = true, role = "HEALER", power = 30, powerMax = 100,
      quests = { [7001] = true, [7002] = true } },
    { name = "Charlie-test", class = "PRIEST", level = 27, online = true, health = 40, healthMax = 100,
      quests = { [7002] = true, [7003] = true } },
    -- (Bravo / Charlie: no gear table → the mock never answers the inspect: retried later, never blocking)
    { name = "Delta-test", class = "WARRIOR", level = 25, online = true, inRange = false, powerType = 1, gear = gear },
  }
  M.auras.party2 = { { name = "Venin-test", kind = "debuff", debuffType = "Poison", spellId = 99001 } }
  M.auras.party1, M.auras.party3 = {}, {}
  settle(4)
  -- status lines (no TreeAI → read from the game)
  check(has("Bravo%-test · Druide 24 · soigneur · PV 100 %% · mana 30 %%"), "status Bravo:\n" .. text())
  check(has("Delta%-test · Guerrier 25 · tank · PV 100 %% · loin"), "status Delta (far, rage → no mana)")
  -- quests
  check(has("Charlie%-test n'a pas « Sang%-test de lion » : partage%-la %(Journal > Partager%)"), "share line")
  check(not has("Quête%-test finie"), "completed quest not suggested")
  check(has("Quêtes rouges pour Bravo%-test %(niv 24%) : « Quête%-test rouge »"), "red quest for the level 24")
  -- buffs / cleanse from another member (player is not a druid)
  if cls ~= "DRUID" then
    check(has("Charlie%-test n'a pas Marque du fauve : Bravo%-test %(druide%) peut la donner"), "buff another member can give")
    check(has("Charlie%-test : poison à retirer, Bravo%-test %(druide%) peut le faire"), "cleanse by another member")
  end
  -- gear by inspect (out of combat, one at a time)
  M.Advance(36, 1)   -- one member per 10 s: Bravo, Charlie, then Delta
  check(has("Delta%-test a encore une arme grise %(Haches à une main%)"), "grey weapon:\n" .. text())
  check(has("Delta%-test n'a rien en : torse"), "missing chest")
  local n = M.inspects or 0
  M.Advance(30, 1)
  check((M.inspects or 0) - n <= 4, "inspect ≤ 1 / 10 s (" .. ((M.inspects or 0) - n) .. ")")
  -- pull readiness
  M.target = { exists = true, name = "Ours-test", level = 26, hostile = true, guid = "Creature-0-0-0-0-7-1" }
  ctx.fire("PLAYER_TARGET_CHANGED")
  M.Advance(2, 0.5)
  check(has("Avant de tirer, attends : Bravo%-test 30 %% mana, Charlie%-test 40 %% PV, Delta%-test loin"), "pull line:\n" .. text())
  local pinned
  for _, l in ipairs(T:GetTabLines("group")) do if l.pin and T.PlainText(l.text):find("Avant de tirer") then pinned = true end end
  check(pinned, "pull line competes for « Maintenant »")
  -- combat: secret values never read, no error
  M.EnterCombat()
  ctx.fire("PLAYER_REGEN_DISABLED")
  for _ = 1, 20 do ctx.fire("UNIT_HEALTH", "party1"); ctx.fire("UNIT_AURA", "party2") end
  M.Advance(3, 0.5)
  check(not has("Avant de tirer"), "no pull line in combat")
  M.LeaveCombat()
  ctx.fire("PLAYER_REGEN_ENABLED")
  M.target = { exists = false }
  ctx.fire("PLAYER_TARGET_CHANGED")
  M.Advance(3, 0.5)

  -- class-specific: buffs / dispels the player can cast
  if cls == "PRIEST" then
    M.spells[1243] = { name = "Mot de pouvoir : Robustesse", known = true, icon = 1 }
    M.spells[528] = { name = "Guérison des maladies", known = true, icon = 1 }
    M.auras.party1 = { { name = "Peste-test", kind = "debuff", debuffType = "Disease", spellId = 99002 } }
    ctx.fire("UNIT_AURA", "party1")
    settle(4)
    check(has("Lance Mot de pouvoir : Robustesse sur Bravo%-test"), "priest buff line:\n" .. text())
    check(has("Dissipe Bravo%-test : Maladie"), "priest dispel line")
  end

  -- merge with Squad: a member who runs TreeAI keeps only what Squad / Missions do not say
  local SQ = T.Squad
  if SQ then
    local oldS, oldA = SQ.State, SQ.IsActive
    SQ.State = function(self, name) if name == "Charlie-test" then return { l = 27 } end return oldS(self, name) end
    SQ.IsActive = function() return true end
    settle(4)
    check(not has("Charlie%-test n'a pas « Sang"), "TreeAI member: quest share left to Squad")
    check(not has("Charlie%-test · Prêtre"), "TreeAI member: status left to Squad")
    if cls ~= "DRUID" then check(has("Charlie%-test : poison à retirer"), "TreeAI member: cleanse still from Team") end
    SQ.State, SQ.IsActive = oldS, oldA
  end

  -- disable: block removed
  T:SetModuleEnabled("Team", false)
  M.Advance(1, 0.5)
  check(not has("Bravo%-test"), "module off: lines gone")
  T:SetModuleEnabled("Team", true)
  M.group = {}
  settle(3)
  T:ApplyPreset("simple")
end
