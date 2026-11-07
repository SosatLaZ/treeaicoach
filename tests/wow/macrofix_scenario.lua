-- Scenario MACROFIX: every way macro installation can fail in the real client, and the fallbacks.
--   • GetMacroIndexByName missing (not referenced anywhere in Forever's UI) → scan GetMacroInfo, no duplicates
--   • lower rank not « known » while a higher rank is → spell found by NAME, body uses the name without rank
--   • spell really unknown → that macro is never emitted
--   • slots full → clear message + copy-paste window, diagnostic « emplacement plein »
--   • CreateMacro forbidden (ADDON_ACTION_FORBIDDEN, returns nothing) or silently ignored (nil return)
--     → ONE attempt only, copy-paste window with each macro's text, diagnostic « bloqué par le jeu »,
--     « Réessayer » creates them once the game allows it
-- Run on every flavour, Hunter + Warrior. Fake names only.
return function(M, ctx)
  local T = TreeAI
  local Mc = T.modules and T.modules.Macros
  ctx.check(Mc and Mc.Diagnose and Mc.ShowCopy and Mc.MacroIndex, "Macros module with diagnostic + copy-paste")
  if not Mc then return end
  M.LeaveCombat()
  local hunter = T:PlayerClass() == "HUNTER"

  local function reset()
    M.macros = { global = {}, char = {} }
    M.macroFail, M.macroBlockedCalls = nil, 0
    local c = Mc.char or {}
    c.installed, c.bodies, c.setup = {}, {}, false
    Mc:ClearBlocked()
    Mc:ClearSpellCache()
    if Mc.copy then Mc.copy:Hide() end
  end
  local function count()
    return #M.macros.global + #M.macros.char
  end
  local function names()
    local seen, dup = {}, false
    for _, l in ipairs({ M.macros.global, M.macros.char }) do
      for _, m in ipairs(l) do if seen[m.name] then dup = true end seen[m.name] = true end
    end
    return seen, dup
  end
  local function click(fn, ...)   -- a player click = one hardware event
    local hw = M.hardware
    M.hardware = true
    T:UserAction(fn, ...)
    M.hardware = hw
  end
  local function macroLine()
    local n, e = 0, nil
    for id, s in pairs(T._suggestions or {}) do
      if tostring(id):find("Macros", 1, true) then n = n + 1; e = s end
    end
    return e, n
  end
  local function diagItem(status)
    local d = TreeAIDB.diag and TreeAIDB.diag.macros
    for _, it in ipairs((d and d.items) or {}) do if it.status == status then return it end end
  end

  -- 1. normal install by a typed command: created, diag written, success line, no duplicates
  reset()
  M.RunSlash("/tai macros installer")
  local made = count()
  ctx.check(made > 0, "macros created by the command (" .. made .. ")")
  local _, dup = names()
  ctx.check(not dup, "no duplicate macro names")
  local d = TreeAIDB.diag and TreeAIDB.diag.macros
  ctx.check(type(d) == "table" and d.ok == made and d.mode == "auto" and type(d.slots) == "table",
    "TreeAIDB.diag.macros written after install")
  local line = macroLine()
  ctx.check(line and tostring(line.text):find("prête", 1, true) and line.onClick, "clear success line (clickable)")
  M.RunSlash("/tai macros installer")
  ctx.check(count() == made, "second install: nothing duplicated")

  -- 2. GetMacroIndexByName missing → scan by GetMacroInfo, still no duplicates
  local byName = rawget(_G, "GetMacroIndexByName")
  reset()
  rawset(_G, "GetMacroIndexByName", nil)
  M.RunSlash("/tai macros installer")
  local made2 = count()
  ctx.check(made2 == made, "install without GetMacroIndexByName (" .. made2 .. "/" .. made .. ")")
  M.RunSlash("/tai macros installer")
  local _, dup2 = names()
  ctx.check(count() == made2 and not dup2, "without GetMacroIndexByName: no duplicates on re-install")
  local first = M.macros.char[1] or M.macros.global[1]
  ctx.check(first and Mc.MacroIndex(first.name), "index found by scanning GetMacroInfo")
  rawset(_G, "GetMacroIndexByName", byName)

  -- 3. ranks: rank-1 ID not known while a higher rank is → known by name; really unknown → never emitted
  if hunter and M.spells[1978] then
    local r1 = M.spells[1978]
    r1.known = false
    M.spells[13549] = { name = r1.name, rank = "", icon = 1, castTime = 0, known = true }
    Mc:ClearSpellCache()
    local n1, k1 = Mc.ResolveSpell(1978)
    ctx.check(k1 and n1 == r1.name, "lower rank unknown but higher rank known → spell known by name")
    M.spells[13549] = nil
    Mc:ClearSpellCache()
    local _, k2 = Mc.ResolveSpell(1978)
    ctx.check(not k2, "spell not in the spellbook → unknown")
    local morsure
    for _, def in ipairs(Mc:Defs()) do if def.n == "TAI Morsure" then morsure = def end end
    ctx.check(morsure and Mc:Render(morsure) == nil, "macro with an unknown spell is never emitted")
    r1.known = true
    Mc:ClearSpellCache()
  end

  -- 4. body validity
  ctx.check(Mc.ValidBody("#showtooltip\n/cast [@mouseover,harm,nodead][] Marque du chasseur"), "conditionals accepted")
  ctx.check(not Mc.ValidBody("/cast [@mouseover,harm,nodead][] "), "clause without spell refused")
  ctx.check(not Mc.ValidBody("/cast " .. string.rep("x", 260)), "> 255 bytes refused")
  ctx.check(not Mc.ValidBody("#showtooltip\n/cast {75}"), "unresolved token refused")

  -- 5. slots full → message + copy-paste + diagnostic « emplacement plein »
  reset()
  local maxG, maxC = Mc.Limits()
  for i = 1, maxG do M.macros.global[i] = { name = "Perso " .. i, icon = 1, body = "/dance" } end
  for i = 1, maxC do M.macros.char[i] = { name = "Moi " .. i, icon = 1, body = "/wave" } end
  local out0 = #M.output
  M.RunSlash("/tai macros installer")
  ctx.check(count() == maxG + maxC, "slots full: nothing created")
  local said = false
  for i = out0 + 1, #M.output do if tostring(M.output[i]):find("plus de place", 1, true) then said = true end end
  ctx.check(said, "slots full: clear message (perso x/y, compte x/y)")
  local fl = macroLine()
  ctx.check(fl and tostring(fl.text):find("Plus de place", 1, true), "slots full: one clickable line")
  if fl and fl.onClick then click(fl.onClick, fl) end
  ctx.check(Mc.copy and Mc.copy:IsShown() and #(Mc._copyList or {}) > 0, "slots full: copy-paste window on click")
  M.RunSlash("/tai macros diag")
  ctx.check(diagItem("full"), "diagnostic: « emplacement plein »")

  -- 6. CreateMacro forbidden (ADDON_ACTION_FORBIDDEN, nothing created) → ONE attempt, copy-paste mode
  for _, mode in ipairs({ "forbidden", "nilret" }) do
    reset()
    M.macroFail = mode
    M.RunSlash("/tai macros installer")
    ctx.check(count() == 0, mode .. ": nothing created")
    ctx.check(M.macroBlockedCalls == 1, mode .. ": a single attempt (" .. tostring(M.macroBlockedCalls) .. ")")
    ctx.check(Mc:IsBlocked(), mode .. ": copy-paste mode on")
    local f = Mc.copy
    local p = Mc._copyList and Mc._copyList[1]
    ctx.check(f and f:IsShown() and p and f.body:GetText() == p.body and f.name:GetText() == p.def.n,
      mode .. ": window shows name + text to copy")
    if f then
      f.body:SetText("abîmé")
      local onChange = f.body.__scripts and f.body.__scripts.OnTextChanged
      if onChange then onChange(f.body, true) end
      ctx.check(f.body:GetText() == (p and p.body), mode .. ": text box is read-only (Ctrl+C only)")
      f.nextB:Click()
      ctx.check(#Mc._copyList < 2 or Mc._copyIdx == 2, mode .. ": « Suivante »")
      f.open:Click()   -- ShowMacroFrame on click only (absent in the mock → « tape /macro »)
    end
    if mode == "forbidden" then
      local b = TreeAIDB.diag and TreeAIDB.diag.blocked
      ctx.check(b and tostring(b[#b].func):find("CreateMacro", 1, true), "forbidden: logged by Core")
    end
    M.RunSlash("/tai macros installer")
    ctx.check(M.macroBlockedCalls == 1 and Mc.copy:IsShown(), mode .. ": no new attempt, straight to copy-paste")
    M.RunSlash("/tai macros diag")
    ctx.check(diagItem("blocked") and TreeAIDB.diag.macros.mode == "copier-coller", mode .. ": diagnostic « bloqué par le jeu »")
    local _, n = macroLine()
    ctx.check(n <= 1, mode .. ": one macro line at most")
    -- the game allows it again → « Réessayer » creates them
    M.macroFail = nil
    if f then f.retry:Click() end
    ctx.check(count() > 0 and not Mc:IsBlocked(), mode .. ": « Réessayer » creates the macros")
  end

  -- 7. panel: one macro → created, put on the cursor (« glisse-la sur ta barre »)
  reset()
  Mc:TogglePanel()
  local P = Mc.panel
  if P then
    local sel = Mc:SelectedDef()
    if sel and Mc:Render(sel) then
      P.one:Click()
      ctx.check(Mc.MacroIndex(sel.n) and M.cursor and M.cursor.type == "macro", "one macro: created + on the cursor")
    end
    P.diag:Click()
    ctx.check(TreeAIDB.diag.macros and #TreeAIDB.diag.macros.items > 0, "« Diagnostic macros » button")
    P:Hide()
  end
  if Mc.copy then Mc.copy:Hide() end
  reset()
end
