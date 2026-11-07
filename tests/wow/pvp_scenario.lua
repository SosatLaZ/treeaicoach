-- Scenario for Modules/PvP.lua (run by tests/wow/test_pvp.py on the real addon, fake names only).
-- The generic mock has no PvP API: this scenario installs mocked battleground widgets, map POIs,
-- scoreboard, queue status and enemy-player units, then removes them.
return function(M, ctx)
  local T = TreeAI
  local P = T and T.modules and T.modules.PvP
  ctx.check(P and P.db and P.db.enabled, "PvP registered + enabled")
  if not P then return end
  local S = T._suggestions
  M.LeaveCombat()
  M.player.level = 26

  -- PvP mocks -------------------------------------------------------------------------------------
  local orig = {}
  local function set(name, v) orig[name] = orig[name] or { rawget(_G, name) }; rawset(_G, name, v) end
  local bg, widgets, pois, scores, status, winner = false, {}, {}, {}, nil, nil
  local function unitData(u)
    u = tostring(u):lower()
    if u == "target" then return M.target.exists and M.target or nil end
    return M.units[u]
  end
  local oIsPlayer, oCanAttack = UnitIsPlayer, UnitCanAttack
  set("UnitIsPlayer", function(u) local d = unitData(u) if d and d.isPlayer then return true end return oIsPlayer(u) end)
  set("UnitCanAttack", function(a, u) local d = unitData(u) if d and d.isPlayer then return d.hostile and true or false end return oCanAttack(a, u) end)
  local oGuild = GetGuildInfo
  set("GetGuildInfo", function(u) local d = unitData(u) if d and d.guild then return d.guild end return oGuild(u) end)
  set("UnitFactionGroup", function(u) if u == "player" then return "Alliance", "Alliance" end local d = unitData(u) return d and d.faction end)
  local oInst = IsInInstance
  set("IsInInstance", function() if bg then return true, "pvp" end return oInst() end)
  set("C_UIWidgetManager", { GetIconAndTextWidgetVisualizationInfo = function(id)
    return widgets[id] and { text = widgets[id], state = 1 } or nil end })
  set("C_AreaPoiInfo", { GetAreaPOIForMap = function() local l = {} for id in pairs(pois) do l[#l + 1] = id end return l end,
    GetAreaPOIInfo = function(_, id) return pois[id] end })
  set("GetNumBattlefieldScores", function() return #scores end)
  set("GetBattlefieldScore", function(i) local r = scores[i] if r then
    return r[1], r[2], r[3], 0, 0, r[4], 0, "Orc", "?", r[5], r[6], r[7] end end)
  set("GetMaxBattlefieldID", function() return 2 end)
  set("GetBattlefieldStatus", function(i) if i == 1 and status then return status, "Bassin Arathi" end return "none" end)
  set("GetBattlefieldPortExpiration", function() return 85 end)
  set("GetBattlefieldWinner", function() return winner end)
  set("RequestBattlefieldScoreData", function() end)

  local function lines() return T:GetTabLines("pvp") end
  local function find(pat)
    for _, l in ipairs(lines()) do if T.PlainText(l.text):find(pat) then return l end end
  end

  -- 0. pure helpers
  local e = P.ParseBGSystem("Le drapeau de l'Alliance a été ramassé par Brisecrâne !")
  ctx.check(e and e.kind == "pick" and e.name == "Brisecrâne" and e.flag == "Alliance", "FR flag pick-up parsed")
  e = P.ParseBGSystem("The Horde Flag was picked up by Lamefine!")
  ctx.check(e and e.kind == "pick" and e.name == "Lamefine" and e.flag == "Horde", "EN flag pick-up parsed")
  e = P.ParseBGSystem("Brisecrâne a pris le drapeau de l'Alliance !")
  ctx.check(e and e.kind == "cap" and e.flag == "Alliance", "FR capture parsed")
  e = P.ParseBGSystem("Lamefine a attaqué la ferme ! Si personne n'intervient, l'Alliance s'en emparera dans 1 minute !")
  ctx.check(e and e.kind == "assault" and e.node == "la ferme", "FR assault parsed: " .. tostring(e and e.node))
  e = P.ParseBGSystem("La Horde a pris l'écurie !")
  ctx.check(e and e.kind == "take" and e.flag == "Horde" and e.node == "l'écurie", "FR node taken parsed")
  local b, s, mx = P.ScoreFromText("Bases : 3  Ressources : 1200/2000")
  ctx.check(b == 3 and s == 1200 and mx == 2000, "AB widget text parsed")
  ctx.check(P.ParseHonor("Ombre meurt, victoire honorable Rang : Sergent (Points d'honneur estimés : 87)") == 87, "FR honor parsed")
  ctx.check(P.ParseHonor("You have been awarded 82 honor.") == 82, "EN bonus honor parsed")
  local pr = P.Predict({ score = 1500, max = 2000, bases = 3 }, { score = 1200, max = 2000, bases = 2 }, "Alliance")
  ctx.check(pr and pr.winner == "Alliance" and pr.eta and math.abs(pr.eta - 300) < 1 and pr.pWin > 0.9,
    "AB prediction: Alliance wins in 5:00, high chance")
  ctx.check(P.Danger({ level = -1 }, 26, "HUNTER") == 10 and P.Danger({ level = 26, class = "ROGUE" }, 26, "HUNTER") == 7,
    "danger rating (skull = 10, same-level rogue vs hunter = 7)")

  -- secret values (in combat on Forever) are ignored, never compared
  set("issecretvalue", function(v) return v == "SECRET" end)
  ctx.check(P.ParseBGSystem("SECRET") == nil and P.ScoreFromText("SECRET") == nil, "secret values ignored")
  set("issecretvalue", nil)

  -- 1. PvP help OFF (default): silent, no tab
  ctx.check(not T.settings.pvp, "pvp off by default")
  M.units.nameplate1 = { name = "Ombrelame", isPlayer = true, hostile = true, level = 60, class = "ROGUE",
    guid = "Player-0-00000101", guild = "Les Fourbes" }
  M.units.nameplate2 = { name = "Brisecrâne", isPlayer = true, hostile = true, level = 60, class = "WARRIOR",
    guid = "Player-0-00000102", health = 300, healthMax = 1000 }
  ctx.fire("NAME_PLATE_UNIT_ADDED", "nameplate1")
  P:AutoRefresh()
  ctx.check(not T.tabs.pvp and not S["PvP:danger"], "nothing when pvp help is off")

  -- 2. ON: tab « JcJ », world PvP danger alert
  T.settings.pvp = true
  P:AutoRefresh()
  ctx.check(T.tabs.pvp and T.tabs.pvp.title == "JcJ", "JcJ tab registered when enabled")
  ctx.fire("NAME_PLATE_UNIT_ADDED", "nameplate1")
  ctx.fire("NAME_PLATE_UNIT_ADDED", "nameplate2")
  local d = S["PvP:danger"]
  ctx.check(d and d.prio == 3 and d.text:find("Danger : 2 ennemis niv 60 près de toi"), "danger alert: " .. tostring(d and d.text))
  M.Advance(1)
  local l = find("Voleur Ombrelame niv 60 <Les Fourbes>")
  ctx.check(l and l.onClick, "enemy line with class, level, guild")
  -- click = macro « TAI Cible » that the player presses himself
  if l then T:UserAction(l.onClick) end
  local idx = GetMacroIndexByName("TAI Cible")
  ctx.check(idx > 0 and GetMacroBody(idx) == "/targetexact Ombrelame", "click writes the /targetexact macro (no auto-target)")
  M.EnterCombat()
  if l then T:UserAction(l.onClick) end
  ctx.check(GetMacroBody(idx) == "/targetexact Ombrelame", "no macro edit in combat")
  M.LeaveCombat()

  -- KOS list
  P:AddKOS("Brisecrâne", "m'a tué au moulin")
  ctx.check(P.db.kos["brisecrâne"] and P.db.kos["brisecrâne"].note == "m'a tué au moulin", "KOS added")
  ctx.fire("NAME_PLATE_UNIT_ADDED", "nameplate2")
  d = S["PvP:danger"]
  ctx.check(d and d.text:find("Brisecrâne %(KOS%)"), "KOS alert: " .. tostring(d and d.text))

  -- 3. target info: class tips, hunter counter, visible defensive aura
  M.target = { exists = true, name = "Ombrelame", isPlayer = true, hostile = true, level = 60, class = "ROGUE",
    guid = "Player-0-00000101", health = 800, healthMax = 1000 }
  M.auras.target = { { name = "Évasion", spellId = 5277, expirationTime = M.now + 12, duration = 15 } }
  ctx.fire("PLAYER_TARGET_CHANGED")
  P:Refresh(true)
  ctx.check(find("Cible : Voleur Ombrelame niv 60 — danger extrême"), "target danger line")
  ctx.check(find("Évasion actif — 12 s"), "visible Evasion aura with its duration")
  ctx.check(find("Fusée éclairante"), "class tip / hunter counter")
  M.target = { exists = false }
  M.auras.target = {}

  -- 4. queue pop countdown
  status = "confirm"
  ctx.fire("UPDATE_BATTLEFIELD_STATUS")
  P:Refresh(true)
  ctx.check(find("Entrée prête : Bassin Arathi — 1:25 pour accepter"), "queue pop countdown")

  -- 5. inside the battleground
  status, bg = "active", true
  M.player.zone = "Bassin Arathi"
  ctx.fire("UPDATE_BATTLEFIELD_STATUS")
  ctx.check(P.bg and P.bg.name == "Bassin Arathi", "battleground start detected")
  -- scores from widgets
  widgets[1893] = "Bases : 3  Ressources : 1500/2000"
  widgets[1894] = "Bases : 2  Ressources : 1200/2000"
  ctx.fire("UPDATE_UI_WIDGET", { widgetID = 1893 })
  P:Refresh(true)
  local pl = find("Prévision : on gagne dans 5:00")
  ctx.check(pl, "score prediction line")
  ctx.check(pl and T.PlainText(pl.text):find("victoire 9%d %%"), "win probability: " .. tostring(pl and pl.text))
  -- objective timers: assault message (no POI yet), then a contested POI
  ctx.fire("CHAT_MSG_BG_SYSTEM_HORDE", "Lamefine a attaqué la ferme ! Si personne n'intervient, la Horde s'en emparera dans 1 minute !")
  P:Refresh(true)
  ctx.check(find("la ferme : 1:00"), "assault timer from system message")
  pois[1] = { name = "Écuries", textureIndex = 3, areaPoiID = 1 }
  ctx.fire("AREA_POIS_UPDATED")
  M.Advance(10)
  P:Refresh(true)
  ctx.check(find("Écuries : 0:50 %(nous%)"), "POI contested timer (Alliance icon)")
  pois[1].textureIndex = 2   -- captured: neutral icon
  ctx.fire("AREA_POIS_UPDATED")
  P:Refresh(true)
  ctx.check(not find("Écuries :"), "timer cleared when captured")
  -- flag carrier (Warsong style message) + health from a visible nameplate
  ctx.fire("CHAT_MSG_BG_SYSTEM_HORDE", "Le drapeau de l'Alliance a été ramassé par Brisecrâne !")
  ctx.fire("NAME_PLATE_UNIT_ADDED", "nameplate2")
  P:Refresh(true)
  local cl = find("Ennemi Brisecrâne a le drapeau de l'Alliance — 30 %%")
  ctx.check(cl and cl.prio == 3 and cl.onClick, "enemy flag carrier line with health %")
  P:CheckDanger()
  ctx.check(not S["PvP:danger"], "no world danger alert inside a battleground")
  ctx.fire("CHAT_MSG_BG_SYSTEM_ALLIANCE", "Le drapeau de l'Alliance a été lâché par Brisecrâne !")
  P:Refresh(true)
  ctx.check(not find("a le drapeau"), "carrier cleared on drop")
  -- enemy team composition from the scoreboard (faction 0 = Horde = enemy)
  scores = {
    { "Lamefine", 3, 12, 0, "ROGUE", 9000, 100 },
    { "Soignelune", 0, 4, 0, "PRIEST", 800, 12000 },
    { "Brisecrâne", 2, 8, 0, "WARRIOR", 7000, 0 },
    { "Ami-Test", 1, 5, 1, "MAGE", 5000, 0 },
  }
  ctx.fire("UPDATE_BATTLEFIELD_SCORE")
  P:Refresh(true)
  ctx.check(find("Ennemis : 3 — 1 Prêtre, 1 Voleur, 1 Guerrier"), "enemy composition")
  ctx.check(find("Soigneurs ennemis %(estimés%) : 1 — Soignelune"), "healer estimate")
  ctx.check(find("Meilleur tueur ennemi : Lamefine %(12"), "top enemy killer")
  -- honor
  ctx.fire("CHAT_MSG_COMBAT_HONOR_GAIN", "Lamefine meurt, victoire honorable Rang : Soldat (Points d'honneur estimés : 90)")
  ctx.fire("CHAT_MSG_COMBAT_HONOR_GAIN", "Vous avez reçu 210 points d'honneur.")
  local st = P:SessionStats()
  ctx.check(st.honor == 300 and st.hk == 1, "session honor/kills: " .. st.honor .. "/" .. st.hk)
  -- end: Alliance wins (1), then leave
  M.Advance(600)
  winner = 1
  ctx.fire("UPDATE_BATTLEFIELD_STATUS")
  local h = P.char.history
  ctx.check(#h == 1 and h[1].won == true and h[1].honor == 300 and h[1].bg == "Bassin Arathi", "game recorded in history")
  ctx.fire("UPDATE_BATTLEFIELD_STATUS")
  ctx.check(P.bg == nil, "no restart while the scoreboard stays up")
  bg, status, winner = false, nil, nil
  ctx.fire("UPDATE_BATTLEFIELD_STATUS")
  P:Refresh(true)
  ctx.check(find("Meilleur BG pour l'honneur : Bassin Arathi"), "best BG by honor/hour")
  ctx.check(find("Session : 300 honneur"), "session line")

  -- 6. OFF again: tab removed, alert cleared
  T.settings.pvp = false
  P:AutoRefresh()
  ctx.check(not T.tabs.pvp and not S["PvP:danger"], "tab hidden + alert cleared when pvp help is turned off")

  for name, v in pairs(orig) do rawset(_G, name, v[1]) end
  M.units = {}
end
