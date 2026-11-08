-- Harness scenario GROUPJOIN (4.0.1) on the REAL addon (Group + GroupJoin + Route + Compass + ZonePlan + Dungeon +
-- QuickTravel, strict mock): a level-28 Horde hunter in Thousand Needles, 2 friends with TreeAI (GRP v4) in the Barrens,
-- 1 friend without TreeAI. Real geography: map rectangles solved from the flight masters of Data/Travel.lua (same world
-- frame as UnitPosition), known flight paths, hearthstone. Checks « Rejoindre » (walk / flight / hearthstone / « il
-- arrive » / « se rapproche »), « Rentable » (exact detour math, ≤ 20 %), the flight card following the join, arrival,
-- meeting point + proposal, « Suivre le groupe » (ZonePlan work zone), the vote, the intent inferred from scripted party
-- chat + movements (decay, chat lockdown, privacy), combat (nothing acts, secret values), one « Maintenant » line max for
-- the group, comms (GRP only, ≤ 255 B, PARTY), no chat output, zero blocked actions. Fake names only.
-- lua5.1 tests/wow/harness.lua -q --flavor forever --class HUNTER --scenario tests/wow/groupjoin_scenario.lua wow_addon/TreeAI
return function(M, ctx)
  local T = TreeAI
  local check = ctx.check
  local G = T and T.modules and T.modules.Group
  local J = T and T.GroupJoin
  check(G and J and G.Join == J, "GroupJoin chargé dans le module « Groupe »")
  if not (G and J) then return end
  M.LeaveCombat()
  T:ApplyPreset("tout")
  local intents, gi = {}, {}                -- (4.0.3 integ) GROUP_INTENT : envoyé quand la clé de l'intention change
  T:On("GROUP_INTENT", gi, function(_, _, x) intents[#intents + 1] = x or false end)
  local out0, sent0 = #M.output, #M.sentAddon
  local lv = M.player.level
  local me = T.PlainText(tostring((UnitName("player"))))
  rawset(_G, "UnitFactionGroup", function() return "Horde", "Horde" end)
  local leader = "player"
  rawset(_G, "UnitIsGroupLeader", function(u) return u == leader end)
  -- world: Thousand Needles / The Barrens / Ashenvale (yards, continent 1 = Kalimdor), from Data/Travel flight masters
  M.mapRects[1441] = { top = -3967.4, left = -434.3, w = 4400, h = 2933.33, continent = 1 }
  M.mapRects[1413] = { top = 1611.9, left = 2626.3, w = 10132.6, h = 6755.7, continent = 1 }
  M.mapRects[1440] = { top = 4674.0, left = 1702.4, w = 5766.7, h = 3843.75, continent = 1 }
  M.mapNames[1441], M.mapNames[1413], M.mapNames[1440] = "Mille pointes", "Les Tarides", "Orneval"
  for k in pairs(T.Geo.rects) do T.Geo.rects[k] = nil end   -- cartes lues avant ces rectangles (orage) : relues
  if T.ZonePlan and T.ZonePlan.ResetMaps then T.ZonePlan.ResetMaps() end
  J.ResetIndex()
  local function world(map, x, y) local r = M.mapRects[map] return r.top - y * r.h, r.left - x * r.w end
  local function yd(m1, x1, y1, m2, x2, y2)
    local a1, b1 = world(m1, x1, y1)
    local a2, b2 = world(m2, x2, y2)
    return math.sqrt((a1 - a2) ^ 2 + (b1 - b2) ^ 2)
  end
  M.player.zone = "Mille pointes"
  M.pos = { mapID = 1441, x = 0.455, y = 0.50 }   -- au Poste de Librevent
  if T.Route and T.Route.char then T.Route.char.taxi = { [30] = true, [77] = true } end
  M.quests = {
    { header = "Mille pointes" },
    { id = 7001, title = "Sang-test", level = lv, objectives = { { text = "Sang : 2/6", have = 2, need = 6, type = "item" } } },
    { id = 7003, title = "Rendu-test", level = lv, complete = true, objectives = {}, xp = 1450 },
  }
  M.waypoints[7003] = { 1441, 0.58, 0.60 }        -- remise sur le chemin vers Delta
  M.group = {
    { name = "Bravo", class = "MAGE", level = lv, online = true, guid = "Player-0-B", pos = { mapID = 1413, x = 0.449, y = 0.585 } },
    { name = "Charlie", class = "DRUID", level = lv, online = true, guid = "Player-0-C", pos = { mapID = 1413, x = 0.425, y = 0.890 } },
    { name = "Delta", class = "WARRIOR", level = lv - 1, online = true, powerType = 1, guid = "Player-0-D", pos = { mapID = 1441, x = 0.70, y = 0.70 } },
  }
  local CM = rawget(_G, "C_Map")
  local origBest = CM.GetBestMapForUnit
  CM.GetBestMapForUnit = function(u)   -- the client gives the map of a party member in the open world
    local i = tonumber(tostring(u):match("^party(%d)$"))
    if i and M.group[i] and M.group[i].pos and not M.instance then return M.group[i].pos.mapID end
    return origBest(u)
  end
  local origPos = CM.GetPlayerMapPosition
  CM.GetPlayerMapPosition = function(map, u)   -- … and its position on that map (party pins of the world map)
    local i = tonumber(tostring(u):match("^party(%d)$"))
    local p = i and M.group[i] and M.group[i].pos
    if p and not M.instance then
      if p.mapID ~= map then return nil end
      local mk = rawget(_G, "CreateVector2D")
      return mk and mk(p.x, p.y) or { x = p.x, y = p.y }
    end
    return origPos(map, u)
  end
  ctx.fire("GROUP_ROSTER_UPDATE")
  ctx.fire("QUEST_LOG_UPDATE")
  local seq = 100
  local function grp(from, payload)
    seq = seq + 1
    M.DeliverAddon("TREEAI", "1~GRP~" .. seq .. "~1/1~r" .. payload, "PARTY", from .. "-Royaume")
  end
  grp("Bravo", "4;F;1;x=40;m=1413;ps=449.585;+7001," .. lv .. ",,Sang-test,i5/6")
  grp("Charlie", "4;F;1;x=75;m=1413;ps=425.890;+7001," .. lv .. ",C,Sang-test,i6/6")
  local function lines(tab) local o = {} for _, l in ipairs(T:GetTabLines(tab or "group")) do if tab or l.source == "Group" then o[#o + 1] = l end end return o end
  local function line(pat) for _, l in ipairs(lines()) do if T.PlainText(l.text):find(pat, 1, true) then return l end end end
  local function text() local o = {} for _, l in ipairs(lines()) do o[#o + 1] = T.PlainText(l.text) end return table.concat(o, "\n") end
  local function control(label) for _, l in ipairs(lines()) do if l.control and l.label == label then return l end end end
  local function groupNow()   -- lignes du groupe dans « Maintenant »
    local n, o = 0, {}
    for _, l in ipairs(T:GetTabLines("now")) do
      if l.source == "Group" or l.module == "Group" then n = n + 1; o[#o + 1] = T.PlainText(l.text) end
    end
    return n, table.concat(o, " | ")
  end
  local function settle(sec)
    G._test.reset()
    J._state.cache, J._state.spotCache = {}, nil
    T:SelectTab("group")
    M.Advance(sec or 4, 0.5)
  end
  M.Advance(32, 1)   -- vols connus relus (cache de 30 s)
  settle(4)

  -- ------------------------------------------------------------ 1. rejoindre : vol (Bravo), à pied (Delta)
  local rb = line("Rejoindre Bravo (Les Tarides) : au plus vite")
  check(rb and rb.right == "vol · 3 min" and rb.onClick, "Bravo à Camp Taurajo : vol depuis le Poste de Librevent, 3 min :\n" .. text())
  local tipB = rb and table.concat(rb.tooltip, " | ") or ""
  check(tipB:find("maître de vol : Poste de Librevent", 1, true) and tipB:find("Vol pour Camp Taurajo", 1, true), "étapes en clair : " .. tipB)
  local rd = line("Rejoindre Delta : au plus vite")
  local walkD = yd(1441, 0.455, 0.50, 1441, 0.70, 0.70) * 1.2 / 7
  check(rd and rd.right == ("à pied · " .. math.floor(walkD / 60 + 0.5) .. " min"), "Delta (sans TreeAI) : à pied, sa position vient du jeu : "
    .. tostring(rd and rd.right))
  check(line("Delta · Guerrier " .. (lv - 1) .. " · à 1,1 km"), "carte de Delta : distance vraie")
  local dl = line("Delta · Guerrier")
  check(dl and table.concat(dl.tooltip, " "):find("Inconnus sans TreeAI : son étape", 1, true), "sans TreeAI : ce qui est inconnu est dit")

  -- ------------------------------------------------------------ 2. rentable : le rendu prêt sur le chemin vers Delta (XP du jeu)
  local rr
  for i, l in ipairs(lines()) do if l == rd then rr = lines()[i + 1] end end
  local a1, a2, a3 = yd(1441, 0.455, 0.50, 1441, 0.58, 0.60), yd(1441, 0.58, 0.60, 1441, 0.70, 0.70), yd(1441, 0.455, 0.50, 1441, 0.70, 0.70)
  local extra = (a1 + a2 - a3) * 1.2 / 7 + 15
  check(rr and T.PlainText(rr.text) == ("Rentable : +" .. math.max(5, math.floor(extra / 5 + 0.5) * 5) .. " s · 1 rendu · +1 450 XP"),
    "rentable : temps ajouté exact (détour + 15 s au PNJ) et XP du jeu : " .. tostring(rr and rr.text) .. " (attendu +" .. string.format("%.1f", extra) .. " s)")
  check(extra <= math.max(30, 0.2 * walkD), "rentable : ≤ 20 % du trajet (au moins 30 s)")
  -- clic « Rentable » : la boussole vise d'abord la remise ; la ligne du trajet est LA ligne du groupe dans « Maintenant »
  if rr then M.Hw(rr.onClick) end
  local tg = T.Compass.Current()
  check(tg and tg.kind == "custom" and tostring(tg.label):find("Rendu-test", 1, true) and math.abs(tg.x - 0.58) < 0.001, "rentable : remise d'abord : "
    .. tostring(tg and tg.label))
  M.Advance(2, 0.5)
  local n, nowTxt = groupNow()
  check(n == 1 and nowTxt:find("^Rejoindre Delta"), "« Maintenant » : une seule ligne du groupe, le trajet en cours : " .. nowTxt)
  J.Stop()

  -- ------------------------------------------------------------ 3. au plus vite vers Bravo : vol, carte des vols, atterrissage, arrivée
  M.pos = { mapID = 1441, x = 0.465, y = 0.515 }   -- à ~100 yd du maître de vol
  settle(2)
  rb = line("Rejoindre Bravo (Les Tarides) : au plus vite")
  if rb then M.Hw(rb.onClick) end
  tg = T.Compass.Current()
  check(tg and tg.label == "Maître de vol : Poste de Librevent", "au plus vite : la boussole vise le maître de vol : " .. tostring(tg and tg.label))
  local Q = T.QuickTravel
  local qd = Q and Q.Destination and Q.Destination()
  check(qd and qd.name == "Bravo", "carte des vols (QuickTravel) : le vol qui rapproche de Bravo : " .. tostring(qd and qd.name))
  M.onTaxi = true
  M.pos = { mapID = 1413, x = 0.45, y = 0.70 }
  M.Advance(4, 0.5)
  local jl = T._suggestions and T._suggestions["Group:join"]
  check(jl and jl.text:find(" : en vol · ", 1, true), "en vol : « Rejoindre Bravo (Les Tarides) : en vol · … » (caché en vol par le cœur) : "
    .. tostring(jl and jl.text))
  check(T.Compass.Current() == nil or T.Compass.Current().label ~= "Rejoindre Bravo (Les Tarides)", "en vol : la boussole n'a rien à viser")
  M.onTaxi = false
  M.pos = { mapID = 1413, x = 0.445, y = 0.591 }   -- posé à Camp Taurajo
  M.Advance(4, 0.5)
  tg = T.Compass.Current()
  check(tg and tg.label == "Rejoindre Bravo (Les Tarides)", "posé : la boussole vise Bravo : " .. tostring(tg and tg.label))
  M.pos = { mapID = 1413, x = 0.4492, y = 0.5852 }
  M.Advance(4, 0.5)
  local joined = T._suggestions and T._suggestions["Group:joined"]
  check(joined and joined.text == "Tu as rejoint Bravo" and not J._state.active, "arrivée : « Tu as rejoint Bravo »")

  -- ------------------------------------------------------------ 4. pierre de foyer (liée à Camp Taurajo), « il arrive », « se rapproche »
  rawset(_G, "GetBindLocation", function() return "Camp Taurajo" end)
  M.pos = { mapID = 1441, x = 0.85, y = 0.80 }      -- Les Salines (loin de tout maître de vol)
  M.Advance(16, 1)
  settle(4)
  rb = line("Rejoindre Bravo (Les Tarides) : au plus vite")
  check(rb and rb.right == "pierre de foyer · moins d'1 min", "Bravo près de ton auberge : la pierre de foyer (prête) : " .. tostring(rb and rb.right))
  check(rb and table.concat(rb.tooltip, " "):find("Pierre de foyer vers Camp Taurajo", 1, true), "étape : « Pierre de foyer vers Camp Taurajo »")
  rawset(_G, "GetBindLocation", function() return "Auberge-Test" end)
  M.pos = { mapID = 1441, x = 0.455, y = 0.50 }
  grp("Bravo", "4;D;2;j=" .. me .. ";je=180")
  settle(4)
  local arr = line("Bravo arrive vers toi : ≈ ")
  check(arr and T.PlainText(arr.text):find("— attends-le", 1, true) and line("vient vers toi"), "« Bravo arrive vers toi : ≈ 3 min — attends-le » + carte")
  check(not line("Rejoindre Bravo (Les Tarides) : au plus vite"), "il arrive : pas de « Rejoindre Bravo » en double")
  grp("Bravo", "4;D;3;j=;je=0")
  -- Delta (sans TreeAI) se rapproche : sa position (jeu) diminue depuis 30 s
  for _, p in ipairs({ { 0.66, 0.66 }, { 0.62, 0.63 }, { 0.58, 0.60 }, { 0.55, 0.58 } }) do
    M.group[3].pos = { mapID = 1441, x = p[1], y = p[2] }
    M.Advance(15, 1)
  end
  settle(2)
  local ap = line("Delta se rapproche : ≈ ")
  check(ap and T.PlainText(ap.text):find("— attends-le", 1, true), "Delta se rapproche (positions du jeu) :\n" .. text())
  M.group[3].pos = { mapID = 1441, x = 0.70, y = 0.70 }
  M.Advance(32, 1)

  -- ------------------------------------------------------------ 5. point de rencontre : proposé au groupe (fil GRP, « J'y vais » chez eux)
  settle(4)
  local sp = line("Point de rencontre : maître de vol de ")
  check(sp and sp.onClick and sp.right and sp.right:find("^toi "), "point de rencontre proposé (maître de vol d'une de vos zones) :\n" .. text())
  local before = #M.sentAddon
  if sp then M.Hw(sp.onClick) end
  M.Advance(6, 0.5)
  local sentMeet
  for i = before + 1, #M.sentAddon do
    local msg = tostring(M.sentAddon[i].msg)
    if msg:find("~r4;E;meet;", 1, true) and msg:find(";fm;%d+$") then sentMeet = msg end
  end
  check(sentMeet, "proposition envoyée (GRP E;meet;…;fm;n°) : " .. tostring(sentMeet))
  grp("Bravo", "4;E;go;" .. tostring(J._state.rdv and J._state.rdv.id or 0) .. ";240")
  settle(3)
  local rv = line("Rendez-vous ")
  local rvTip = rv and table.concat(rv.tooltip, " | ") or ""
  check(rv and T.PlainText(rv.text):find(": Bravo (≈ 4 min) y va · 2 sans réponse", 1, true) and rvTip:find("Charlie : pas encore répondu", 1, true)
    and rvTip:find("Delta : sans TreeAI", 1, true), "qui vient : " .. tostring(rv and rv.text) .. " / " .. rvTip)
  J._state.rdv = nil

  -- ------------------------------------------------------------ 6. où va chacun : Bravo (chef) et Charlie en Orneval → « Suivre le groupe »
  leader = "party1"
  grp("Bravo", "4;D;4;sp=1440.500.500;st=obj,7001,1,Tue « Ogre-test »")
  grp("Charlie", "4;D;2;sp=1440.520.480")
  settle(4)
  local cf = line("Bravo et Charlie vont vers « Orneval », toi et Delta vers « Mille pointes »")
  check(cf and cf.onClick and cf.prio == 2, "conflit, le chef tranche :\n" .. text())
  local fol = control("Suivre le groupe")
  check(fol, "bouton « Suivre le groupe »")
  if fol then M.Hw(fol.onClick) end
  local Z = T.ZonePlan
  check(Z and Z.char and Z.char.mode == "zones" and Z.char.zones[1440] and not Z.char.zones[1441], "suivre : zone de travail du trajet = Orneval")
  local fj
  for _, e in ipairs(G._test.news()) do if e.key == "join" then fj = e end end
  local say = T._suggestions and T._suggestions["Group:say"]
  check(J._state.active and J._state.active.who == "Bravo" and fj and fj.text:find("prends le vol pour Camp Taurajo", 1, true),
    "suivre : en route vers le chef (au maître de vol : « prends le vol ») : " .. tostring(fj and fj.text))
  check(say and say.text == "Ton trajet suit le groupe : « Orneval »", "réponse au clic, seule ligne du groupe pendant 6 s : " .. tostring(say and say.text))
  J.Stop()
  Z:SetMode("auto")

  -- ------------------------------------------------------------ 7. vote : zones et donjons à votre niveau ; chacun clique ; résultat
  local vbtn = control("Voter")
  check(vbtn, "bouton « Voter »")
  before = #M.sentAddon
  if vbtn then M.Hw(vbtn.onClick) end
  M.Advance(6, 0.5)
  local vmsg
  for i = before + 1, #M.sentAddon do if tostring(M.sentAddon[i].msg):find("~r4;E;vote;", 1, true) then vmsg = tostring(M.sentAddon[i].msg) end end
  check(vmsg and vmsg:find(";dRFK", 1, true) and vmsg:find(";z1440", 1, true), "vote envoyé (Orneval, Kraal…) : " .. tostring(vmsg))
  local vote = J._state.vote
  local rfk
  for i, c in ipairs(vote and vote.choices or {}) do if c.v == "RFK" then rfk = i end end
  grp("Bravo", "4;E;ballot;" .. tostring(vote and vote.id) .. ";" .. tostring(rfk))
  grp("Charlie", "4;E;ballot;" .. tostring(vote and vote.id) .. ";" .. tostring(rfk))
  settle(2)
  local vl = line("Voter : Kraal de Tranchebauge (2 voix)")
  check(vl and vl.onClick, "ligne de vote cliquable :\n" .. text())
  if vl then M.Hw(vl.onClick) end
  settle(2)
  local vr = line("Vote : Kraal de Tranchebauge gagne")
  check(vote and vote.closed and vote.result == rfk and vr and vr.onClick, "vote clos : le Kraal gagne (3 voix) — Delta sans TreeAI ne vote pas")
  if vr then M.Hw(vr.onClick) end
  local Dg = T.Dungeon
  check(Dg and Dg.char and Dg.char.planned == "RFK", "suivre le vote : donjon prévu = Kraal de Tranchebauge")
  check(J._state.active and J._state.active.who == "@group", "suivre le vote : la boussole va vers l'entrée")
  J.Stop()
  J._state.vote = nil
  if Dg and Dg.SetPlanned then Dg.SetPlanned(nil) end

  -- ------------------------------------------------------------ 8. intention déduite : chat de groupe + déplacements
  grp("Bravo", "4;D;5;sp=1413.430.880")
  grp("Charlie", "4;D;3;sp=1413.425.890")
  M.FireEvent("CHAT_MSG_PARTY_LEADER", "on va au kraal, rdv à l'entrée", "Bravo-Royaume")
  M.FireEvent("CHAT_MSG_PARTY", "ok je suis devant rfk", "Charlie-Royaume")
  M.group[2].pos = { mapID = 1413, x = 0.424, y = 0.898 }   -- Charlie à l'entrée
  for _, p in ipairs({ { 0.445, 0.70 }, { 0.44, 0.78 }, { 0.432, 0.85 }, { 0.426, 0.89 } }) do   -- Bravo y descend
    M.group[1].pos = { mapID = 1413, x = p[1], y = p[2] }
    M.Advance(15, 1)
  end
  settle(3)
  local it = line("Le groupe va vers : Kraal de Tranchebauge (2/4 à l'entrée)")
  local tip = it and table.concat(it.tooltip, " | ") or ""
  check(it and tip:find("confiance forte", 1, true) and tip:find("Chat de Bravo", 1, true) and tip:find("Chat de Charlie", 1, true)
    and tip:find("sur place", 1, true), "intention : « Le groupe va vers : Kraal de Tranchebauge (2/4 à l'entrée) » :\n" .. text() .. "\n" .. tip)
  n, nowTxt = groupNow()
  check(n == 1 and nowTxt:find("Kraal de Tranchebauge", 1, true), "« Maintenant » : la ligne du groupe = l'intention (une seule) : " .. nowTxt)
  -- (4.0.3 integ) contrat GROUPJOIN → pilote auto : Group.Intent() au format du pilote + GROUP_INTENT une fois par clé
  local bi = G.Intent and G.Intent(G)
  check(type(bi) == "table" and bi.kind == "dungeon" and bi.dungeon == "RFK" and type(bi.point) == "table"
    and bi.point.map == 1413 and bi.point.x > 1 and bi.point.x <= 100 and bi.map == 1413 and (bi.conf or 0) >= 0.6
    and type(bi.by) == "string" and bi.src and tonumber(bi.at) and tostring(bi.label):find("Kraal", 1, true),
    "Group.Intent() : { kind = dungeon, dungeon = RFK, point (0–100), map, conf, by, src, at, label } : "
    .. (type(bi) == "table" and (tostring(bi.kind) .. "/" .. tostring(bi.dungeon) .. "/" .. tostring(bi.src) .. "/" .. tostring(bi.conf)) or tostring(bi)))
  local nI, lastI = #intents, intents[#intents]
  check(nI >= 1 and type(lastI) == "table" and lastI.kind == "dungeon", "GROUP_INTENT envoyé avec l'intention (" .. nI .. ")")
  local bi2 = G.Intent(G)
  settle(3)
  check(#intents == nI and bi2 and bi2.at == bi.at, "même intention : pas de nouvel envoi, « at » stable")
  local jr = line("Rejoindre le groupe (Kraal de Tranchebauge) : au plus vite")
  check(jr and jr.onClick, "« Rejoindre » vise maintenant l'entrée du Kraal")
  fol = control("Suivre le groupe")
  if fol then M.Hw(fol.onClick) end
  check(Dg and Dg.char and Dg.char.planned == "RFK" and J._state.active and J._state.active.who == "@group", "suivre : donjon prévu + boussole vers l'entrée")
  J.Stop()
  if Dg and Dg.SetPlanned then Dg.SetPlanned(nil) end
  -- vie privée : jamais le texte du chat dans les données sauvées
  local sv = T:Serialize(TreeAIDB)
  check(not sv:find("on va au kraal", 1, true) and not sv:find("devant rfk", 1, true), "vie privée : aucun texte du chat dans TreeAIDB")
  -- verrou du chat (rencontre de boss) : texte secret → ignoré, sans erreur
  M.encounter = true
  M.FireEvent("CHAT_MSG_PARTY", "on va à Orneval", "Charlie-Royaume")
  M.encounter = false
  local nChat = #J._state.chat
  if M.flavor == "forever" then   -- (les autres saveurs du faux client n'ont pas de valeurs secrètes)
    check(nChat == 2, "verrou du chat : message secret ignoré (" .. nChat .. " indices)")
  end
  -- déclin : 30 min plus tard, sans nouveau mot ni regroupement
  M.group[1].pos, M.group[2].pos = { mapID = 1413, x = 0.449, y = 0.585 }, { mapID = 1413, x = 0.425, y = 0.80 }
  grp("Bravo", "4;D;6;sp=")
  grp("Charlie", "4;D;4;sp=")
  M.Advance(1800, 10)
  settle(3)
  check(not line("Kraal de Tranchebauge (2/4"), "30 min plus tard : l'intention s'est éteinte :\n" .. text())
  check(#intents > nI and intents[#intents] ~= intents[nI], "intention changée / éteinte : GROUP_INTENT renvoyé")
  T:Off("GROUP_INTENT", gi)

  -- ------------------------------------------------------------ 9. combat : aucun clic n'agit, aucune lecture, valeurs secrètes sans erreur
  settle(3)
  local row = line("Rejoindre Bravo")
  M.EnterCombat()
  local tgBefore = T.Compass.Current()
  if row then M.Hw(row.onClick) end
  local say = T._suggestions and T._suggestions["Group:say"]
  check(say and say.text == "Hors combat seulement" and T.Compass.Current() == tgBefore, "en combat : « Hors combat seulement », boussole inchangée")
  for _ = 1, 8 do
    ctx.fire("UNIT_HEALTH", "party1"); ctx.fire("UNIT_POWER_UPDATE", "party2")
    M.FireEvent("CHAT_MSG_PARTY", "go rfk", "Bravo-Royaume")
  end
  M.Advance(6, 0.5)
  M.LeaveCombat()
  M.Advance(3, 0.5)

  -- ------------------------------------------------------------ 10. calme, fil, chat, actions bloquées
  n, nowTxt = groupNow()
  check(n <= 1, "jamais plus d'une ligne du groupe dans « Maintenant » : " .. nowTxt)
  local grpMsgs, byMin = 0, {}
  for i = sent0 + 1, #M.sentAddon do
    local s = M.sentAddon[i]
    local msg = tostring(s.msg)
    check(#msg <= 255 and (s.channel == "PARTY" or msg:find("~SELFTEST~", 1, true)), "message ≤ 255 B en PARTY : " .. msg:sub(1, 60))
    check(not msg:match("^1~(%u+)~") or msg:match("^1~(%u+)~") == "GRP" or msg:match("^1~(%u+)~") == "HELLO"
      or msg:match("^1~(%u+)~") == "PLN" or msg:find("~SELFTEST~", 1, true)
      or (msg:match("^1~(GG[EBQ])~") ~= nil), "un seul type de message de groupe (+ équipement GGE / GGB / GGQ) : " .. msg:sub(1, 30))
    if msg:find("^1~GRP~") then grpMsgs = grpMsgs + 1 end
  end
  check(grpMsgs > 0, "le groupe parle par GRP (" .. grpMsgs .. " messages)")
  check(#M.output == out0, "rien dans le chat (" .. (#M.output - out0) .. " lignes)")
  check(#(M.blocked or {}) == 0, "aucune action bloquée")
  M.group = {}
  ctx.fire("GROUP_ROSTER_UPDATE")
  M.Advance(3, 0.5)
  check(#lines() == 0 and not J._state.active, "groupe quitté : plus rien")
  rawset(_G, "UnitIsGroupLeader", function(u) return u == "player" end)
  CM.GetBestMapForUnit, CM.GetPlayerMapPosition = origBest, origPos
  T:ApplyPreset("simple")
end
