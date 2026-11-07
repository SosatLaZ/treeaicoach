-- Harness scenario: the real addon (Core + Squad + Missions) in a party with one TreeAI friend and one
-- friend without the addon. Missions messages stay ≤ 255 B in PARTY, friend data is accepted, an
-- outsider's is not, commands work, summary.missions is written. Fake names only.
-- lua5.1 tests/wow/harness.lua --scenario tests/wow/missions_scenario.lua wow_addon/TreeAI
return function(M, ctx)
  local Mi = TreeAI.Missions
  ctx.check(Mi and Mi:IsEnabled(), "Missions actif")
  M.group = { { name = "Amie", class = "PRIEST", level = 25, online = true },
              { name = "Sansaddon", class = "ROGUE", level = 24, online = true } }
  M.FireEvent("GROUP_ROSTER_UPDATE")
  local st = TreeAI:Serialize({ v = 2, c = "PRIEST", l = 25, z = "Elwynn", h = 90, p = 80, b = 4, bt = 64, pr = "Couture 90" })
  for _ = 1, 3 do
    M.DeliverAddon("TREEAI", "1~SQS~1~1/1~t" .. st, "PARTY", "Amie-Royaume")
    M.DeliverAddon("TREEAI", "1~SQQ~2~1/1~r2:1001,25,,Les ours,m3/8", "PARTY", "Amie-Royaume")
    M.DeliverAddon("TREEAI", "1~MSM~3~1/1~r1:1001,40,1429,500,500,Ours tué", "PARTY", "Amie-Royaume")
    M.Advance(20, 0.5)
  end
  M.DeliverAddon("TREEAI", "1~MSC~4~1/1~r1:1001.0", "PARTY", "Amie-Royaume")
  M.DeliverAddon("TREEAI", "1~MSC~5~1/1~r1:1001.0", "PARTY", "Inconnu-Royaume")
  M.Advance(3, 0.5)
  ctx.check(Mi._claims.Amie and Mi._claims.Amie[1] and Mi._claims.Amie[1].q == 1001, "prise de l'amie reçue")
  ctx.check(Mi._claims.Inconnu == nil, "hors groupe ignoré")
  ctx.check(Mi._info.Amie and Mi._info.Amie[1001] and Mi._info.Amie[1001].names[1] == "Ours tué", "infos de quête de l'amie")
  ctx.check(type(Mi.board) == "table" and #Mi.board >= 1, "tableau des missions")
  ctx.check(Mi.noAddon and Mi.noAddon[1] == "Sansaddon", "membre sans TreeAI détecté")
  local kinds = {}
  for _, s in ipairs(M.sentAddon) do
    local k = tostring(s.msg):match("^1~(MS%u)~")
    if k then kinds[k] = true end
    ctx.check(#s.msg <= 255 and (s.channel == "PARTY" or s.channel == "RAID"), "message <= 255 B en PARTY/RAID")
  end
  ctx.check(kinds.MSM, "MSM envoyé")
  for _, line in ipairs({ "/tai mission", "/tai mission objectif niveau 30", "/tai prends ours", "/tai mission cible",
    "/tai mission pret", "/tai mission ou", "/tai mission xp", "/tai mission voyage", "/tai mission metiers",
    "/tai mission butin", "/tai mission marque", "/tai mission recap" }) do
    M.RunSlash(line)
  end
  M.FireEvent("CHAT_MSG_LOOT", "Amie reçoit le butin : |cff0070dd|Hitem:1:0|h[Bâton]|h|r.")
  M.FireEvent("QUEST_COMPLETE")
  M.Advance(10, 0.5)
  ctx.check(Mi.char.session and Mi.char.session.goal and Mi.char.session.goal.n == 30, "objectif de session")
  TreeAI:Fire("SUMMARY")
  local s = TreeAI:Summary()
  ctx.check(s.missions and s.missions.v == 1 and s.missions.session, "summary.missions écrit")
end
