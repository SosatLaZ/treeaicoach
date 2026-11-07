-- Scenario for Modules/Boost.lua (run by tests/wow/test_boost.py on the real addon, fake names only).
return function(M, ctx)
  local T = TreeAI
  local B = T and T.modules and T.modules.Boost
  ctx.check(B and B.db and B.db.enabled, "Boost registered + enabled")
  if not B then return end
  local S = T._suggestions
  M.LeaveCombat()
  T.settings.soundTips = true  -- rare sound is a prio-2 tip: needs the « sons aussi pour les conseils » option

  -- 1. rare on a nameplate: line + sound, once per GUID; click = skull out of combat
  M.units.nameplate1 = { name = "Croc-test", guid = "Creature-0-0-0-0-99-0001", classification = "rare",
    hostile = true, level = 27 }
  local snd = #M.sounds
  ctx.fire("NAME_PLATE_UNIT_ADDED", "nameplate1")
  ctx.check(S["Boost:rare"] and S["Boost:rare"].text:find("Rare : Croc%-test"), "rare suggestion")
  ctx.check(#M.sounds > snd, "rare sound played")
  snd = #M.sounds
  ctx.fire("NAME_PLATE_UNIT_ADDED", "nameplate1")
  ctx.check(#M.sounds == snd, "no second sound for the same GUID")
  if S["Boost:rare"] then S["Boost:rare"].onClick() end
  ctx.check(M.raidTargets and M.raidTargets.nameplate1 == 8, "skull marker on click")

  -- 2. quest rewards: leather agi beats cloth int / plate for a level-26 hunter (equipped: leather agi 5)
  M.AddItem(90001, { name = "Gilet porté", classID = 4, subclassID = 2, equipLoc = "INVTYPE_CHEST", quality = 2,
    sellPrice = 100, stats = { ITEM_MOD_AGILITY_SHORT = 5 } })
  M.AddItem(90002, { name = "Robe de tissu", classID = 4, subclassID = 1, equipLoc = "INVTYPE_ROBE", quality = 2,
    sellPrice = 900, stats = { ITEM_MOD_INTELLECT_SHORT = 8 } })
  M.AddItem(90003, { name = "Gilet agile", classID = 4, subclassID = 2, equipLoc = "INVTYPE_CHEST", quality = 2,
    sellPrice = 300, stats = { ITEM_MOD_AGILITY_SHORT = 10 } })
  M.AddItem(90004, { name = "Cuirasse lourde", classID = 4, subclassID = 4, equipLoc = "INVTYPE_CHEST", quality = 2,
    sellPrice = 5000, stats = { ITEM_MOD_STRENGTH_SHORT = 20 } })
  M.inventory[5] = { id = 90001, count = 1 }
  M.questChoices = { 90004, 90002, 90003 }
  local best = B:RankQuestRewards()
  ctx.check(best == 3, "best reward = leather agi (got " .. tostring(best) .. ")")
  local btn = CreateFrame("Button", "QuestInfoRewardsFrameQuestInfoItem3", UIParent)
  ctx.fire("QUEST_COMPLETE")
  M.Advance(3)
  local glowed = false
  for _, f in ipairs(M.frames) do
    local p = f.__points and f.__points[1]
    -- the glow: shown, anchored to a reward button, never its child (taint rule)
    if f:IsShown() and f.__parent == UIParent and p and type(p[2]) == "table" and p[2] ~= UIParent
        and f.label then glowed = true end
  end
  ctx.check(glowed, "best reward button highlighted (glow anchored to it, never parented: no taint)")
  ctx.check(S["Boost:reward"] and S["Boost:reward"].text:find("Gilet agile"), "reward suggestion: "
    .. tostring(S["Boost:reward"] and S["Boost:reward"].text))
  M.questChoices = { 90004, 90002 } -- no upgrade -> highest vendor price
  local i, v, why = B:RankQuestRewards()
  ctx.check(i == 1 and why == "sell" and v == 5000, "fallback to vendor price")
  ctx.fire("QUEST_FINISHED")

  -- 3. tooltip lines (legacy OnTooltipSetItem path: the mock has no TooltipDataProcessor)
  GameTooltip.__item, GameTooltip.__lines = 90003, {}
  local fn = GameTooltip:GetScript("OnTooltipSetItem")
  ctx.check(fn, "tooltip hooked")
  if fn then
    fn(GameTooltip)
    local joined = table.concat(GameTooltip.__lines, " | ")
    ctx.check(joined:find("Amélioration %+100 %%"), "upgrade line: " .. joined)
    ctx.check(joined:find("Vendre : 3 pa"), "vendor line: " .. joined)
    GameTooltip.__item, GameTooltip.__lines = 90004, {}
    fn(GameTooltip)
    ctx.check(table.concat(GameTooltip.__lines, " "):find("armure non portable"), "plate refused for a hunter")
    GameTooltip.__item = nil
  end

  -- 4. threat in a group, non-tank hunter; silent solo
  M.group = { { name = "Amie", class = "PRIEST", level = 25, online = true } }
  M.target = { exists = true, name = "Ours", level = 26, hostile = true, guid = "Creature-0-0-0-0-5-1" }
  M.threat = { pct = 92, status = 2 }
  B:CheckThreat()
  ctx.check(S["Boost:threat"] and S["Boost:threat"].text:find("Lâche un peu : 92 %% de menace"), "threat warning")
  M.group = {}
  B:CheckThreat()
  ctx.check(not S["Boost:threat"], "silent when solo")
  M.threat = nil

  -- 5. XP engine
  local xp, bonus, kill = B.ParseXP("Loup meurt, vous gagnez 150 points d'expérience. (+75 points d'expérience bonus de repos)")
  ctx.check(xp == 150 and bonus == 75 and kill, "FR XP parse")
  xp, bonus, kill = B.ParseXP("Wolf dies, you gain 80 experience. (+40 exp Rested bonus)")
  ctx.check(xp == 80 and bonus == 40 and kill, "EN XP parse")
  xp, bonus, kill = B.ParseXP("Expérience gagnée : 1500.")
  ctx.check(xp == 1500 and not kill, "quest XP parse")
  B.char.xpKills, B.char.xpQuests = {}, {}
  for _ = 1, 3 do
    ctx.fire("CHAT_MSG_COMBAT_XP_GAIN", "Loup meurt, vous gagnez 200 points d'expérience. (+100 points d'expérience bonus de repos)")
  end
  ctx.fire("QUEST_TURNED_IN", 1003, 2000, 50)
  M.player.xp, M.player.xpMax, M.player.rested = 10000, 26000, 4000
  local r = B:XPStats()
  -- base 100 ; rested 4000 < 16000/2 -> (16000-4000)/100 = 120 kills ; 16000/2000 = 8 quests
  ctx.check(r and r.kills == 120 and r.quests == 8, "kills/quests to level: " .. tostring(r and r.kills) .. "/" .. tostring(r and r.quests))
  ctx.check((B:XPText() or ""):find("~120 monstres ou ~8 quêtes avant niv"), "xp text " .. tostring(B:XPText()))

  -- 6. hunter travel aspect (shared id Hunter:aspect), cleared in combat
  M.spells[5118] = { name = "Aspect du guépard", known = true }
  M.speed = 7
  for _ = 1, 7 do B:CheckTravel(); M.Advance(1) end
  ctx.check(S["Hunter:aspect"] and S["Hunter:aspect"].text:find("guépard"), "travel aspect reminder")
  M.EnterCombat(); B:CheckTravel()
  ctx.check(not S["Hunter:aspect"] or S["Hunter:aspect"].module ~= "Boost", "travel reminder cleared in combat")
  M.LeaveCombat(); M.speed = 0

  -- 7. pet advisor (wolf 25, empty pet book -> Morsure rank 4)
  local adv = B:PetAdvice()
  ctx.check(adv and adv:find("Morsure rang 4"), "pet advice: " .. tostring(adv))

  -- 8. hearthstone ready + full bags
  M.AddItem(6948, { name = "Pierre de foyer" })
  for b = 0, 4 do
    M.bags[b] = M.bags[b] or { size = 16, slots = {} }
    for s = 1, M.bags[b].size do if not M.bags[b].slots[s] then M.SetBagItem(b, s, 6948, 1) end end
  end
  B:CheckReminders()
  ctx.check(S["Boost:hearth"] and S["Boost:hearth"].text:find("Pierre de foyer prête"), "hearth reminder")

  -- 9. skinning hint
  M.skills = { { name = "Dépeçage", rank = 130 } }
  M.units.mouseover = { name = "Loup mort", dead = true, creatureType = "Bête", level = 24 }
  local tip = { lines = {}, AddLine = function(self, t) self.lines[#self.lines + 1] = t end }
  M.Advance(11)
  B.DecorateUnit(tip, "mouseover")
  ctx.check((tip.lines[1] or ""):find("peut dépecer"), "skinning hint: " .. tostring(tip.lines[1]))

  -- compact summary for the AI
  T:Fire("SUMMARY")
  local sb = T:Summary().boost
  ctx.check(sb and sb.raresSeen == 1 and sb.killsToLevel == 120, "summary.boost written")
end
