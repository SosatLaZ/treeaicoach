-- Harness scenario: the real addon (Core + Squad) in a party sends its v2 state/quests, ≤ 255 B per
-- message, and a friend's v2 messages are accepted while an outsider's are not. Fake names only.
-- lua5.1 tests/wow/harness.lua --scenario tests/wow/squad_scenario.lua wow_addon/TreeAI
return function(M, ctx)
  local S = TreeAI.Squad
  ctx.check(S and S:IsActive(), "Squad actif")
  M.group = { { name = "Amie", class = "PRIEST", level = 25, online = true } }
  M.FireEvent("GROUP_ROSTER_UPDATE")
  ctx.check(pcall(S.Tick, S), "Squad:Tick sans erreur")
  M.Advance(20, 0.5)
  local kinds = {}
  for _, s in ipairs(M.sentAddon) do
    local k = tostring(s.msg):match("^1~(SQ%u)~")
    if k then kinds[k] = (kinds[k] or 0) + 1 end
    ctx.check(#s.msg <= 255 and (s.channel == "PARTY" or s.channel == "RAID"), "message <= 255 B en PARTY/RAID")
  end
  ctx.check(kinds.SQS and kinds.SQQ and kinds.SQR, "SQS/SQQ/SQR envoyés")
  local friend = TreeAI:Serialize({ v = 2, c = "PRIEST", l = 25, z = "Elwynn", h = 90, p = 15, b = 0, bt = 64 })
  M.DeliverAddon("TREEAI", "1~SQS~1~1/1~t" .. friend, "PARTY", "Amie-Royaume")
  M.DeliverAddon("TREEAI", "1~SQS~1~1/1~t" .. friend, "PARTY", "Inconnu-Royaume")
  M.Advance(4, 0.5)
  ctx.check(S:State("Amie") and S:State("Amie").l == 25, "état de l'amie reçu")
  ctx.check(S:State("Inconnu") == nil, "hors groupe ignoré")
  ctx.check(#S:Members() == 2, "2 membres")
  M.RunSlash("/tai groupe")
  M.RunSlash("/tai partir 15")
  M.RunSlash("/tai pret off")
  TreeAI:Fire("SUMMARY")
  local s = TreeAI:Summary()
  ctx.check(s.group and s.group.v == 2 and #s.group.members == 2, "summary.group écrit")
end
