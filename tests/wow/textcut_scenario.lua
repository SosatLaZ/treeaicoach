-- (4.0) UTF-8 everywhere: the whole addon with quest titles, objectives, NPC and zone names full of accents at every
-- byte position (the realistic world of qaui_lib: level-28 Horde hunter, Mille pointes). Every text the player can
-- see — each SetText / SetFormattedText of every frame, tooltip lines, suggestions, every tab line, the card, every
-- window page, the old panel — must stay valid UTF-8 (the byte cuts of 17 modules showed « …de la for\195… »).
-- Run by tests/wow/test_textcut.py (forever + modern):
-- lua5.1 tests/wow/harness.lua -q --flavor forever --class HUNTER --level 28 --allow LibQuestieDB --allow QuestDB
--   --allow NpcDB --allow ObjectDB --allow ItemDB --allow TreeAIPlan --allow IsQuestFlaggedCompleted
--   --scenario tests/wow/textcut_scenario.lua wow_addon/TreeAI
return function(M, ctx)
  local T = TreeAI
  local check = ctx.check
  local Lb = dofile("tests/wow/qaui_lib.lua")

  local function valid(s)
    local i, n = 1, #s
    while i <= n do
      local c = s:byte(i)
      local w = (c < 0x80 and 1) or (c >= 0xC2 and c <= 0xDF and 2) or (c >= 0xE0 and c <= 0xEF and 3)
        or (c >= 0xF0 and c <= 0xF4 and 4) or nil
      if not w or i + w - 1 > n then return false end
      for j = i + 1, i + w - 1 do
        local d = s:byte(j)
        if d < 0x80 or d > 0xBF then return false end
      end
      i = i + w
    end
    return true
  end
  local function show(s) return (tostring(s):gsub("[\128-\255]", function(ch) return ("\\%d"):format(ch:byte()) end)) end

  -- every text set on any frame, from now on (the mock's shared widget methods)
  local bad, seen, nSet = {}, {}, 0
  local function note(s, where)
    if type(s) ~= "string" or valid(s) or seen[s] then return end
    seen[s] = true
    bad[#bad + 1] = show(s) .. "  <" .. tostring(where) .. ">"
  end
  local Wg = Lb.widget(M)
  check(Wg and Wg.SetText, "mock widget methods found")
  local set0, fmt0 = Wg.SetText, Wg.SetFormattedText
  local function caller()
    local tb = debug.traceback("", 3)
    return tb:match("(Modules[/\\][%w_]+%.lua:%d+)") or tb:match("([%w_]+%.lua:%d+)") or "?"
  end
  Wg.SetText = function(self, t, ...)
    nSet = nSet + 1
    if type(t) == "string" and not valid(t) then note(t, caller()) end
    return set0(self, t, ...)
  end
  Wg.SetFormattedText = function(self, f, ...)
    local okF, s = pcall(string.format, f, ...)
    if okF and type(s) == "string" and not valid(s) then note(s, caller()) end
    return fmt0(self, f, ...)
  end

  -- the world, then accents everywhere: log titles, objectives, Questie names (quests + NPCs), zone names
  local LOG, lib = Lb.world(M, T)
  local LONG = {
    "Les loups-garous de la forêt d'Ombre-Lune", "Élixir élémentaire de l'été éternel à Hautebrande",
    "Œufs à éclore près des Salines miroitantes", "àéèêëîïôöùûüç àéèêëîïôöùûüç àéèêëîïôöùûüç",
    "Le cœur d'Âprefange : une tâche très, très longue", "Écailles d'émeraude érodées — épreuve d'été",
    "Défi des géants égarés près de l'Éperon", "Ça s'ébruite à Mille pointes : « Préparez-vous » !",
  }
  local k = 0
  for _, q in ipairs(LOG) do
    if q.id then
      k = k + 1
      local base = LONG[(k - 1) % #LONG + 1]
      q.title = (k > #LONG) and ("é" .. base) or base           -- a second pass shifts every accent by one byte
      for _, o in ipairs(q.objectives or {}) do
        o.text = o.text:gsub("^([^:]+)", "%1 élevé près de l'Éperon d'Âprefange")
      end
    end
  end
  local function rows(entity) local _, r = debug.getupvalue(entity.Get, 1) return type(r) == "table" and r or {} end
  if lib then
    for id, r in pairs(rows(lib.Quest)) do
      for _, q in ipairs(LOG) do if q.id == id then r.name = q.title end end
    end
    for _, r in pairs(rows(lib.Npc)) do r.name = "Éléonore " .. r.name .. " d'Âprefange-Été" end
    for _, r in pairs(rows(lib.Item)) do r.name = "Écaille " .. r.name .. " érodée" end
  end
  M.mapNames[1441] = "Mille pointes d'Âprefange-l'Éternelle"

  Lb.goTo(1441, 46, 50)
  M.player.zone, M.player.subzone = M.mapNames[1441], "Poste de Librevent-Été"
  M.FireEvent("ZONE_CHANGED_NEW_AREA")
  M.FireEvent("QUEST_LOG_UPDATE")
  local X, P = T.Export, T.Planner
  if X and X.Rebuild then X:Rebuild() end
  M.Advance(8, 0.1)
  if P and P.Refresh then P:Refresh(true) end
  M.Advance(5, 0.05)
  M.Advance(3, 0.5)

  -- the card and every window page
  local W = T.Window
  check(W and W.ShowCard and W.Open, "window module loaded")
  if W and W.ShowCard then
    W.ShowCard()
    W.RenderCard(true)
    W.Open("now")
    for _, pg in ipairs(W.PAGES or {}) do if not pg.hidden then W.Select(pg.id) M.Advance(0.3, 0.1) end end
    W.Close()
  end
  -- the old panel, every tab
  if T.ShowPanel and T.SelectTab then
    T:ShowPanel()
    for id in pairs(T.tabs) do T:SelectTab(id) end
    if T.HidePanel then T:HidePanel() end
  end
  M.Advance(4, 0.2)

  -- what the modules hold: suggestions (text + tooltip), every tab line (text, right, tooltip)
  local nLines = 0
  local function walk(v, where)
    if type(v) == "string" then nLines = nLines + 1 note(v, where)
    elseif type(v) == "table" then for _, x in ipairs(v) do walk(x, where) end end
  end
  for id, e in pairs(T._suggestions or {}) do
    if type(e) == "table" then walk(e.text, "suggestion " .. tostring(id)) walk(e.tooltip, "suggestion " .. tostring(id)) end
  end
  for id in pairs(T.tabs) do
    for _, l in ipairs(T:GetTabLines(id) or {}) do
      if type(l) == "table" then
        walk(l.text, "tab " .. id) walk(l.label, "tab " .. id) walk(l.right, "tab " .. id) walk(l.tooltip, "tab " .. id)
      end
    end
  end
  -- tooltip lines and chat
  for _, f in ipairs(M.frames) do
    for _, l in ipairs(rawget(f, "__lines") or {}) do note(l, "tooltip") end
  end
  for _, l in ipairs(M.output) do note(l, "chat") end

  -- the long accented titles really reached the screen, cut (else this test proves nothing)
  local cutSeen = 0
  for _, f in ipairs(M.frames) do
    local function scan(r, depth)
      if type(r) ~= "table" or depth > 12 then return end
      local t = rawget(r, "__text")
      if type(t) == "string" and t:find("…", 1, true) and (t:find("[\195\197][\128-\191]") ) then cutSeen = cutSeen + 1 end
      for _, c in ipairs(rawget(r, "__children") or {}) do scan(c, depth + 1) end
    end
    scan(f, 0)
  end
  Wg.SetText, Wg.SetFormattedText = set0, fmt0

  check(nSet > 200 and nLines > 50, ("enough text rendered (%d SetText, %d lines)"):format(nSet, nLines))
  check(cutSeen > 0, "some accented texts were cut with « … » on screen")
  check(#bad == 0, ("%d invalid UTF-8 text(s): %s"):format(#bad, table.concat(bad, " || ", 1, math.min(#bad, 12))))
  if os.getenv("TEXTCUT_VERBOSE") then
    io.stderr:write(("textcut: %d SetText, %d lines, %d cut accented texts, %d invalid\n"):format(nSet, nLines, cutSeen, #bad))
    for _, b in ipairs(bad) do io.stderr:write("  BAD ", b, "\n") end
  end
end
