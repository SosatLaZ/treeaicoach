-- Harness scenario (ANNOY, round 5): the player's real journeys, hunting everything that gets on his nerves.
-- Run with --no-storm (a clean first login):
--   lua5.1 tests/wow/harness.lua --no-storm --scenario tests/wow/annoy_scenario.lua wow_addon/TreeAI
-- Asserts: first-login wizard waits for a calm moment and never re-opens once done; « clic droit = masquer »
-- sticks (modules re-suggesting the same thing do not bring it back; a new meaning or a danger does);
-- no chat spam entering / leaving dungeons or when the game blocks the same call 5×; the corner alert is not
-- replayed after /reload; nothing new pops up / no chat / no sound in a plain fight; group, vendor, mail,
-- trainer, AH, level-up, mount, taxi, UI scale: silent; many nameplates in a city: cheap; SavedVariables
-- after 40 days of play stay small; the « masqués » list is capped.
return function(M, ctx)
  local T = TreeAI
  local check, fire = ctx.check, ctx.fire

  local alerts = {}
  local showAlert = T.ShowAlert
  T.ShowAlert = function(self, e, f) alerts[#alerts + 1] = e and e.text or "?" return showAlert(self, e, f) end
  local function topShown()
    local s = {}
    for _, f in ipairs(M.frames) do
      local n = f.GetName and f:GetName()
      if n and n:find("^TreeAI") and f:IsShown() and (f:GetParent() == UIParent or f:GetParent() == nil) then s[n] = true end
    end
    return s
  end
  local o, snd, al = 0, 0, 0
  local function mark() o, snd, al = #M.output, #M.sounds, #alerts end
  local function chatSince() return #M.output - o end
  local function soundsSince() return #M.sounds - snd end
  local function alertsSince() return #alerts - al end

  -- 1. first login: the wizard waits while the player is dead (or flying / in a dungeon / fighting)
  local wiz = function() local w = T.GetWizard and T:GetWizard() return w and w:IsShown() end
  check(not T.db.onboarded, "1er lancement : pas encore d'assistant fini")
  M.player.dead = true
  fire("PLAYER_DEAD")
  M.Advance(10, 0.5)
  check(not wiz(), "assistant : pas pendant que le joueur est mort")
  M.player.dead = false
  fire("PLAYER_ALIVE")
  M.Advance(35, 0.5)
  check(wiz(), "assistant : ouvert au premier moment calme")
  if T.GetWizard and T:GetWizard() then T:GetWizard():Hide() end
  T.db.onboarded = T.version or true
  T:_MaybeWizard(0)
  M.Advance(2, 0.5)
  check(not wiz(), "assistant : jamais rouvert une fois fini / passé")

  -- 2. « clic droit = masquer » sticks
  M.Advance(10, 0.5)
  local ids = {}
  for _, e in ipairs(T:GetSuggestions()) do if not e.danger then ids[#ids + 1] = e.id; T:DismissSuggestion(e.id) end end
  check(#ids >= 3, "des conseils à masquer (" .. #ids .. ")")
  local function stormRefresh()
    for _, ev in ipairs({ "BAG_UPDATE_DELAYED", "UNIT_AURA", "PLAYER_XP_UPDATE", "QUEST_LOG_UPDATE",
      "UPDATE_INVENTORY_DURABILITY", "UNIT_HAPPINESS", "PET_UI_UPDATE", "UNIT_PET", "PLAYER_EQUIPMENT_CHANGED",
      "UPDATE_MACROS", "SPELLS_CHANGED", "ZONE_CHANGED_NEW_AREA" }) do
      fire(ev, ev:find("^UNIT_") and "player" or nil)
    end
  end
  for _ = 1, 4 do stormRefresh(); M.Advance(30, 0.5) end
  M.EnterCombat(); fire("PLAYER_REGEN_DISABLED"); M.Advance(8, 0.5); M.LeaveCombat(); fire("PLAYER_REGEN_ENABLED")
  M.Advance(20, 0.5)
  local back = {}
  local live = {}
  for _, e in ipairs(T:GetSuggestions()) do live[e.id] = e end
  for _, id in ipairs(ids) do if live[id] then back[#back + 1] = id .. " « " .. tostring(live[id].text) .. " »" end end
  check(#back == 0, "masqué = reste masqué (revenus : " .. table.concat(back, ", ") .. ")")
  T:Suggest("annoy:x", "Il te reste 12 flèches", 2, { ttl = 0 })
  T:DismissSuggestion("annoy:x")
  check(T:Suggest("annoy:x", "Il te reste 9 flèches", 2, { ttl = 0 }) == nil, "masqué : les chiffres qui changent ne le ramènent pas")
  check(T:Suggest("annoy:x", "Plus de flèches : achète-en", 2, { ttl = 0 }) ~= nil, "masqué : un nouveau sens revient")
  T:DismissSuggestion("annoy:x")
  check(T:Suggest("annoy:x", "Plus de flèches : achète-en", 3, { ttl = 0, danger = true }) ~= nil, "masqué : un danger passe toujours")
  T:ClearSuggestion("annoy:x")
  -- one merged line (same topic from two modules): hiding it hides both
  T:Suggest("Hunter:hungry", "Familier juste content : nourris-le", 2, { ttl = 0 })
  T:Suggest("QuickFight:feed", "Familier seulement content : nourris-le hors combat", 2, { ttl = 0, topic = "petfood" })
  T:DismissSuggestion("Hunter:hungry")
  local still = false
  for _, e in ipairs(T:GetSuggestions()) do if e.id == "QuickFight:feed" or e.id == "Hunter:hungry" then still = true end end
  check(not still, "masqué : la même info venue d'un autre module ne la remplace pas")
  -- kept across /reload (char data), expires after DISMISS_S
  local d = T:CharDB().dismissed
  check(type(d) == "table" and d[ids[1]], "masqués gardés dans les données du perso (/reload)")
  local savedTime = time
  time = function() return savedTime() + T.DISMISS_S + 60 end
  T:PruneDismissed()
  check(next(T:CharDB().dismissed) == nil, "masqués : oubliés après 2 h")
  time = savedTime
  for i = 1, 60 do T:Suggest("annoy:n" .. i, "Conseil numéro " .. i, 1, { ttl = 5 }); T:DismissSuggestion("annoy:n" .. i) end
  local n = 0
  for _ in pairs(T:CharDB().dismissed) do n = n + 1 end
  check(n <= 40, "masqués : liste plafonnée (" .. n .. ")")
  T:CharDB().dismissed = {}

  -- 3. dungeons: enter / leave twice -> at most the first explanation in the chat
  -- (the harness event storm may have toggled « /tai debug » : debug lines are not player chat — off, as by default)
  if T.settings then T.settings.debug = false end
  mark()
  for _ = 1, 2 do
    M.instance = true; fire("PLAYER_ENTERING_WORLD", false, false); M.Advance(5, 0.5)
    M.instance = false; fire("PLAYER_ENTERING_WORLD", false, false); M.Advance(5, 0.5)
  end
  check(chatSince() <= 1, "donjon ×2 : ≤ 1 ligne de chat (" .. chatSince() .. ")")
  mark()
  M.instance = true; fire("PLAYER_ENTERING_WORLD", false, false); M.Advance(2, 0.5)
  M.instance = false; fire("PLAYER_ENTERING_WORLD", false, false); M.Advance(2, 0.5)
  check(chatSince() == 0, "donjon : plus rien dans le chat la fois suivante")

  -- 4. the game blocking the same call 5× -> one chat line, 5 diagnostics
  mark()
  for _ = 1, 5 do fire("ADDON_ACTION_BLOCKED", "TreeAI", "UneFonction()") end
  check(chatSince() == 1, "action bloquée ×5 : 1 ligne de chat (" .. chatSince() .. ")")
  local nb = 0
  for _, b in ipairs(T.db.diag and T.db.diag.blocked or {}) do if b.func == "UneFonction()" then nb = nb + 1 end end
  check(nb == 5, "action bloquée : les 5 gardées pour le diagnostic")

  -- 5. corner alert not replayed by /reload (5 min memory in the char data)
  T:ResetAlertGate()
  T:Suggest("annoy:urgent", "Sacs pleins : vends tes objets gris", 3, { ttl = 0 })
  local nA = #alerts
  check(nA >= 1, "alerte importante montrée une fois")
  T:_SaveRecentAlerts()
  check(T:CharDB().recentAlerts and T:CharDB().recentAlerts["annoy:urgent"], "alertes récentes sauvées à la déconnexion")
  for k in pairs(T._alerted) do T._alerted[k] = nil end   -- what a /reload does to the Lua memory
  T:ResetAlertGate()
  T:ClearSuggestion("annoy:urgent")
  T:_RestoreRecentAlerts()
  T:Suggest("annoy:urgent", "Sacs pleins : vends tes objets gris", 3, { ttl = 0 })
  check(#alerts == nA, "après /reload : la même alerte ne repop pas")
  T:ClearSuggestion("annoy:urgent")

  -- 6. a plain fight: nothing new appears, no chat, no sound
  M.Advance(30, 0.5)
  mark()
  local before = topShown()
  M.target = { exists = true, name = "Ours", level = 27, health = 800, healthMax = 800, hostile = true,
    classification = "normal", creatureType = "Bête", guid = "Creature-0-0-0-0-2351-0009" }
  fire("PLAYER_TARGET_CHANGED")
  M.EnterCombat(); fire("PLAYER_REGEN_DISABLED")
  M.Advance(15, 0.25)
  local popped = {}
  for nm in pairs(topShown()) do if not before[nm] then popped[#popped + 1] = nm end end
  check(#popped == 0, "combat : aucun cadre qui surgit (" .. table.concat(popped, ", ") .. ")")
  check(chatSince() == 0, "combat : rien dans le chat")
  check(soundsSince() == 0, "combat : aucun son sans danger")
  check(alertsSince() == 0, "combat : aucun encadré sans danger")
  M.target = { exists = false }; fire("PLAYER_TARGET_CHANGED")
  M.LeaveCombat(); fire("PLAYER_REGEN_ENABLED")
  M.Advance(10, 0.5)

  -- 7. everyday visits: silent in the chat, no alert storm
  mark()
  M.mounted = true; fire("UNIT_AURA", "player"); M.speed = 14; M.Advance(15, 0.5)
  M.mounted = false; fire("UNIT_AURA", "player"); M.speed = 0
  M.onTaxi = true; fire("PLAYER_CONTROL_LOST"); M.Advance(15, 0.5)
  M.onTaxi = false; fire("PLAYER_CONTROL_GAINED"); M.Advance(5, 0.5)
  M.group = { { name = "Sansaddon", class = "PRIEST", level = 25, health = 700, healthMax = 800, online = true } }
  fire("GROUP_ROSTER_UPDATE"); M.Advance(40, 0.5)
  M.group = {}; fire("GROUP_ROSTER_UPDATE"); M.Advance(5, 0.5)
  M.player.level = M.player.level + 1
  fire("PLAYER_LEVEL_UP", M.player.level, 100, 50, 0, 1, 0, 0, 0, 0); M.Advance(10, 0.5)
  M.SetMerchant(true); M.Advance(3, 0.5); M.SetMerchant(false); M.Advance(2, 0.5)
  fire("MAIL_SHOW"); M.Advance(3, 0.5); fire("MAIL_CLOSED")
  fire("TRAINER_SHOW"); M.Advance(3, 0.5); fire("TRAINER_CLOSED")
  M.SetAuctionHouse(true); M.Advance(4, 0.5); M.SetAuctionHouse(false); M.Advance(2, 0.5)
  UIParent:SetSize(1600, 900); fire("UI_SCALE_CHANGED"); fire("DISPLAY_SIZE_CHANGED"); M.Advance(5, 0.5)
  check(chatSince() == 0, "monture / vol / groupe / niveau / marchand / courrier / maître / HV / échelle : rien dans le chat ("
    .. chatSince() .. ")")
  check(alertsSince() <= 2, "visites : ≤ 2 encadrés (" .. alertsSince() .. ")")

  -- 8. a city full of nameplates: cheap
  local c0 = os.clock()
  for i = 1, 40 do M.units["nameplate" .. i] = { name = "Garde " .. i, guid = "Creature-0-0-0-0-68-" .. i, hostile = false, level = 30 } end
  for _ = 1, 20 do
    for i = 1, 40 do fire("NAME_PLATE_UNIT_ADDED", "nameplate" .. i) end
    M.Advance(1, 0.1)
    for i = 1, 40 do fire("NAME_PLATE_UNIT_REMOVED", "nameplate" .. i) end
  end
  local dt = os.clock() - c0
  check(dt < 2.0, ("ville : 1600 barres de nom en 20 s traitées en %.2f s"):format(dt))

  -- 9. SavedVariables after 40 days of play
  local s1 = #T:Serialize(TreeAIDB)
  for day = 1, 40 do
    M.serverTime = M.serverTime + 86400
    M.player.money = (M.player.money or 0) + 1000; fire("PLAYER_MONEY")
    fire("PLAYER_ENTERING_WORLD", false, true)
    fire("QUEST_TURNED_IN", 2000 + day, 1500, 100)
    fire("PLAYER_XP_UPDATE", "player")
    M.Advance(30, 1)
    fire("PLAYER_LOGOUT")
  end
  local s2 = #T:Serialize(TreeAIDB)
  check(s2 - s1 < 32 * 1024, ("SavedVariables : +%d o en 40 jours (< 32 Ko)"):format(s2 - s1))
end
