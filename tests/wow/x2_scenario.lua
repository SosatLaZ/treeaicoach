-- Scenario for Modules/Fight.lua, HunterPro.lua, Comfort.lua (FEATURES50 26-40, 48-50). Fake names only.
return function(M, ctx)
  local T = TreeAI
  local F, HP, C = T.modules.Fight, T.modules.HunterPro, T.modules.Comfort
  ctx.check(F and F.db and F.db.enabled, "Fight registered")
  ctx.check(HP and HP.db, "HunterPro registered")
  ctx.check(C and C.db, "Comfort registered")
  if not (F and HP and C) then return end
  local S = T._suggestions
  local function sug(id) return S[id] end
  M.LeaveCombat()

  -- 26. pull counter
  M.target = { exists = true, name = "Ours-test", level = 26, hostile = true, guid = "Creature-0-0-0-0-7-1" }
  M.units.nameplate1 = { name = "Loup-a", guid = "Creature-0-0-0-0-8-1", hostile = true, level = 25 }
  M.units.nameplate2 = { name = "Loup-b", guid = "Creature-0-0-0-0-8-2", hostile = true, level = 25 }
  M.units.nameplate3 = { name = "Loup-c", guid = "Creature-0-0-0-0-8-3", hostile = true, level = 26 }
  M.units.nameplate4 = { name = "Garde-élite", guid = "Creature-0-0-0-0-9-1", hostile = true, level = 28,
    classification = "elite" }
  F:CheckPull()
  local p = sug("Fight:pull")
  ctx.check(p and p.text:find("4 monstres") and p.text:find("1 élite") and p.prio == 2, "pull: " .. tostring(p and p.text))
  ctx.check(p and p.tab == "info", "pull listed in « Infos »")

  -- 27. elite patrol: distance shrinks while standing still -> urgent (prio 3)
  local near = false
  CheckInteractDistance = function(_, i) if i == 4 then return true end return near end
  F:CheckPatrol()
  ctx.check(not sug("Fight:patrol"), "no patrol warning on first sight")
  near = true
  F:CheckPatrol()
  local pa = sug("Fight:patrol")
  ctx.check(pa and pa.prio == 3 and pa.text:find("Garde%-élite"), "patrol: " .. tostring(pa and pa.text))
  for i = 1, 4 do M.units["nameplate" .. i] = nil end

  -- 30. pre-pull check
  M.player.health = 400
  F:CheckPrePull()
  local pp = sug("Fight:prepull")
  ctx.check(pp and pp.text:find("PV 40 %%"), "prepull: " .. tostring(pp and pp.text))
  M.player.health = 1000
  F:CheckPrePull()
  ctx.check(not sug("Fight:prepull"), "prepull cleared at full HP")

  -- 28. fleeing mob (emote) + memory
  ctx.fire("CHAT_MSG_MONSTER_EMOTE", "%s tente de s'enfuir !", "Ours-test")
  ctx.check(sug("Fight:flee") and F.db.fleeSeen["Ours-test"] == 1, "flee emote")

  -- 31. interruptible heal on the target, hunter has Scatter Shot ready
  M.spells[19503] = { name = "Flèche de dispersion", rank = "", icon = 1, castTime = 0, known = true }
  UnitCastingInfo = function(u)
    if u == "target" then return "Soins rapides", "", 0, M.now * 1000, (M.now + 2) * 1000, false, 1, false, 2061 end
  end
  ctx.fire("UNIT_SPELLCAST_START", "target", "cast-1", 2061)
  local it = sug("Fight:interrupt")
  ctx.check(it and it.prio == 3 and it.text:find("Flèche de dispersion"), "interrupt: " .. tostring(it and it.text))
  ctx.fire("UNIT_SPELLCAST_STOP", "target", "cast-1", 2061)
  ctx.check(not sug("Fight:interrupt"), "interrupt cleared on stop")
  UnitCastingInfo = function() return nil end

  -- 32. crowd control timer (aura from C_UnitAuras / UnitAura) + early break
  M.auras.target = { { kind = "debuff", name = "Piège givrant", spellId = 3355, duration = 10,
    expirationTime = M.now + 10, source = "player" } }
  ctx.fire("UNIT_AURA", "target")
  ctx.check(F.ActiveCC()["Creature-0-0-0-0-7-1"], "CC tracked")
  M.Advance(7.6, 0.2)
  local ce = sug("Fight:ccend")
  ctx.check(ce and ce.prio == 3, "CC ending alert: " .. tostring(ce and ce.text))
  M.auras.target = { { kind = "debuff", name = "Piège givrant", spellId = 3355, duration = 20,
    expirationTime = M.now + 20, source = "player" } }
  ctx.fire("UNIT_AURA", "target")
  M.auras.target = {}
  ctx.fire("UNIT_AURA", "target")
  local cb = sug("Fight:ccbreak")
  ctx.check(cb and cb.prio == 3 and cb.text:find("rompu"), "CC broken early: " .. tostring(cb and cb.text))

  -- 29. death recap (UNIT_COMBAT only, no combat log) + pure advice
  M.target.classification = "elite"
  local nDeaths = #F.char.deaths
  M.EnterCombat()
  for _ = 1, 3 do ctx.fire("UNIT_COMBAT", "player", "WOUND", "", 300, 1) end
  ctx.fire("PLAYER_DEAD")
  local d = sug("Fight:death")
  ctx.check(d and d.text:find("^Tué par Ours%-test") and d.text:find("Élite trop fort")
    and type(d.tooltip) == "table" and table.concat(d.tooltip, " "):find("900"), "death recap: " .. tostring(d and d.text))
  ctx.check(#F.char.deaths == nDeaths + 1 or #F.char.deaths == 20, "death stored")
  ctx.check(F.DeathAdvice({ attackers = 3 }):find("3 monstres"), "advice: many adds")
  ctx.check(F.DeathAdvice({ attackers = 1, startHP = 40 }):find("40 %%"), "advice: low HP pull")
  M.target.classification = nil

  -- 33. pet loses aggro (threat API)
  M.threat = { tanking = true, status = 3, pct = 100 }
  F:CheckPetAggro()
  local pg = sug("Fight:petaggro")
  ctx.check(pg and pg.prio == 3, "pet aggro: " .. tostring(pg and pg.text))
  M.threat = nil
  M.LeaveCombat()

  -- 34. pet mood -> lost DPS
  ctx.check(F.MoodLoss(1, 30) == 20 and F.MoodLoss(2, 40) == 10 and F.MoodLoss(3, 50) == 0, "mood loss maths")
  M.pet.happiness = 1
  F:CheckPetMood()
  ctx.check(not sug("Fight:mood"), "pet mood: the Hunter module owns the feed line (one line per topic)")
  local hunterMod = T.modules and T.modules.Hunter
  if hunterMod and hunterMod.db then hunterMod.db.enabled = false end
  F:CheckPetMood()
  ctx.check(sug("Fight:mood") and sug("Fight:mood").text:find("mécontent"), "pet mood line (Hunter module off)")
  if hunterMod and hunterMod.db then hunterMod.db.enabled = true end
  M.pet.happiness = 3
  F:CheckPetMood()
  ctx.check(not sug("Fight:mood"), "mood cleared when happy")

  -- 35. shot tips
  local tipText, tipMore = F.ShotTip(2.9, { arcane = "Tir des arcanes", aimed = "Visée" })
  local more = table.concat(tipMore or {}, " ")
  ctx.check(tipText and tipText:find("^Lance") and tipText:find("2%.9 s") and more:find("Tir des arcanes") and more:find("ouverture"),
    "shot tip: " .. tostring(tipText) .. " / " .. more)
  ctx.check(F:UpdateShotTip(), "shot tip for the hunter")

  -- 36. pet advisor
  local adv, fam = HP.PetAdvice("Contreforts de Hautebrande", 26)
  ctx.check(fam == "Chat" and adv:find("Griffe 4") and adv:find("rang 5 au niv 32"), "pet advice: " .. tostring(adv))
  ctx.check(select(2, HP.PetAdvice("Zone inconnue", 26)) == nil, "unknown zone -> generic advice")

  -- 37. ammo budget
  local b = HP.AmmoBudget(10000, 175, 8, 340)
  ctx.check(b and b.kills == 58 and b.need == 464 and b.missing == 124 and b.stacks == 1, "ammo budget maths")
  ctx.check(HP:UpdateAmmo(), "ammo budget computed in game")

  -- 38. trap bar
  ctx.fire("UNIT_SPELLCAST_SUCCEEDED", "player", "cast-2", 1499)
  local tr = HP.Trap()
  ctx.check(tr and tr.name == "Piège givrant", "trap armed")
  ctx.check(HP.TrapText(tr, M.now):find("armé : 60 s"), "trap text: " .. tostring(HP.TrapText(tr, M.now)))
  M.auras.target = { { kind = "debuff", name = "Piège givrant", spellId = 3355, duration = 20,
    expirationTime = M.now + 20, source = "player" } }
  ctx.fire("UNIT_AURA", "target")
  ctx.check(HP.Trap() and HP.Trap().triggered, "trap triggered")
  M.auras.target = {}
  ctx.fire("UNIT_AURA", "target")

  -- 39. feign death: resisted (hit right after), then success
  M.EnterCombat()
  ctx.fire("UNIT_SPELLCAST_SUCCEEDED", "player", "cast-3", 5384)
  M.Advance(0.5)
  ctx.fire("UNIT_COMBAT", "player", "WOUND", "", 120, 1)
  local fd = sug("HunterPro:fd")
  ctx.check(fd and fd.prio == 3 and fd.text:find("RÉSISTÉ"), "FD resisted")
  ctx.fire("UNIT_SPELLCAST_SUCCEEDED", "player", "cast-4", 5384)
  M.Advance(3)
  ctx.check(HP.char.fd.tries == 2 and HP.char.fd.ok == 1, "FD stats " .. HP.char.fd.ok .. "/" .. HP.char.fd.tries)
  ctx.check(HP:FDLine():find("50 %%"), "FD line")

  -- 40. aspect time: whole fight in Cheetah -> tip
  M.auras.player = { { name = "Aspect du guépard", spellId = 5118 } }
  M.Advance(10, 0.5)
  M.LeaveCombat()
  local as = sug("HunterPro:aspect")
  ctx.check(as and as.prio == 2 and as.text:find("guépard"), "aspect report: " .. tostring(as and as.text))
  M.auras.player = {}

  -- 48. changes since last login
  local diff = C.Diff({ time = 1000, level = 25, rested = 0, money = 10000, xpMax = 20000, version = "0.1.0", zone = "A" },
    { time = 91000, level = 26, rested = 0, money = 25000, xpMax = 22000, version = "0.2.0", zone = "A" })
  local joined = table.concat(diff, " | ")
  ctx.check(joined:find("Absent 1 j 1 h") and joined:find("Niveau 25 → 26") and joined:find("Or : %+1 po 50 pa")
    and joined:find("0%.1%.0 → 0%.2%.0"), "diff: " .. joined)
  local rd = table.concat(C.Diff({ level = 26, rested = 100, xpMax = 26000 }, { level = 26, rested = 5300, xpMax = 26000 }), " ")
  ctx.check(rd:find("Repos : %+5 200 XP bonus %(20 %% d'un niveau%)"), "rested gain: " .. rd)
  C.char.snap = { time = 1, level = 25, money = 100, rested = 0, xpMax = 26000 }
  C:ShowChanges()
  local ch = sug("Comfort:changes")
  ctx.check(ch and ch.text:find("Niveau 25 > " .. M.player.level), "changes suggestion: " .. tostring(ch and ch.text))
  ctx.check(C.char.snap.level == M.player.level, "snapshot refreshed")

  -- 49. new spell + free action slot
  C.char.spells = { ["Tir automatique"] = true }
  rawset(_G, "C_SpellBook", nil)
  GetSpellTabInfo = function() return "Général", 0, 0, 2 end
  GetSpellBookItemName = function(i) return ({ "Tir automatique", "Tir des arcanes" })[i] end
  rawset(_G, "IsPassiveSpell", function() return false end)
  HasAction = function(s) return s <= 6 end
  GetBindingKey = function(b) if b == "ACTIONBUTTON7" then return "7" end end
  C:CheckSpells()
  local sp = sug("Comfort:spell")
  ctx.check(sp and sp.text:find("Tir des arcanes") and sp.text:find("case 7 %(touche 7%)"), "new spell: " .. tostring(sp and sp.text))
  ctx.check(C.char.spells["Tir des arcanes"], "spell remembered")
  rawset(_G, "IsPassiveSpell", nil)

  -- 50. perf: per-module timing + report + auto slowdown
  ctx.check((C.instrumented or 0) > 10, "modules instrumented: " .. tostring(C.instrumented))
  ctx.check(C.stats.Fight and C.stats.Fight.calls > 0, "Fight calls timed")
  rawset(_G, "GetAddOnMemoryUsage", function() return 812.4 end)
  rawset(_G, "UpdateAddOnMemoryUsage", function() end)
  M.RunSlash("/tai perf")
  ctx.check(ctx.out("812 Ko"), "/tai perf prints memory")
  rawset(_G, "GetAddOnMemoryUsage", nil)
  rawset(_G, "UpdateAddOnMemoryUsage", nil)
  local st = {}
  C.SlowDecision(st, 20, 0.1); ctx.check(C.SlowDecision(st, 20, 0.1) == true, "slow after 2 bad samples")
  for _ = 1, 4 do C.SlowDecision(st, 60, 0.1) end
  ctx.check(C.SlowDecision(st, 60, 0.1) == false, "back to normal after 5 good samples")
  local base = T.settings.refresh
  GetFramerate = function() return 20 end
  M.Advance(7, 0.5)
  ctx.check(T.perfSlow == true and T.settings.refresh == math.min(12, base * 2), "auto slowdown on low FPS")
  GetFramerate = function() return 60 end
  M.Advance(14, 0.5)
  ctx.check(not T.perfSlow and T.settings.refresh == base, "refresh restored when FPS recover")

  -- never anything in chat from advice
  for _, l in ipairs(M.output) do
    ctx.check(not l:find("RÉSISTÉ") and not l:find("Contrôle rompu"), "advice leaked to chat: " .. l)
  end
end
