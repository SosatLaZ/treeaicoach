-- Scenario 5.1 CLEAN / B10 — « les macros ont l'air guez », « horribles », un vrai tutoriel.
-- Default profile, hunter 40 (and other classes for the generic parts). What it proves:
--   1. « TAI Monture » never contains the Hearthstone (no mount item → the macro is NOT created and says why ; with a
--      mount → « /use [nomounted] item:X » + « /dismount [mounted] » only), even with a Hearthstone in the bags
--   2. no macro body prints to the chat (« /run print ») ; a consumable macro with nothing in the bags is not created
--   3. an old TreeAI macro still installed with the Hearthstone body / the « /run print » body is rewritten to a
--      neutral text by the player's click (also when the macro is now switched off: « TAI Mana » of a hunter)
--   4. « Sert à » is built from the RENDERED body: this file re-parses each body on its own (modifiers, spell and
--      item names) and compares — for every macro of the class, every preset, guard on / off
--   5. a hunter who never installed anything gets the 5 essentials ; one who had macros keeps « Complet » ;
--      « TAI Mana » is off for a hunter ; one GCD action per press by default (no aspect guard)
--   6. the suggested key never holds a modifier that the body tests (Maj+Z + [mod:shift] = the old Monture trap)
--   7. the tutorial is one click away from the page and gives, per macro: Fait / Automatique / Barre / Touche
return function(M, ctx)
  local T = TreeAI
  local Mc, D, W = T.modules and T.modules.Macros, T.Data and T.Data.Macros, T.Window
  local check = ctx.check
  check(Mc and D and Mc.Describe and Mc.TutoLines and Mc.IsLegacyHarmful and Mc.DefaultPreset, "5.1 macro API")
  if not (Mc and D and Mc.Describe) then return end
  local cls = T:PlayerClass()
  local hunter = cls == "HUNTER"
  M.LeaveCombat()
  M.Advance(6, 0.5)
  local out0 = #M.output

  -- every spell of the class known (the bodies are then the richest ones: all modifier clauses present)
  local function teachAll()
    for _, def in ipairs(D.classes[cls] or {}) do
      for _, v in ipairs(def.v or {}) do
        for sid in (v.b or ""):gmatch("{(%d+)}") do
          sid = tonumber(sid)
          if not M.spells[sid] and D.spells[sid] then
            M.spells[sid] = { name = D.spells[sid], rank = "", icon = 132000 + sid, castTime = 0, known = true }
          end
        end
      end
    end
    Mc:ClearSpellCache()
  end
  local function reset(preset, setup)
    M.macros = { global = {}, char = {} }
    M.actions, M.bindings = {}, {}
    M.macroFail = nil
    local c = Mc.char
    c.installed, c.bodies, c.setup, c.opts, c.preset, c.stamps = {}, {}, setup or false, {}, preset, {}
    Mc:ClearBlocked(); Mc:ClearSpellCache(); Mc:ForgetMemo()
  end
  local function def(name) for _, d in ipairs(Mc:Defs()) do if d.n == name then return d end end end
  local function state(name) local d = def(name) return d and Mc:MacroState(d) end
  local function click(fn) M.Hw(function() T:UserAction(fn) end) end
  local function bodyOf(name)
    local i = Mc.MacroIndex(name)
    if not i then return nil end
    local _, _, b = GetMacroInfo(i)
    return b
  end

  -- ---------------------------------------------------------------- 5. defaults
  reset(nil, false)
  if hunter then
    check(Mc:Preset() == "essentiel", "a hunter who never installed anything: « Mes 5 macros essentielles » (" .. tostring(Mc:Preset()) .. ")")
    local on = {}
    for _, d in ipairs(Mc:Defs()) do if Mc:IsOn(d) then on[#on + 1] = d.n end end
    table.sort(on)
    check(table.concat(on, ",") == "TAI Aspects,TAI Familier,TAI Feindre,TAI Pièges,TAI Tir",
      "the 5 essentials on, nothing else: " .. table.concat(on, ","))
    -- the migration: he had macros → « Complet » is pinned once, the new default never switches his set
    local c = Mc.char
    local c2 = { installed = { ["TAI Marque"] = true }, bodies = {}, opts = {}, stamps = {}, setup = true, v5h = true }
    Mc.char = c2
    Mc:ForgetMemo()
    check(Mc:Preset() == "complet" and c2.preset == "complet" and c2.v51 == true,
      "an installed set keeps « Complet » (pinned once): " .. tostring(c2.preset))
    Mc.char = c
    Mc:ForgetMemo()
    reset("complet", false)
    check(not Mc:IsOn(def("TAI Mana")), "« TAI Mana » is off by default for a hunter (Complet)")
    check(not Mc:IsOn(def("TAI Defense")), "« TAI Defense » (click-only variants) is off by default")
  else
    check(Mc:Preset() == "complet", "other classes: « Complet » stays the default (" .. tostring(Mc:Preset()) .. ")")
  end

  -- ---------------------------------------------------------------- 6. suggested key vs modifiers tested
  do
    local KEYMOD = { Maj = "shift", Ctrl = "ctrl", Alt = "alt" }
    local bad = {}
    for c2, defs in pairs(D.classes) do
      for _, d in ipairs(defs) do
        local held = {}
        for m in (d.k or ""):gmatch("(%a+)%+") do if KEYMOD[m] then held[KEYMOD[m]] = true end end
        if next(held) then
          for _, v in ipairs(d.v or {}) do
            local b = (v.b or ""):lower()
            for cond in b:gmatch("%[([^%]]*)%]") do
              for x in (cond .. ","):gmatch("([^,]*),") do
                x = x:gsub("%s", "")
                local m = x:match("^no?mod:(.+)$") or x:match("^mod:(.+)$")
                if x == "mod" or x == "nomod" then bad[#bad + 1] = c2 .. " " .. d.n .. " " .. d.k end
                if m then for k in m:gmatch("[^/]+") do if held[k] then bad[#bad + 1] = c2 .. " " .. d.n .. " " .. d.k end end end
              end
            end
            if held.shift and b:find("isshiftkeydown", 1, true) then bad[#bad + 1] = c2 .. " " .. d.n .. " " .. d.k end
          end
        end
      end
    end
    check(#bad == 0, "no suggested key holds a modifier its macro tests: " .. table.concat(bad, " | "))
  end

  -- ---------------------------------------------------------------- 1-2. Monture / empty consumables
  teachAll()
  reset("complet", false)
  local ALLMODS = { shift = "Maj", ctrl = "Ctrl", alt = "Alt" }
  do
    local mt = state("TAI Monture")
    check(mt and mt.on and mt.code == "empty" and mt.expected == nil, "Monture without a mount item: not created (" .. tostring(mt and mt.code) .. ")")
    check(mt and mt.text:find("pas de monture dans tes sacs", 1, true) and mt.text:find("pas créée", 1, true),
      "…and its line says why: " .. tostring(mt and mt.text))
    local label, fn = nil, nil
    if W and W.pageIndex and W.pageIndex.macros then
      M.Hw(function() Mc:TogglePanel() end)
      W.Render()
      label, fn = W.pageIndex.macros.action()
    end
    if fn then click(fn) else click(function() Mc:UpdateAll() end) end
    check(bodyOf("TAI Monture") == nil, "« Tout mettre à jour » did not create TAI Monture")
    local n = 0
    for _, m in ipairs(M.macros.char) do
      n = n + 1
      check(not tostring(m.body):find("/run print", 1, true), "no chat line in a macro: " .. tostring(m.name))
      check(not tostring(m.body):find("item:6948", 1, true), "no Hearthstone in a macro: " .. tostring(m.name))
    end
    for _, m in ipairs(M.macros.global) do
      check(not tostring(m.body):find("/run print", 1, true) and not tostring(m.body):find("item:6948", 1, true),
        "no chat line / Hearthstone in a macro: " .. tostring(m.name))
    end
    check(n >= 8, "the other macros were created (" .. n .. ")")
    -- with a mount in the bags
    M.AddItem(8595, { name = "Corne du loup brun", classID = 15, subclassID = 5, minLevel = 40 })
    M.SetBagItem(0, 12, 8595, 1)
    M.FireEvent("BAG_UPDATE", 0) M.FireEvent("BAG_UPDATE_DELAYED")
    M.Advance(3, 0.5)
    mt = state("TAI Monture")
    check(mt and mt.expected == "#showtooltip\n/use [nomounted] item:8595\n/dismount [mounted]",
      "Monture with a mount: mount / dismount only: " .. tostring(mt and mt.expected))
    check(mt and Mc.Describe(mt.def, mt.expected) == "Corne du loup brun (si pas monté) + descend de monture (si monté)",
      "…described: " .. tostring(mt and mt.expected and Mc.Describe(mt.def, mt.expected)))
    M.SetBagItem(0, 12, nil)
    M.FireEvent("BAG_UPDATE", 0) M.FireEvent("BAG_UPDATE_DELAYED")
    M.Advance(3, 0.5)
  end

  -- ---------------------------------------------------------------- 3. old harmful macros are neutralised by a click
  do
    reset("complet", true)
    local c = Mc.char
    local old = { ["TAI Monture"] = "#showtooltip\n/use item:6948",
      ["TAI Mana"] = "/run print(\"TreeAI : rien dans les sacs pour cette macro\")" }
    if not def("TAI Mana") then old["TAI Mana"] = nil end
    for name, b in pairs(old) do
      M.Hw(function() CreateMacro(name, "INV_MISC_QUESTIONMARK", b, true) end)
      c.installed[name], c.bodies[name] = true, b
    end
    Mc.DirtyPlacement()
    M.FireEvent("UPDATE_MACROS")
    for name in pairs(old) do
      local st = state(name)
      check(st and st.code == "update" and st.expected == Mc.NEUTRAL_BODY,
        name .. " (old harmful body): « à mettre à jour » with the neutral text (" .. tostring(st and st.code) .. ")")
    end
    click(function() Mc:UpdateAll() end)
    for name in pairs(old) do
      check(bodyOf(name) == Mc.NEUTRAL_BODY, name .. ": rewritten to the neutral text by the click: " .. tostring(bodyOf(name)))
      local st = state(name)
      check(st and st.code ~= "update", name .. ": nothing left to update (" .. tostring(st and st.code) .. ")")
    end
    -- a macro the player edited by hand is never touched
    reset("complet", true)
    M.Hw(function() CreateMacro("TAI Monture", "INV_MISC_QUESTIONMARK", "/use item:6948\n/dance", true) end)
    c = Mc.char
    c.installed["TAI Monture"], c.bodies["TAI Monture"] = true, "#showtooltip\n/use item:6948"
    M.FireEvent("UPDATE_MACROS")
    click(function() Mc:UpdateAll() end)
    check(bodyOf("TAI Monture") == "/use item:6948\n/dance", "a hand-edited macro is left alone")
  end

  -- ---------------------------------------------------------------- 4. « Sert à » = the rendered body (independent parse)
  local CASTS = { ["/cast"] = true, ["/use"] = true, ["/castsequence"] = true, ["/castrandom"] = true, ["/equip"] = true,
    ["/cancelaura"] = true, ["/petautocaston"] = true, ["/petautocastoff"] = true }
  local classNames = {}
  for _, d in ipairs(D.classes[cls] or {}) do
    for _, v in ipairs(d.v or {}) do
      for sid in (v.b or ""):gmatch("{(%d+)}") do
        local n = T.API.SpellName(tonumber(sid)) or D.spells[tonumber(sid)]
        if n then classNames[n] = true end
      end
    end
  end
  local function parse(body)
    local mods, names = {}, {}
    for line in (body .. "\n"):gmatch("(.-)\n") do
      local cmd, rest = line:match("^(/%S+)%s*(.*)$")
      if cmd == "/run" then   -- a script line: the keys it reads
        if rest:find("IsShiftKeyDown", 1, true) then mods.shift = true end
        if rest:find("IsControlKeyDown", 1, true) then mods.ctrl = true end
        if rest:find("IsAltKeyDown", 1, true) then mods.alt = true end
      elseif cmd then
        for cond in rest:gmatch("%[([^%]]*)%]") do
          for x in (cond .. ","):gmatch("([^,]*),") do
            local m = x:gsub("%s", ""):lower():match("^n?o?mod:(.+)$")
            if m then for k in m:gmatch("[^/]+") do mods[k] = true end end
          end
        end
        if CASTS[cmd:lower()] then
          for clause in (rest .. ";"):gmatch("([^;]*);") do
            local a = clause:gsub("%b[]", ""):gsub("reset=%S+", "")
            for one in (a .. ","):gmatch("([^,]*),") do
              one = one:gsub("^%s+", ""):gsub("%s+$", ""):gsub("^!", "")
              local id = one:match("^item:(%d+)$")
              if id then names[#names + 1] = (T.API.ItemInfo(tonumber(id)))
              elseif one ~= "" and one:lower() ~= "null" and not one:match("^%d+$") then names[#names + 1] = one end
            end
          end
        end
      end
    end
    return mods, names
  end
  local function word(text, w) return text:find("%f[%a]" .. w .. "%f[%A]") ~= nil end
  local checked, problems = 0, {}
  local function verify(tag)
    local list = Mc:States()
    for _, st in ipairs(list) do
      local body = st.expected
      if body and body ~= Mc.NEUTRAL_BODY then
        local desc = Mc.Describe(st.def, body)
        checked = checked + 1
        if type(desc) ~= "string" or desc == "" then problems[#problems + 1] = tag .. " " .. st.name .. ": no description"
        else
          local mods, names = parse(body)
          for k, fr in pairs(ALLMODS) do
            if (mods[k] and true or false) ~= word(desc, fr) then
              problems[#problems + 1] = ("%s %s: %s %s in the body but %s in « %s »"):format(tag, st.name, fr,
                mods[k] and "tested" or "absent", word(desc, fr) and "named" or "not named", desc)
            end
          end
          for _, n in ipairs(names) do
            if not desc:find(n, 1, true) then problems[#problems + 1] = ("%s %s: « %s » cast but not in « %s »"):format(tag, st.name, n, desc) end
          end
          for n in pairs(classNames) do
            if desc:find(n, 1, true) and not body:find(n, 1, true) then
              problems[#problems + 1] = ("%s %s: « %s » promised but absent from the body"):format(tag, st.name, n)
            end
          end
          if desc:find("nil", 1, true) or desc:find("{", 1, true) then problems[#problems + 1] = tag .. " " .. st.name .. ": raw token in « " .. desc .. " »" end
          -- the row and the tutorial show that same sentence
          if not Mc.Purpose(st.def, body):find(desc, 1, true) then problems[#problems + 1] = tag .. " " .. st.name .. ": Purpose ≠ Describe" end
        end
      end
    end
  end
  for _, preset in ipairs(Mc.PRESET_ORDER) do
    if Mc.PresetOK(preset) then
      reset(preset, false)
      verify(preset)
      if hunter then
        for _, d in ipairs(Mc:Defs()) do if (d.f or ""):find("g", 1, true) then Mc:SetOpt(d, "guard", true) end end
        verify(preset .. "+garde")
      end
    end
  end
  -- …and with the few spells of a fresh character (optional clauses dropped: the text must follow)
  if hunter then
    for sid, sp in pairs(M.spells) do
      if type(sp) == "table" and sid ~= 75 and sid ~= 3044 and sid ~= 1978 and sid ~= 2973 and sid ~= 883 and sid ~= 136 and sid ~= 982 then sp.known = false end
    end
    Mc:ClearSpellCache()
    reset("complet", false)
    verify("niveau bas")
    local ct = state("TAI Controle")
    check(ct and ct.expected == nil, "low level: TAI Controle (Trait de choc not learned) is not offered")
    teachAll()
    for _, sp in pairs(M.spells) do if type(sp) == "table" then sp.known = true end end
    Mc:ClearSpellCache()
  end
  check(checked >= 20, "descriptions verified against their body: " .. checked)
  check(#problems == 0, #problems .. " description(s) that do not match the macro text:\n    " .. table.concat(problems, "\n    ", 1, math.min(#problems, 12)))

  if hunter then
    -- the audit's example: Scatter Shot / Intimidation not learned → the line no longer promises Maj / Alt
    reset("complet", false)
    M.spells[19503] = nil M.spells[19577] = nil M.spells[1513] = nil   -- dispersion, intimidation, effrayer une bête
    for sid, sp in pairs(M.spells) do if type(sp) == "table" and (sp.name == "Tir de dispersion" or sp.name == "Intimidation" or sp.name == "Effrayer une bête") then M.spells[sid] = nil end end
    Mc:ClearSpellCache()
    local st = state("TAI Controle")
    check(st and st.expected == "#showtooltip\n/cast [@mouseover,harm,nodead][] Trait de choc", "Controle without its talents: " .. tostring(st and st.expected))
    local p = st and Mc.Purpose(st.def, st.expected) or ""
    check(p:find("Sert à : Trait de choc [survol, sinon cible]", 1, true) and not p:find("Maj", 1, true) and not p:find("Alt", 1, true),
      "…« Sert à » promises no key that does nothing: " .. p)
    teachAll()
    -- one GCD action per press by default: no aspect guard unless the player asks for it
    reset("complet", false)
    local tir = state("TAI Tir")
    check(tir and tir.expected and not tir.expected:find("Aspect du faucon", 1, true), "TAI Tir: no aspect line by default: " .. tostring(tir and tir.expected))
    Mc:SetOpt(def("TAI Tir"), "guard", true)
    tir = state("TAI Tir")
    check(tir and tir.expected and tir.expected:find("/cast !Aspect du faucon", 1, true), "TAI Tir: the guard comes back when asked")
    Mc:SetOpt(def("TAI Tir"), "guard", nil)
    -- the pet key keeps Maj (revive) in the 5 essentials
    reset("essentiel", false)
    local fam = state("TAI Familier")
    check(fam and fam.expected and fam.expected:find("[mod:shift][@pet,dead] Ressusciter le familier", 1, true),
      "essentials: TAI Familier keeps Maj = Ressusciter: " .. tostring(fam and fam.expected))
  end

  -- ---------------------------------------------------------------- 7. the tutorial, one click from the page
  check(not (W and W._active) or (W.pageIndex and W.pageIndex.macros and W.Render) ~= nil, "the Macros page exists when the window is on")
  if W and W._active and W.pageIndex and W.pageIndex.macros then
    reset(nil, false)
    if W.char and W.char.collapsed then for _, k in ipairs({ "macros:list", "macros:off", "macros:set", "macros:more" }) do W.char.collapsed[k] = nil end end
    Mc._pageSel, Mc._tuto = nil, nil
    if not (W.IsOpen() and W.CurrentPage() == "macros") then M.Hw(function() Mc:OpenPage() end) end
    W.Render()
    local btn
    for _, r in ipairs(W.lastLay.rects) do
      if not btn and r.label and tostring(r.label):find("^Tutoriel") then btn = r end
    end
    check(btn ~= nil, "a « Tutoriel » button on the Macros page")
    check(Mc:ToggleTuto(true) == true, "tutorial opened")
    W.Render()
    local rows, texts = {}, {}
    for _, r in ipairs(W.lastLay.rects) do
      if r.kind == "row" then rows[#rows + 1] = tostring(r.label) end
      texts[#texts + 1] = tostring(r.label or "")
    end
    local all = table.concat(texts, "\n")
    check(all:find("En 3 gestes", 1, true) and all:find("Ce qui est automatique", 1, true) and all:find("Retour à mes macros", 1, true),
      "tutorial: the 3 steps, what is automatic, a way back")
    local nOn = 0
    for _, d in ipairs(Mc:Defs()) do if Mc:IsOn(d) then nOn = nOn + 1 end end
    check(#rows == nOn and nOn >= 5, ("tutorial: one row per active macro (%d rows, %d active)"):format(#rows, nOn))
    for _, r in ipairs(rows) do
      check(r:find("Fait : ", 1, true) and r:find("Automatique : ", 1, true) and r:find("Barre : ", 1, true) and r:find("Touche", 1, true),
        "tutorial row: Fait / Automatique / Barre / Touche — " .. r:gsub("\n", " ⏎ "):sub(1, 140))
    end
    Mc:ToggleTuto(false)
    W.Render()
    local back
    for _, r in ipairs(W.lastLay.rects) do if r.label and tostring(r.label):find("^Tutoriel") then back = true end end
    check(back, "« Retour » shows the list again")
  end
  for i = out0 + 1, #M.output do check(false, "nothing in the chat: " .. tostring(M.output[i])) end
end
