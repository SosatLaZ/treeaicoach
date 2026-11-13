-- Harness scenario (5.0, agent STABLE) — « jouer sans que ça bugue » : the core mechanisms that keep the game fluid,
-- each replayed from a real-game complaint. Run by tests/wow/test_stable.py (Forever mock, hunter 30, group of 4).
--   1. quest-log noise: QUEST_LOG_UPDATE that changes nothing wakes nobody; a real change is delivered at once; a
--      stream of noise can no longer hold the real events back for ever (4.3.6 bug: nothing updated while the party
--      was questing)
--   2. roster noise: GROUP_ROSTER_UPDATE without any change of the group is dropped
--   3. coalesced flows: T:OnBags / OnQuestLog / OnRoster / OnItemInfo / OnPlates — one call per burst, never in the
--      event's frame, held during combat, nameplate removal immediate
--   4. unit events of nameplates never reach handlers that asked for the player / the group
--   5. unit tooltips: one answer per unit, cached; nothing for players, nothing in combat
--   6. T.Spikes: a call over 8 ms and a frame over 12 ms are recorded (module, job, ms, combat, plates, group)
--   7. watchdog: 5 errors in a minute, or repeated spikes, put the module to sleep with ONE line; WakeAll restarts it
--   8. T:SetQuestGuide: one switch for all the quest guidance, QUEST_GUIDE_CHANGED, /tai quetes on|off
--   9. QuestieDB reads memoised; API probe without any player name; /tai pics opens the window page
return function(M, ctx)
  local T = TreeAI
  local check = ctx.check
  local function out(s) io.write("STABLE ", s, "\n") end
  if not check(T.OnBags and T.OnQuestLog and T.OnPlates and T.OnRoster and T.OnItemInfo and T.OnUnitTip and T.Spikes
      and T.Watchdog and T.SetQuestGuide and T.QuestGuideOn and T.Guard and T.Spread, "5.0 core API present") then return end
  M.LeaveCombat()
  M.player.level = 30
  M.quests = {
    { id = 9101, title = "Les loups", level = 28, objectives = { { text = "Loup tué : 1/8", done = false, have = 1, need = 8 } } },
    { id = 9102, title = "Le colis", level = 29, complete = true, objectives = { { text = "Colis : 1/1", done = true, have = 1, need = 1 } } },
  }
  M.group = {
    { name = "Amie", class = "PRIEST", level = 29, online = true, guid = "Player-1-0000000A" },
    { name = "Compagnon", class = "WARRIOR", level = 30, online = true, guid = "Player-1-0000000B" },
    { name = "Camarade", class = "MAGE", level = 29, online = true, guid = "Player-1-0000000C" },
  }
  ctx.fire("GROUP_ROSTER_UPDATE")
  ctx.fire("QUEST_LOG_UPDATE")
  M.Advance(12, 0.05)

  -- a probe module: counts what the dispatcher gives it
  local seen = {}
  local Pm = T:RegisterModule("ZzProbe", { title = "Sonde de test", defaults = { enabled = true } })
  local function count(k) seen[k] = (seen[k] or 0) + 1 end
  function Pm:OnEnable()
    self:RegisterEvent("QUEST_LOG_UPDATE", function() count("QLU") end)
    self:RegisterEvent("UNIT_QUEST_LOG_CHANGED", function() count("UQLC") end)
    self:RegisterEvent("QUEST_WATCH_UPDATE", function() count("QWU") end)
    self:RegisterEvent("GROUP_ROSTER_UPDATE", function() count("GRU") end)
    self:RegisterUnitEvent("UNIT_HEALTH", "player", nil, function() count("HP") end)
    self:RegisterUnitEvent("UNIT_POWER_UPDATE", "group", nil, function() count("POW") end)
  end
  T:SetModuleEnabled("ZzProbe", false)   -- (registered before its OnEnable existed: restarted so the events are registered)
  T:SetModuleEnabled("ZzProbe", true)
  check(Pm._active, "probe module active")
  M.Advance(2, 0.05)
  local function reset() for k in pairs(seen) do seen[k] = nil end end
  local function n(k) return seen[k] or 0 end
  local q1 = M.quests[1]
  local function kill()
    local o = q1.objectives[1]
    o.have = o.have + 1
    o.text = ("Loup tué : %d/8"):format(o.have)
  end

  -- ------------------------------------------------------------------ 1. quest-log noise
  reset()
  for _ = 1, 10 do ctx.fire("QUEST_LOG_UPDATE") M.Advance(0.5, 0.05) end
  check(n("QLU") == 0, "QUEST_LOG_UPDATE sans changement : personne n'est réveillé (" .. n("QLU") .. ")")
  kill()
  ctx.fire("QUEST_WATCH_UPDATE", 9101) ctx.fire("QUEST_LOG_UPDATE") ctx.fire("UNIT_QUEST_LOG_CHANGED", "player")
  check(n("QLU") == 1 and n("QWU") == 1 and n("UQLC") == 1, ("monstre tué : chaque sorte remise tout de suite, une fois (%d %d %d)")
    :format(n("QLU"), n("QWU"), n("UQLC")))
  ctx.fire("QUEST_LOG_UPDATE") ctx.fire("QUEST_LOG_UPDATE") ctx.fire("UNIT_QUEST_LOG_CHANGED", "party1")
  M.Advance(4, 0.05)
  check(n("QLU") == 1, "les QUEST_LOG_UPDATE qui suivent le même monstre : rien de plus (" .. n("QLU") .. ")")
  check(n("UQLC") == 2, "le journal d'un équipier (party1) passe tel quel aux modules de groupe (" .. n("UQLC") .. ")")
  -- the 4.3.6 starvation: noise every 0.5 s, a kill in the middle — it must be seen quickly, not « when it calms down »
  reset()
  local seenAt
  for i = 1, 24 do
    if i == 5 then kill() ctx.fire("QUEST_LOG_UPDATE") end
    ctx.fire("QUEST_LOG_UPDATE")
    M.Advance(0.5, 0.05)
    if n("QLU") > 0 and not seenAt then seenAt = i end
  end
  check(seenAt and seenAt <= 9, "monstre tué sous un flot de QUEST_LOG_UPDATE : vu en moins de 2 s (tour " .. tostring(seenAt) .. ")")
  check(n("QLU") <= 2, "… et une seule remise, pas une par événement (" .. n("QLU") .. ")")
  out(("bruit du journal : %d événements jetés"):format(T._questBurst.noise or 0))

  -- ------------------------------------------------------------------ 2. roster noise
  reset()
  for _ = 1, 8 do ctx.fire("GROUP_ROSTER_UPDATE") M.Advance(0.3, 0.05) end
  check(n("GRU") == 0, "GROUP_ROSTER_UPDATE sans changement du groupe : jeté (" .. n("GRU") .. ")")
  M.group[3].online = false
  ctx.fire("GROUP_ROSTER_UPDATE")
  check(n("GRU") == 1, "un équipier se déconnecte : remis tout de suite")
  M.group[3].online = true
  ctx.fire("GROUP_ROSTER_UPDATE")
  check(n("GRU") == 2, "il revient : remis")

  -- ------------------------------------------------------------------ 3. coalesced flows
  local fl = { bags = {}, quest = {}, roster = {}, items = {}, plates = {}, gone = {} }
  local inEvent = false
  T:OnBags(Pm, function(_, info) fl.bags[#fl.bags + 1] = { info = info, inEvent = inEvent, t = M.now } end)
  T:OnQuestLog(Pm, function(_, info) fl.quest[#fl.quest + 1] = { info = info, inEvent = inEvent } end)
  T:OnRoster(Pm, function(_, info) fl.roster[#fl.roster + 1] = info end)
  T:OnItemInfo(Pm, function(_, info) fl.items[#fl.items + 1] = info end)
  T:OnPlates(Pm, function(_, units) local c = {} for u in pairs(units) do c[#c + 1] = u end table.sort(c) fl.plates[#fl.plates + 1] = c end,
    function(_, u) fl.gone[#fl.gone + 1] = u end)
  local function fireIn(...) inEvent = true M.FireEvent(...) inEvent = false end
  -- a new subscriber gets the first burst whatever it holds (it has seen nothing yet): primed here
  fireIn("QUEST_LOG_UPDATE") fireIn("GROUP_ROSTER_UPDATE")
  M.Advance(4, 0.05)
  check(#fl.quest == 1 and #fl.roster == 1, "premier appel après l'abonnement : l'état de départ (" .. #fl.quest .. ", " .. #fl.roster .. ")")
  for k in pairs(fl) do fl[k] = {} end
  -- bags: 20 changes in a burst (loot / vendor) → one call, later
  local t0 = M.now
  for i = 1, 20 do fireIn("BAG_UPDATE", i % 5) fireIn("BAG_UPDATE_DELAYED") if i % 4 == 0 then M.Advance(0.05, 0.05) end end
  check(#fl.bags == 0, "sacs : rien dans l'image de l'événement")
  M.Advance(1.5, 0.05)
  check(#fl.bags == 1, "sacs : 20 BAG_UPDATE = UN appel (" .. #fl.bags .. ")")
  local b = fl.bags[1]
  check(b and not b.inEvent and b.t - t0 >= 0.3, "sacs : au moins 0,3 s après, hors de l'événement")
  check(b and b.info.bags and b.info.bags[0] and b.info.bags[4] and b.info.delayed, "sacs : info.bags = sacs touchés")
  -- continuous storm: at most ~1 call per second
  fl.bags = {}
  for _ = 1, 60 do fireIn("BAG_UPDATE", 0) fireIn("BAG_UPDATE_DELAYED") M.Advance(0.05, 0.05) end
  M.Advance(1.5, 0.05)
  check(#fl.bags >= 2 and #fl.bags <= 5, "sacs : rafale continue de 3 s → un appel par seconde au plus (" .. #fl.bags .. ")")
  -- combat: held, delivered merged at the end of the fight
  fl.bags = {}
  M.EnterCombat()
  fireIn("BAG_UPDATE", 1) fireIn("BAG_UPDATE_DELAYED")
  M.Advance(2, 0.05)
  check(#fl.bags == 0, "sacs : rien en combat (sans opts.combat)")
  fireIn("BAG_UPDATE", 3) fireIn("BAG_UPDATE_DELAYED")
  M.Advance(1, 0.05)
  M.LeaveCombat()
  M.Advance(1, 0.05)
  check(#fl.bags == 1 and fl.bags[1].info.bags[1] and fl.bags[1].info.bags[3], "sacs : remis à la fin du combat, infos fusionnées (" .. #fl.bags .. ")")
  -- quest log: noise → nothing; a kill → counts; an accept → structure
  fl.quest = {}
  for _ = 1, 6 do fireIn("QUEST_LOG_UPDATE") M.Advance(0.2, 0.05) end
  M.Advance(2, 0.05)
  check(#fl.quest == 0, "journal : le bruit n'appelle personne (" .. #fl.quest .. ")")
  kill()
  fireIn("QUEST_WATCH_UPDATE", 9101) fireIn("QUEST_LOG_UPDATE") fireIn("UNIT_QUEST_LOG_CHANGED", "player") fireIn("QUEST_LOG_UPDATE")
  check(#fl.quest == 0, "journal : rien dans l'image de l'événement")
  M.Advance(2, 0.05)
  check(#fl.quest == 1 and fl.quest[1].info.counts and not fl.quest[1].info.structure and not fl.quest[1].inEvent,
    "journal : monstre tué = UN appel, info.counts (pas structure) (" .. #fl.quest .. ")")
  fl.quest = {}
  table.insert(M.quests, { id = 9103, title = "Nouvelle", level = 30, objectives = { { text = "Ours tué : 0/5", done = false, have = 0, need = 5 } } })
  fireIn("QUEST_ACCEPTED", #M.quests, 9103) fireIn("QUEST_LOG_UPDATE") fireIn("UNIT_QUEST_LOG_CHANGED", "player") fireIn("QUEST_LOG_UPDATE")
  M.Advance(3, 0.05)
  check(#fl.quest == 1 and fl.quest[1].info.structure and fl.quest[1].info.accepted and fl.quest[1].info.accepted[9103],
    "journal : quête prise = UN appel, info.structure + info.accepted[9103] (" .. #fl.quest .. ")")
  -- roster / item info
  fl.roster, fl.items = {}, {}
  for _ = 1, 5 do fireIn("GROUP_ROSTER_UPDATE") end
  for i = 1, 90 do fireIn("GET_ITEM_INFO_RECEIVED", 20000 + i, true) if i % 30 == 0 then M.Advance(0.1, 0.05) end end
  M.Advance(4, 0.05)
  check(#fl.roster == 0, "groupe : rafale sans changement = aucun appel")
  check(#fl.items == 1 and fl.items[1].many and fl.items[1].items[20001], "infos d'objet : 90 GET_ITEM_INFO_RECEIVED = UN appel (" .. #fl.items .. ")")
  table.remove(M.group, 3)
  fireIn("GROUP_ROSTER_UPDATE") fireIn("GROUP_ROSTER_UPDATE")
  M.Advance(3, 0.05)
  check(#fl.roster == 1 and fl.roster[1].n ~= nil, "groupe : un départ = UN appel")
  M.group[3] = { name = "Camarade", class = "MAGE", level = 29, online = true, guid = "Player-1-0000000C" }
  fireIn("GROUP_ROSTER_UPDATE")
  M.Advance(3, 0.05)
  -- nameplates: grouped adds, immediate removal, a plate gone before delivery is not delivered
  fl.plates, fl.gone = {}, {}
  for i = 1, 12 do M.units["nameplate" .. i] = { name = "Loup " .. i, guid = "Creature-0-0-0-0-3101-" .. i, hostile = true, level = 28 } end
  for i = 1, 12 do fireIn("NAME_PLATE_UNIT_ADDED", "nameplate" .. i) end
  check(#fl.plates == 0, "barres de nom : rien dans l'image de l'événement")
  fireIn("NAME_PLATE_UNIT_REMOVED", "nameplate5")
  M.units.nameplate5 = nil
  check(#fl.gone == 1 and fl.gone[1] == "nameplate5", "barre partie : dite tout de suite")
  check(T:PlateCount() == 11, "T:PlateCount() = 11 (" .. T:PlateCount() .. ")")
  M.Advance(0.6, 0.01)
  local got = {}
  for _, c in ipairs(fl.plates) do for _, u in ipairs(c) do got[u] = (got[u] or 0) + 1 end end
  local nGot = 0
  for _ in pairs(got) do nGot = nGot + 1 end
  check(nGot == 11 and not got.nameplate5, "barres de nom : les 11 restantes données, pas celle partie avant (" .. nGot .. ")")
  check(#fl.plates <= 2, "barres de nom : regroupées (" .. #fl.plates .. " appel(s) pour 12 barres)")
  for i = 1, 12 do if M.units["nameplate" .. i] then fireIn("NAME_PLATE_UNIT_REMOVED", "nameplate" .. i) M.units["nameplate" .. i] = nil end end
  check(T:PlateCount() == 0, "plus aucune barre")

  -- ------------------------------------------------------------------ 4. unit events of the crowd
  reset()
  local st = T._stats.ZzProbe
  local calls0 = st and st.calls or 0
  for i = 1, 40 do
    M.units["nameplate" .. i] = { name = "Joueur", isPlayer = true, guid = "Player-1-" .. i, health = 100, healthMax = 100 }
    M.FireEvent("UNIT_HEALTH", "nameplate" .. i) M.FireEvent("UNIT_POWER_UPDATE", "nameplate" .. i, "MANA")
  end
  check(n("HP") == 0 and n("POW") == 0 and (T._stats.ZzProbe and T._stats.ZzProbe.calls or 0) == calls0,
    "vie / énergie de 40 barres de nom : aucun appel chez qui ne veut que le joueur / le groupe")
  M.FireEvent("UNIT_HEALTH", "player") M.FireEvent("UNIT_POWER_UPDATE", "party2", "MANA") M.FireEvent("UNIT_POWER_UPDATE", "raid7", "MANA")
  check(n("HP") == 1 and n("POW") == 2, "le joueur et le groupe passent (" .. n("HP") .. " " .. n("POW") .. ")")
  for i = 1, 40 do M.units["nameplate" .. i] = nil end
  -- another addon's messages never reach TreeAI's handler
  local c0 = T._stats.Core and T._stats.Core.calls or 0
  for _ = 1, 30 do M.FireEvent("CHAT_MSG_ADDON", "questie", "v=1", "GUILD", "Quelquun-Royaume") end
  check((T._stats.Core and T._stats.Core.calls or 0) == c0, "messages d'un autre addon : aucun appel")

  -- ------------------------------------------------------------------ 5. unit tooltips
  local tipN = 0
  T:OnUnitTip(Pm, function(_, unit, name, guid) tipN = tipN + 1 return "TreeAI test : " .. tostring(name), 1, 1, 0 end)
  local GT = GameTooltip
  local function tip(d)
    M.units.mouseover = d
    GT.__lines = {}
    GT.GetUnit = function() return d.name, "mouseover" end
    T._OnUnitTip(GT)
    local nl = 0
    for _, l in ipairs(GT.__lines) do if tostring(l):find("TreeAI test", 1, true) then nl = nl + 1 end end
    return nl
  end
  local wolf = { name = "Loup gris", guid = "Creature-0-0-0-0-3101-77", hostile = true, level = 28 }
  local lines = 0
  for _ = 1, 5 do lines = lines + tip(wolf) end
  check(tipN == 1 and lines == 5, ("infobulle : 5 survols du même monstre = UN calcul, la ligne à chaque fois (%d, %d)"):format(tipN, lines))
  tipN = 0
  for i = 1, 30 do tip({ name = "Joueur" .. i, isPlayer = true, guid = "Player-1-000000" .. i }) end
  check(tipN == 0, "infobulle : 30 joueurs survolés = aucun calcul (" .. tipN .. ")")
  M.EnterCombat()
  tipN = 0
  check(tip(wolf) == 0 and tipN == 0, "infobulle : rien en combat")
  M.LeaveCombat()
  M.Advance(1, 0.05)
  M.units.mouseover = nil

  -- ------------------------------------------------------------------ 6. spikes
  T.Spikes.Reset()
  local slowMs, boom = 0, false
  T:On("ZZ_WORK", Pm, function()
    if boom then error("erreur de test (stable_scenario)") end
    if slowMs > 0 then M.now = M.now + slowMs / 1000 end   -- the handler « takes » slowMs of the game's clock
  end)
  slowMs = 3
  T:Fire("ZZ_WORK")
  M.Advance(0.2, 0.05)
  local S = T.Spikes.Store()
  check(S.calls == 0 and S.frames == 0, "un travail de 3 ms : rien de noté")
  slowMs = 21
  M.units.nameplate1 = { name = "Loup", guid = "Creature-0-0-0-0-3101-1", hostile = true }
  M.FireEvent("NAME_PLATE_UNIT_ADDED", "nameplate1")
  T:Fire("ZZ_WORK")
  M.Advance(0.2, 0.05)
  S = T.Spikes.Store()
  local e = S.last[1]
  check(S.calls == 1 and e and e.m == "ZzProbe" and e.j == "ZZ_WORK" and math.abs(e.ms - 21) < 0.6 and e.k == "appel",
    "appel de 21 ms noté : module, travail, ms (" .. tostring(e and e.m) .. " " .. tostring(e and e.j) .. " " .. tostring(e and e.ms) .. ")")
  check(e and e.p == 1 and e.g == 4 and not e.c, "… avec les barres de nom (1), le groupe (4), hors combat")
  check(S.frames >= 1, "… et l'image entière (> 12 ms) notée aussi")
  slowMs = 5
  for _ = 1, 4 do T:Fire("ZZ_WORK") end
  M.Advance(0.2, 0.05)
  S = T.Spikes.Store()
  local img
  for _, x in ipairs(S.last) do if x.k == "image" and (x.n or 0) >= 4 then img = x end end
  check(img and img.ms >= 19 and img.b and img.b >= 4.5, "4 appels de 5 ms dans la même image : image de 20 ms notée, aucun appel isolé")
  check(S.calls == 1, "… (toujours un seul appel > 8 ms)")
  M.EnterCombat()
  slowMs = 9
  for _ = 1, 10 do T:Fire("ZZ_WORK") M.Advance(0.1, 0.05) end
  for _ = 1, 40 do T.Spikes.Note("ZzAutre", "travail " .. _, 8.5) end   -- 40 other jobs: the lists stay bounded
  M.LeaveCombat()
  M.Advance(0.5, 0.05)
  S = T.Spikes.Store()
  check(#S.last <= 30 and #S.top <= 30, ("au plus 30 derniers + 30 plus gros (%d, %d)"):format(#S.last, #S.top))
  local topE
  for _, x in ipairs(S.top) do if x.j == "ZZ_WORK" and x.k == "appel" then topE = x end end
  check(topE and topE.x == 11 and math.abs(topE.ms - 21) < 0.6, "les plus gros : une ligne par travail, le pire gardé, compté (×" .. tostring(topE and topE.x) .. ")")
  local sawCombat = false
  for _, x in ipairs(S.last) do if x.c then sawCombat = true end end
  check(sawCombat, "« en combat » noté")
  local L = T.Spikes.Lines(5, 5)
  check(type(L) == "table" and #L >= 4 and L[1]:find("Pics de lenteur", 1, true), "T.Spikes.Lines() : lignes lisibles")
  for _, l in ipairs(L) do out(l) end
  check(not Pm._asleep, "9 ms × 10 en combat : relevé, mais pas de quoi couper le module (" .. tostring(Pm._asleep) .. ")")
  M.units.nameplate1 = nil
  M.FireEvent("NAME_PLATE_UNIT_REMOVED", "nameplate1")

  -- ------------------------------------------------------------------ 7. watchdog
  local WD = T.Watchdog
  M.Advance(70, 1)   -- a fresh minute
  slowMs = 60
  for _ = 1, 3 do T:Fire("ZZ_WORK") M.Advance(1, 0.05) end
  check(not Pm._asleep, "3 pics : pas encore")
  T:Fire("ZZ_WORK")
  M.Advance(0.5, 0.05)
  check(Pm._asleep == "slow" and not Pm._active, "4 pics de 60 ms en une minute : le module est mis en pause")
  local line
  local nLines = 0
  for _, s in ipairs(T:GetSuggestions()) do if s.id == "core:asleep" then line = s.text nLines = nLines + 1 end end
  check(nLines == 1 and line == "Fonction Sonde de test mise en pause : trop lente ici", "UNE ligne : « " .. tostring(line) .. " »")
  reset()
  slowMs = 0
  M.FireEvent("UNIT_HEALTH", "player")
  T:Fire("ZZ_WORK")
  check(n("HP") == 0, "en pause : plus aucun événement pour lui")
  check(#WD.Asleep() == 1, "T.Watchdog.Asleep() = { ZzProbe }")
  check(WD.WakeAll() == 1 and Pm._active and not Pm._asleep, "T.Watchdog.WakeAll() le relance")
  M.Advance(1, 0.05)
  local still = false
  for _, s in ipairs(T:GetSuggestions()) do if s.id == "core:asleep" then still = true end end
  check(not still, "la ligne part avec la pause")
  M.FireEvent("UNIT_HEALTH", "player")
  check(n("HP") == 1, "relancé : les événements reviennent")
  -- errors: 5 in a minute
  M.Advance(70, 1)
  boom = true
  for _ = 1, 4 do T:Fire("ZZ_WORK") M.Advance(1, 0.05) end
  check(not Pm._asleep, "4 erreurs : pas encore")
  T:Fire("ZZ_WORK")
  M.Advance(0.5, 0.05)
  boom = false
  check(Pm._asleep == "err", "5 erreurs en une minute : en pause")
  line = nil
  for _, s in ipairs(T:GetSuggestions()) do if s.id == "core:asleep" then line = s.text end end
  check(line == "Fonction Sonde de test mise en pause : trop d'erreurs", "« " .. tostring(line) .. " »")
  check(T.Bugs and T.Bugs.SpikeLines and table.concat(T.Bugs.SpikeLines(), "\n"):find("Mis en pause par le chien de garde : ZzProbe", 1, true),
    "le relevé dit qui est en pause")
  WD.WakeAll()
  M.Advance(1, 0.05)
  -- in combat: events and timers stop at once (no frame is touched), OnDisable waits for the end of the fight
  local disabledAt
  function Pm:OnDisable() disabledAt = M.inCombat and "combat" or "calme" end
  M.Advance(70, 1)
  M.EnterCombat()
  slowMs = 60
  for _ = 1, 4 do T:Fire("ZZ_WORK") M.Advance(0.5, 0.05) end
  slowMs = 0
  M.Advance(0.5, 0.05)
  reset()
  M.FireEvent("UNIT_HEALTH", "player")
  check(Pm._asleep == "slow" and Pm._suspended and n("HP") == 0 and disabledAt == nil,
    "pause en combat : événements coupés tout de suite, OnDisable pas encore (" .. tostring(disabledAt) .. ")")
  M.LeaveCombat()
  M.Advance(1, 0.05)
  check(disabledAt == "calme" and not Pm._active, "… OnDisable à la sortie du combat (" .. tostring(disabledAt) .. ")")
  Pm.OnDisable = nil
  WD.WakeAll()
  M.Advance(1, 0.05)
  -- a guarded hook (tooltip / OnUpdate) is timed and silenced the same way
  local hookN = 0
  local hook = T:Guard(Pm, "crochet", function() hookN = hookN + 1 end)
  hook() hook()
  check(hookN == 2, "T:Guard : le crochet tourne")
  T:SetModuleEnabled("ZzProbe", false)
  hook()
  check(hookN == 2, "T:Guard : module coupé = crochet muet")
  T:SetModuleEnabled("ZzProbe", true)
  M.Advance(1, 0.05)

  -- ------------------------------------------------------------------ 7 bis. the auto-refresh cycle at rest, per module
  -- (4.3.6: ONE module without « autoIdleSafe » made every :AutoRefresh() run every 3 s while nothing happened)
  local ar = { safe = 0, plain = 0 }
  local Ps = T:RegisterModule("ZzCalme", { title = "Sonde calme", defaults = { enabled = true }, autoIdleSafe = true,
    AutoRefresh = function() ar.safe = ar.safe + 1 end })
  local Pp = T:RegisterModule("ZzCycle", { title = "Sonde cycle", defaults = { enabled = true },
    AutoRefresh = function() ar.plain = ar.plain + 1 end })
  T:SetModuleEnabled("ZzCalme", true) T:SetModuleEnabled("ZzCycle", true)
  M.speed = 0
  M.Advance(40, 0.2)                       -- settle: the last events of the sections above are behind
  ar.safe, ar.plain = 0, 0
  M.Advance(25, 0.2)                       -- 25 s of nothing: no event, no movement
  check(ar.plain >= 2, "au calme : un module sans « autoIdleSafe » garde son cycle (étiré) (" .. ar.plain .. ")")
  check(ar.safe <= 1, "au calme : un module « autoIdleSafe » n'est pas appelé, même si un autre ne l'est pas (" .. ar.safe .. ")")
  ar.safe = 0
  M.FireEvent("PLAYER_MONEY")
  M.Advance(8, 0.2)
  check(ar.safe >= 1, "un événement : il est rappelé au cycle suivant (" .. ar.safe .. ")")
  T:SetModuleEnabled("ZzCalme", false) T:SetModuleEnabled("ZzCycle", false)
  check(Ps and Pp, "sondes du cycle")

  -- ------------------------------------------------------------------ 8. quest guide
  local function activeGuide()
    local t = {}
    for name in pairs(T.QUEST_GUIDE) do local m = T.modules[name] if m and m._active then t[#t + 1] = name end end
    table.sort(t)
    return t
  end
  local function questLines() return #T:GetTabLines("quests") end
  local on0 = activeGuide()
  check(T:QuestGuideOn() and #on0 >= 6, "guide de quêtes allumé par défaut (" .. table.concat(on0, ",") .. ")")
  M.Advance(6, 0.1)
  local guideMsg
  T:On("QUEST_GUIDE_CHANGED", "stable_scenario", function(_, _, on) guideMsg = on end)
  M.RunSlash("/tai quetes off")
  M.Advance(4, 0.1)
  check(T:QuestGuideOn() == false and guideMsg == false, "/tai quetes off : coupé, message QUEST_GUIDE_CHANGED(false)")
  check(#activeGuide() == 0, "tous les modules du guide sont arrêtés (" .. table.concat(activeGuide(), ",") .. ")")
  local left = {}
  for _, e in ipairs(T:GetTabLines("quests")) do left[#left + 1] = tostring(e.id) .. " « " .. tostring(e.text) .. " »" end
  check(questLines() == 0, "plus aucune ligne dans l'onglet Quêtes (" .. table.concat(left, " | ") .. ")")
  local Cm = T.Compass
  local questTargets = 0
  for _, t in ipairs(Cm and Cm.targets or {}) do if t.questID or t.plan then questTargets = questTargets + 1 end end
  check(questTargets == 0, "boussole : plus de cible de quête")
  local sug = 0
  for _, s in ipairs(T:GetSuggestions()) do
    local id = tostring(s.id)
    if id:find("^Quests:") or id:find("^Route") or id:find("^Planner") or id:find("^Tracker") or id:find("^ZonePlan") then sug = sug + 1 end
  end
  check(sug == 0, "plus aucun conseil de quête")
  -- cost when off: the guide's modules do nothing at all on quest events
  local before = {}
  for name in pairs(T.QUEST_GUIDE) do before[name] = T._stats[name] and T._stats[name].calls or 0 end
  for _ = 1, 10 do kill() q1.objectives[1].have = 1 ctx.fire("QUEST_LOG_UPDATE") ctx.fire("QUEST_WATCH_UPDATE", 9101) M.Advance(1, 0.1) end
  local worked = {}
  for name in pairs(T.QUEST_GUIDE) do
    local c = T._stats[name] and T._stats[name].calls or 0
    if c ~= before[name] then worked[#worked + 1] = name end
  end
  check(#worked == 0, "guide coupé : aucun de ses modules ne travaille (" .. table.concat(worked, ",") .. ")")
  local Q = T.modules.Quests
  check(Q and Q._active and type(Q.lastList) == "table" and #Q.lastList >= 2, "le relevé du journal reste (groupe, donjons, objets de quête le lisent)")
  check(T.db.questGuide == false, "choix gardé (TreeAIDB.questGuide)")
  M.RunSlash("/tai quetes on")
  M.Advance(8, 0.1)
  check(T:QuestGuideOn() and guideMsg == true and #activeGuide() == #on0, "/tai quetes on : tout revient (" .. #activeGuide() .. "/" .. #on0 .. ")")
  check(questLines() > 0, "les lignes de quête reviennent (" .. questLines() .. ")")

  -- ------------------------------------------------------------------ 9. QuestieDB memo, API probe, /tai pics
  local reads = 0
  local ent = { Get = function(id, key) reads = reads + 1 if id == 7 then return nil end return { id, key } end }
  local get = T.API.QuestieMemo(ent, "Get", ent.Get)
  local a1, a2 = get(1, "spawns"), get(1, "spawns")
  get(7, "name") get(7, "name") get(2, "spawns")
  check(reads == 3 and a1 == a2, "QuestieDB : une lecture par (id, champ), même les réponses vides (" .. reads .. ")")
  check(T.API.QuestieMemo(ent, "Get", ent.Get) == get, "… un seul lecteur par source, partagé entre les modules")
  local B = T.Bugs
  local probe = B.ProbeAPI()
  local txt = table.concat(probe, "\n")
  local modern = type(C_QuestLog) == "table" and C_QuestLog.GetInfo ~= nil
  check(#probe > 80 and txt:find(modern and "C_QuestLog.GetInfo : 1 retour (table{" or "C_QuestLog.GetInfo : absente", 1, true)
    and txt:find("GetTalentInfo", 1, true)
    and txt:find("C_Traits.GetConfigInfo", 1, true) and txt:find("IsPlayerSpell", 1, true), "sonde d'API : talents, quêtes, sacs… (" .. #probe .. " lignes)")
  check(not txt:find(M.player.name, 1, true) and not txt:find("Amie", 1, true), "sonde d'API : aucun nom de joueur")
  local api = B.StoreProbe(true)
  check(api and T.db.diag.api and #T.db.diag.api.lines == #probe, "sonde gardée dans TreeAIDB.diag.api")
  local chat0 = #M.output
  M.RunSlash("/tai pics")
  M.Advance(0.5, 0.05)
  local Wn = T.Window
  check(#M.output == chat0, "/tai pics : rien dans le chat")
  if Wn and Wn._active and Wn.CurrentPage then
    check(Wn.IsOpen() and Wn.CurrentPage() == "pics", "/tai pics : la page « Pics de lenteur » de la fenêtre (" .. tostring(Wn.CurrentPage()) .. ")")
  end
  local rep = B.Build(true)
  local rt = table.concat(rep.lines, "\n")
  check(rt:find("== Pics de lenteur", 1, true) and rt:find("ZzProbe · ZZ_WORK", 1, true), "rapport /tai erreurs : section « Pics de lenteur »")
  check(#rep.lines <= 240, "rapport : " .. #rep.lines .. " lignes")
  T:SetModuleEnabled("ZzProbe", false)
end
