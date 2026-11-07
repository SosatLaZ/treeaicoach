-- Harness scenario (CONTENTFIX, robustesse des textes) : chaque ligne de chaque module (conseils + tous les
-- onglets), rendue avec les données factices dans les états mort / fantôme / combat / vol / instance / solo /
-- groupe / niveau 1 / niveau 60, ne contient jamais « ? », « nil », « NaN », « 0/0 », « -1 », double espace,
-- anglais non traduit, code de lien brut ; tient en ≤ 2 lignes courtes (TreeAI.LINE_MAX) ; ne dit jamais de lier
-- la course automatique (touche « ² » de l'utilisateur). Noms factices.
-- lua5.1 tests/wow/harness.lua --scenario tests/wow/robustness_scenario.lua wow_addon/TreeAI
return function(M, ctx)
  local T = TreeAI
  local check = ctx.check
  local BAD = {
    { "?", "« ? »", true }, { "%f[%a]nil%f[^%w\128-\255]", "nil" }, { "%f[%a][Nn]a[Nn]%f[^%w\128-\255]", "NaN" },
    { "%f[%a]inf%f[^%w\128-\255]", "inf" }, { "%f[%d]0/0%f[%D]", "0/0" }, { "%f[%w%-]%-1%f[%D]", "-1" },
    { "  ", "double espace", true }, { "|H", "lien brut", true }, { "item:%d", "lien brut" },
    { "table: ", "table", true }, { "function: ", "fonction", true },
    { "%f[%a]true%f[^%w\128-\255]", "true" }, { "%f[%a]false%f[^%w\128-\255]", "false" }, { "%f[%a]Unknown%f[^%w\128-\255]", "anglais" },
    { "UNKNOWN", "anglais", true }, { "%f[%a]the%f[^%w\128-\255]", "anglais" }, { "%f[%a]You%f[^%w\128-\255]", "anglais" },
    { "[Aa]utorun", "course auto" }, { "course automatique", "course auto", true }, { "TOGGLEAUTORUN", "course auto", true },
    { "²", "touche ²", true },
  }
  local seen, nBad, nLines = {}, 0, 0
  local function audit(state, where, text)
    local plain = T.PlainText(tostring(text or ""))
    nLines = nLines + 1
    local function bad(why)
      local k = why .. "|" .. plain
      if seen[k] then return end
      seen[k] = true
      nBad = nBad + 1
      if nBad <= 60 then check(false, ("[%s] %s — %s : %s"):format(state, where, why, plain)) end
    end
    for _, b in ipairs(BAD) do
      -- une vraie question (« Prêt ? », « Tu veux … ? ») n'est pas une valeur inconnue
      local p = (b[1] == "?") and (plain:gsub("Prêt %?", "")) or plain
      if p:find(b[1], 1, b[3] or false) then bad(b[2]) end
    end
    if T.TextLen and T.TextLen(plain) > (T.LINE_MAX or 96) then bad("trop long (" .. T.TextLen(plain) .. ")") end
    if plain:find("^%s") or plain:find("%s$") then bad("espace au bord") end
  end
  local function sweep(state)
    for id, e in pairs(T._suggestions) do audit(state, "conseil " .. id, e.text) end
    for _, tab in ipairs(T:GetTabs()) do
      for _, l in ipairs(T:GetTabLines(tab.id)) do audit(state, "onglet " .. tab.id, l.text) end
    end
  end
  local function settle(sec)
    for _, ev in ipairs({ "PLAYER_ENTERING_WORLD", "BAG_UPDATE_DELAYED", "QUEST_LOG_UPDATE", "UNIT_INVENTORY_CHANGED",
        "PLAYER_EQUIPMENT_CHANGED", "UPDATE_INVENTORY_DURABILITY", "PLAYER_XP_UPDATE", "SKILL_LINES_CHANGED",
        "GROUP_ROSTER_UPDATE", "UNIT_PET", "PLAYER_TARGET_CHANGED", "ZONE_CHANGED_NEW_AREA" }) do
      pcall(ctx.fire, ev, ev == "UNIT_PET" and "player" or nil)
    end
    M.Advance(sec or 12, 1)
  end
  local function reset()
    M.player.dead, M.player.ghost, M.onTaxi, M.instance, M.mounted = false, false, false, false, false
    M.player.level = 26
    M.group = {}
    if M.inCombat then M.LeaveCombat() end
    T:PlayerState(true)
  end
  T:ApplyPreset("tout")   -- tout voir : le pire cas
  local STATES = {
    { "solo", function() end },
    { "groupe", function()
      M.group = { { name = "Bravo-test", class = "MAGE", level = 25, online = true },
                  { name = "Charlie-test", class = "PRIEST", level = 27, online = true } }
    end },
    { "combat", function()
      M.target = { exists = true, name = "Ours-test", level = 26, hostile = true, guid = "Creature-0-0-0-0-7-1" }
      M.EnterCombat()
    end },
    { "mort", function() M.player.dead = true; M.player.health = 0; pcall(ctx.fire, "PLAYER_DEAD") end },
    { "fantôme", function() M.player.ghost = true; pcall(ctx.fire, "PLAYER_ALIVE") end },
    { "vol", function() M.onTaxi = true end },
    { "instance", function() M.instance = true end },
    { "niveau 1", function() M.player.level = 1; pcall(ctx.fire, "PLAYER_LEVEL_UP", 1) end },
    { "niveau 60", function() M.player.level = 60; pcall(ctx.fire, "PLAYER_LEVEL_UP", 60) end },
  }
  for _, s in ipairs(STATES) do
    reset()
    s[2]()
    T:PlayerState(true)
    settle(12)
    sweep(s[1])
  end
  reset()
  M.player.health = M.player.healthMax
  M.target = { exists = false }
  settle(4)
  check(nLines > 300, "assez de lignes vues (" .. nLines .. ")")
  if nBad > 60 then check(false, (nBad - 60) .. " autres lignes en défaut") end
  T:ApplyPreset("simple")
end
