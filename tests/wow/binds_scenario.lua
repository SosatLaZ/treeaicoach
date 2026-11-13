-- Scenario for Modules/Binds.lua (agent BINDS): mock action bars + bindings, plan, conflicts, actions on click,
-- binding blocked by the game (ADDON_ACTION_FORBIDDEN) -> instructions. Fake names only.
return function(M, ctx)
  local GetMacroIndexByName = rawget(_G, "GetMacroIndexByName") or M.MacroIndex   -- absent on Forever (FIDELITY)
  local T = TreeAI
  local B, Mac = T.modules.Binds, T.modules.Macros
  ctx.check(B and B.db, "Binds registered")
  if not (B and Mac) then return end
  M.LeaveCombat()
  M.bindings = {}
  -- (MACRO3) the storm clicked every button (presets, per-macro settings of the « Macros » page): default set again
  if Mac.char then Mac.char.opts, Mac.char.preset = {}, "complet" end
  T:UserAction(Mac.Install, Mac, false)
  ctx.check(GetMacroIndexByName("TAI Tir") > 0 and GetMacroIndexByName("TAI Marque") > 0, "TAI macros created")

  -- bars: 1 = TAI Tir (key 1), 2 = empty (key 2), 3+4 = Serpent Sting twice (keys 3, 4), 61 = Disengage (no key)
  M.actions = {
    [1] = { type = "macro", id = GetMacroIndexByName("TAI Tir"), text = "TAI Tir" },
    [3] = { type = "spell", id = 1978 }, [4] = { type = "spell", id = 1978 },
    [61] = { type = "spell", id = 781 },
  }
  for i = 1, 4 do M.bindings["ACTIONBUTTON" .. i] = tostring(i) end
  M.bindings.MOVEFORWARD, M.bindings.STRAFELEFT = "Z", "Q"   -- AZERTY movement
  M.bindings.SITORSTAND = "X"
  M.bindings.ACTIONPAGE1 = "SHIFT-1"
  -- Bartender4 present: its button 62 has a key (read only)
  rawset(_G, "Bartender4", {})
  rawset(_G, "BT4Button62", { action = 62 })
  M.bindings["CLICK BT4Button62:LeftButton"] = "SHIFT-2"

  B:Refresh()
  local st, res = B.state, B.result
  ctx.check(st.slots[1].key == "1" and st.slots[61].key == nil, "slot keys read")
  ctx.check(st.slots[62].key == "SHIFT-2" and st.slots[62].src == "Bartender4", "Bartender4 key read")
  local freeSet = {}
  for _, k in ipairs(st.free) do freeSet[k] = true end
  ctx.check(not freeSet.Q and not freeSet.Z and not freeSet.X and not freeSet["SHIFT-2"] and freeSet.E and freeSet.A
    and st.free[1] == "5", "free keys skip movement / bound keys: " .. table.concat(st.free, " "))
  local useless = {}
  for _, u in ipairs(st.useless) do useless[u.key] = u.label end
  ctx.check(useless.X == "s'asseoir" and useless["SHIFT-1"] == "page de barre 1", "useless keys")
  local kinds = {}
  for _, c in ipairs(res.conflicts) do kinds[c.kind .. ":" .. (c.name or c.key or "")] = c.text end
  ctx.check(kinds["dup:Morsure de serpent"], "duplicate spell on 2 keys")
  -- (the redone macros now cover Désengagement with a TAI macro: then it is not reported at all)
  local disCovered = not kinds["nokey:Désengagement"] and not kinds["missing:Désengagement"]
  ctx.check(disCovered or (kinds["nokey:Désengagement"] and kinds["nokey:Désengagement"]:find("barre bas gauche case 1")),
    "spell on a bar without key")
  ctx.check(not kinds["missing:Feindre la mort"], "spell covered by a TAI macro is not reported")
  local p1 = res.plan[1]
  ctx.check(p1 and B.StepText(p1) == "Mets « TAI Marque » sur la touche 2 (barre 1 case 2, libre)",
    "plan 1: " .. tostring(p1 and B.StepText(p1)))
  local lines = T:GetTabLines("binds")
  local found = false
  for _, l in ipairs(lines) do if l.text:find("Mets « TAI Marque »", 1, true) and l.onClick then found = true end end
  ctx.check(found, "plan shown in « Touches » with a click action")

  -- in combat: nothing happens
  M.EnterCombat()
  T:UserAction(B.DoStep, B, p1)
  ctx.check(M.actions[2] == nil, "no placement in combat")
  M.LeaveCombat()

  -- click: place the macro (one per click)
  T:UserAction(B.DoStep, B, p1)
  ctx.check(M.actions[2] and M.actions[2].type == "macro" and M.actions[2].text == "TAI Marque" and M.cursor == nil,
    "macro placed in slot 2")
  B:Refresh()
  local p2 = B.result.plan[1]
  ctx.check(p2 and p2.how == "bind", "next step = bind: " .. tostring(p2 and B.StepText(p2)))
  -- bind: first click = confirmation line, second click = SetBinding + SaveBindings
  T:UserAction(B.DoStep, B, p2)
  local cmd = (p2.item.kind == "macro" and "MACRO " or "SPELL ") .. p2.item.name
  ctx.check(M.bindings[cmd] == nil and B.confirm, "confirmation asked first")
  local conf = false
  for _, l in ipairs(T:GetTabLines("binds")) do if l.text:find("^Confirmer") then conf = true end end
  ctx.check(conf, "confirmation line shown")
  T:UserAction(B.DoStep, B, p2)
  ctx.check(M.bindings[cmd] == p2.key and M.savedBindings ~= nil, "bound " .. cmd .. " to " .. tostring(p2.key))

  -- WoW Forever blocks bindings: degrade to instructions
  M.forbidBindings = true
  B:Refresh()
  local p3 = B.result.plan[1]
  ctx.check(p3 and p3.how == "bind", "p3 bind")
  T:UserAction(B.DoStep, B, p3)
  T:UserAction(B.DoStep, B, p3)
  ctx.check(B.db.bindBlocked == true, "binding blocked remembered")
  local manual = false
  for _, l in ipairs(T:GetTabLines("binds")) do
    if type(l.tooltip) == "string" and l.tooltip:find("^À la main") then manual = true end
  end
  ctx.check(manual, "manual instructions in tooltips")
  ctx.check(T:Summary().binds and T:Summary().binds.bindBlocked, "summary.binds")
  M.forbidBindings = false

  -- spell removed from every bar -> « absent des barres »
  M.actions[61] = nil
  B:Refresh()
  local miss = false
  for _, c in ipairs(B.result.conflicts) do if c.kind == "missing" and c.name == "Désengagement" then miss = true end end
  ctx.check(miss or disCovered, "important spell missing from bars")
  -- slash
  T:HandleSlash("touches")
  ctx.check(B.state ~= nil, "/tai touches")
  rawset(_G, "Bartender4", nil)
  rawset(_G, "BT4Button62", nil)
end
