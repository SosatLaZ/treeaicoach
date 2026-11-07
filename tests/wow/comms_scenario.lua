-- Harness scenario: Core comms obey WoW Forever's addon-message limits — token bucket (burst 8, 1/s),
-- result codes checked (3/8 throttled -> retried with backoff, 11 lockdown -> paused), state before quest
-- logs, pause in dungeons. Fake names only.
-- lua5.1 tests/wow/harness.lua --scenario tests/wow/comms_scenario.lua wow_addon/TreeAI
return function(M, ctx)
  local T = TreeAI
  M.group = { { name = "Amie", class = "PRIEST", level = 25, online = true } }
  M.FireEvent("GROUP_ROSTER_UPDATE")
  M.Advance(30, 0.5)  -- laisse partir le trafic de démarrage
  local real = C_ChatInfo and C_ChatInfo.SendAddonMessage or SendAddonMessage
  local codes, calls = {}, 0
  local function fake(...)
    calls = calls + 1
    local c = table.remove(codes, 1)
    if c ~= nil then return c end
    return real(...)
  end
  if C_ChatInfo and C_ChatInfo.SendAddonMessage then C_ChatInfo.SendAddonMessage = fake else SendAddonMessage = fake end
  local function sentOf(kind)
    local n = 0
    for _, s in ipairs(M.sentAddon) do if tostring(s.msg):match("^1~" .. kind .. "~") then n = n + 1 end end
    return n
  end

  -- rafale : 20 messages distincts -> 8 tout de suite, le reste à 1/s
  for i = 1, 20 do T:Send("TST" .. string.char(64 + i), "x") end
  local burst = 0
  for i = 1, 20 do burst = burst + sentOf("TST" .. string.char(64 + i)) end
  ctx.check(burst <= 8, "rafale limitée à 8 (" .. burst .. ")")
  M.Advance(6, 0.25)
  local later = 0
  for i = 1, 20 do later = later + sentOf("TST" .. string.char(64 + i)) end
  ctx.check(later > burst and later <= burst + 7, "puis ~1 message/s (" .. later .. ")")
  M.Advance(20, 0.25)
  later = 0
  for i = 1, 20 do later = later + sentOf("TST" .. string.char(64 + i)) end
  ctx.check(later == 20, "tout finit par partir (" .. later .. ")")

  -- code « throttle » : réessayé, pas perdu
  M.Advance(10, 0.5)
  codes = { 3, 8, false }
  T:Send("RTRY", "retry-me")
  M.Advance(15, 0.25)
  ctx.check(sentOf("RTRY") == 1, "message limité (3, 8, false) réessayé puis envoyé")
  ctx.check(T.commStats.throttled >= 3, "compteur throttle")

  -- priorité : l'état passe avant les journaux de quêtes
  M.Advance(10, 0.5)
  local before = #M.sentAddon
  T:Send("SQQ", string.rep("q", 2000))  -- 10 morceaux, file basse
  T:Send("SQS", { v = 2, l = 25 })
  M.Advance(15, 0.1)
  local firstS, lastQ
  for i = before + 1, #M.sentAddon do
    local msg = tostring(M.sentAddon[i].msg)
    if msg:match("^1~SQS~") and not firstS then firstS = i end
    if msg:match("^1~SQQ~%d+~10/10~") then lastQ = i end
  end
  ctx.check(firstS and lastQ and firstS < lastQ, "SQS passe devant les morceaux de SQQ en file")
  -- remplacement : deux états d'affilée en file -> un seul part
  T:Send("OLDS", "old"); T:Send("OLDS", "new")
  M.Advance(30, 0.25)
  local olds = 0
  for _, s in ipairs(M.sentAddon) do if tostring(s.msg):find("~rold", 1, true) then olds = olds + 1 end end
  ctx.check(olds <= 1, "état périmé remplacé")

  -- donjon : pause claire, rien n'est envoyé, reprise à la sortie
  local realInst = IsInInstance
  IsInInstance = function() return true, "party" end
  M.FireEvent("PLAYER_ENTERING_WORLD")
  ctx.check(T.commPaused == "donjon", "pause en donjon")
  ctx.check(ctx.out("Synchro du groupe en pause %(donjon%)"), "message « synchro en pause (donjon) »")
  local n0 = #M.sentAddon
  ctx.check(T:Send("SQS", { v = 2 }) == false, "Send refusé en donjon")
  M.Advance(5, 0.5)
  ctx.check(#M.sentAddon == n0, "aucun message en donjon")
  IsInInstance = realInst
  M.FireEvent("PLAYER_ENTERING_WORLD")
  ctx.check(T.commPaused == nil, "reprise hors donjon")
  -- verrou du jeu (code 11)
  codes = { 11 }
  T:Send("LOCK", "x")
  M.Advance(1, 0.25)
  ctx.check(T.commPaused == "bloquée", "code 11 -> pause")
  M.FireEvent("ZONE_CHANGED_NEW_AREA")
  ctx.check(T.commPaused == nil, "reprise au changement de zone")
  local st = T:CommStatus()
  ctx.check(type(st) == "table" and st.stats.sent > 0, "CommStatus")
  ctx.check(T._ClassifySend(true, 0) == "ok" and T._ClassifySend(true, true) == "ok"
    and T._ClassifySend(true, 3) == "throttle" and T._ClassifySend(true, 11) == "lock"
    and T._ClassifySend(true, 5) == "error" and T._ClassifySend(false) == "error", "codes de retour")
end
