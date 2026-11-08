-- Harness scenario for Modules/Group.lua (« Groupe », GROUPMERGE 4.0) on the REAL addon (Core comms, UI tabs, every
-- module loaded, strict mock): 2 friends with TreeAI (protocol v4 « GRP »), 1 friend still on 3.9 (Squad v2 decoded),
-- 1 member without TreeAI (Blizzard APIs only: IsUnitOnQuest, UnitInRange, inspect, auras) and 1 offline member.
-- Cards, pull line pinned in « Maintenant », buffs / cleanse, gear flags, per-quest share lines, goal picker by mouse,
-- « Prêt ? », meeting point, facades kept for the other modules, summaries, module off / on, combat with secret
-- values, comms (one message type, ≤ 255 B, PARTY only). Fake names only.
-- lua5.1 tests/wow/harness.lua -q --flavor forever --class HUNTER --scenario tests/wow/group_scenario.lua wow_addon/TreeAI
return function(M, ctx)
  local T = TreeAI
  local check = ctx.check
  local G = T and T.modules and T.modules.Group
  check(G and G.db and G.db.enabled and G.title == "Groupe", "module « Groupe » enregistré et allumé")
  if not G then return end
  check(T.Squad and T.modules.Squad == T.Squad and T.Missions and T.modules.Missions == T.Missions and T.modules.Team == T.Team,
    "façades : Squad / Missions / Team (anciens noms gardés)")
  local moduleNames = {}
  for _, n in ipairs(T.moduleOrder) do moduleNames[n] = true end
  check(not moduleNames.Squad and not moduleNames.Missions and not moduleNames.Team, "un seul module dans « Plus → Modules »")
  local cls = T:PlayerClass()
  local lv = M.player.level
  M.LeaveCombat()
  T:ApplyPreset("tout")

  local function lines(src)
    local out = {}
    for _, l in ipairs(T:GetTabLines("group")) do if not src or l.source == src then out[#out + 1] = l end end
    return out
  end
  local function text()
    local out = {}
    for _, l in ipairs(lines("Group")) do out[#out + 1] = T.PlainText(l.text) end
    return table.concat(out, "\n")
  end
  local function has(pat) return text():find(pat, 1, true) ~= nil end
  local function line(pat) for _, l in ipairs(lines("Group")) do if T.PlainText(l.text):find(pat, 1, true) then return l end end end
  local function settle(sec)
    G._test.reset()
    T:SelectTab("group")
    M.Advance(sec or 3, 0.5)
  end
  local seq = 100
  local function grp(from, payload)
    seq = seq + 1
    M.DeliverAddon("TREEAI", "1~GRP~" .. seq .. "~1/1~r" .. payload, "PARTY", from .. "-Royaume")
  end

  -- solo : rien
  M.group = {}
  ctx.fire("GROUP_ROSTER_UPDATE")
  M.Advance(3, 0.5)
  check(#lines("Group") == 0, "seul : aucune ligne")

  -- le groupe
  M.AddItem(99101, { name = "Hache-test rouillée", quality = 0, ilvl = 5, subType = "Haches à une main", equipLoc = "INVTYPE_WEAPON" })
  M.AddItem(99102, { name = "Casque-test", quality = 2, ilvl = 22, equipLoc = "INVTYPE_HEAD" })
  M.quests = {
    { header = "Marais-test" },
    { id = 7001, title = "Sang-test de lion", level = lv, objectives = { { text = "Sang de lion : 2/6", have = 2, need = 6, type = "item" } } },
    { id = 7002, title = "Ours-test", level = lv, objectives = { { text = "Ours tué : 1/8", have = 1, need = 8 } } },
    { id = 7003, title = "Quête-test finie", level = lv - 1, complete = true, objectives = {} },
  }
  local gear = {}
  for _, s in ipairs({ 1, 2, 3, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 17, 18 }) do gear[s] = 99102 end
  gear[16] = 99101   -- arme grise, pas de torse (5)
  M.group = {
    { name = "Bravo", class = "MAGE", level = lv, online = true, power = 25, powerMax = 100, guid = "Player-0-B" },
    { name = "Charlie", class = "DRUID", level = lv, online = true, guid = "Player-0-C" },
    { name = "Delta", class = "WARRIOR", level = lv - 2, online = true, powerType = 1, inRange = false, gear = gear,
      quests = { [7002] = true }, guid = "Player-0-D" },
    { name = "Echo", class = "PRIEST", level = lv, online = false, guid = "Player-0-E" },
  }
  M.auras.party1, M.auras.party2, M.auras.party3 = { { name = "Venin-test", kind = "debuff", debuffType = "Poison", spellId = 99001 } }, {}, {}
  ctx.fire("GROUP_ROSTER_UPDATE")
  ctx.fire("QUEST_LOG_UPDATE")
  -- Bravo et Charlie ont TreeAI 4.0 (protocole v4) ; Charlie a une quête que je n'ai pas
  grp("Bravo", "4;F;1;x=40;r=10;xs=8000;du=8;b=0;bt=64;st=obj,7001,1,Ramasse « Sang de lion »;+7001," .. lv .. ",,Sang-test de lion,i5/6;+7002," .. lv .. ",,Ours-test,m8/8")
  grp("Charlie", "4;F;1;x=75;xs=12000;+7001," .. lv .. ",C,Sang-test de lion,i6/6;+7005," .. lv .. ",,Murloc-test,m0/5")
  settle(4)
  check(has("Bravo · Mage " .. lv .. " (40 %) · mana 25 %"), "carte de Bravo (TreeAI) :\n" .. text())
  check(has("Bravo : Ramasse « Sang de lion » (5/6)"), "étape en cours de Bravo avec sa progression")
  check(has("Bravo : durabilité 8 % · sacs pleins"), "signaux d'alerte de Bravo (TreeAI)")
  check(has("Delta · Guerrier " .. (lv - 2) .. " · loin"), "carte de Delta (sans TreeAI, loin, rage → pas de mana)")
  check(has("Echo · Prêtre " .. lv .. " · hors ligne"), "carte d'Echo hors ligne")
  check(has("Delta est 2 niveaux derrière : partage-lui « Sang-test de lion »"), "écart de niveau + quête à lui partager")
  check(has("Ensemble : « Sang-test de lion » — Bravo 5/6, Charlie fini, toi 2/6"), "prochain arrêt commun : « toi » pour moi")
  check(has("Demande « Murloc-test » à Charlie"), "quête de Charlie que je n'ai pas")
  check(not has("Quête-test finie"), "quête finie : jamais proposée")
  check(has("XP de la soirée : Charlie +12 000 · Bravo +8 000"), "course à l'XP (amis TreeAI) :\n" .. text())
  local nx = line("Ensemble : « Sang-test de lion »")
  check(nx and nx.onClick and nx.questID == 7001, "prochain arrêt commun (cliquable, boussole)")
  check(not has("nil") and not has("?"), "aucun « nil » ni « ? »")

  -- inspection discrète (Delta : à portée d'inspection, hors combat, 1 / 10 s)
  M.Advance(36, 1)
  settle(2)
  check(has("Delta : arme grise · rien en torse"), "inspection : arme grise, emplacement vide :\n" .. text())
  check(T.Team.GearOf("Player-0-D") and T.Team.GearOf("Player-0-D").links[16], "Team.GearOf (Dungeon) : liens de l'équipement inspecté")
  local n = M.inspects or 0
  M.Advance(30, 1)
  check((M.inspects or 0) - n <= 4, "inspection ≤ 1 / 10 s (" .. ((M.inspects or 0) - n) .. ")")

  -- soutien : auras relues toutes les 15 s hors combat (le cœur ne relaie pas UNIT_AURA du groupe)
  M.Advance(16, 1)
  settle(2)
  local bl = line("Bravo : ")
  local sup
  for _, l in ipairs(lines("Group")) do
    local p = T.PlainText(l.text)
    if p:find("^Bravo : ") and (p:find("manque", 1, true) or p:find("à retirer", 1, true)) then sup = p end
  end
  check(bl ~= nil and sup and sup:find("poison à retirer (Charlie)", 1, true), "poison sur Bravo : sur sa carte, qui peut le retirer :\n" .. text())
  if cls ~= "DRUID" then
    check(sup and sup:find("manque Marque du fauve (Charlie)", 1, true), "buff qu'un autre membre peut donner (carte de Bravo) :\n" .. text())
  end
  check(not has("Delta : manque"), "membre loin (hors de portée) : auras pas lues, rien inventé")
  if cls == "PRIEST" then
    M.spells[1243] = { name = "Mot de pouvoir : Robustesse", known = true, icon = 1 }
    ctx.fire("SPELLS_CHANGED")
    M.Advance(16, 1)
    settle(2)
    local l = line("Lance Mot de pouvoir : Robustesse sur")
    check(l and l.pin and l.prio == 2, "prêtre : « Lance Robustesse sur … » épinglé :\n" .. text())
  end

  -- « Avant de tirer, attends » : lu par le jeu, épinglé (« Maintenant »)
  M.target = { exists = true, name = "Ours-test", level = lv, hostile = true, guid = "Creature-0-0-0-0-7-1" }
  ctx.fire("PLAYER_TARGET_CHANGED")
  M.Advance(1, 0.25)
  local pl = line("Avant de tirer, attends : ")
  check(pl and pl.pin and T.PlainText(pl.text):find("Bravo 25 % mana", 1, true) and T.PlainText(pl.text):find("Delta loin", 1, true)
    and T.PlainText(pl.text):find("Echo hors ligne", 1, true), "ligne « avant de tirer » : " .. tostring(pl and pl.text))
  local inNow = false
  for _, l in ipairs(T:GetTabLines("now")) do if T.PlainText(l.text):find("Avant de tirer", 1, true) then inNow = true end end
  check(inNow, "elle concourt pour « Maintenant »")
  M.EnterCombat()
  for _ = 1, 10 do ctx.fire("UNIT_HEALTH", "party1"); ctx.fire("UNIT_POWER_UPDATE", "party1"); ctx.fire("UNIT_AURA", "party2") end
  M.Advance(3, 0.5)
  check(not line("Avant de tirer"), "en combat : la ligne disparaît (valeurs secrètes jamais lues)")
  M.LeaveCombat()
  M.target = { exists = false }
  ctx.fire("PLAYER_TARGET_CHANGED")
  M.Advance(2, 0.5)

  -- objectif du groupe à la souris : bouton « Objectif » → choix en lignes → clic
  local before = #M.sentAddon
  local ctrl
  for _, l in ipairs(lines("Group")) do if l.control and l.label == "Objectif" then ctrl = l end end
  check(ctrl and ctrl.onClick, "bouton « Objectif » dans la barre de l'onglet")
  if ctrl then M.Hw(ctrl.onClick) end
  local pick = line("Niveau " .. (lv + 1) .. " ensemble")
  check(pick and pick.onClick and line("Finir « Marais-test » (2 quêtes)") and line("Pas d'objectif"), "choix proposés :\n" .. text())
  if pick then M.Hw(pick.onClick) end
  M.Advance(2, 0.5)
  check(G.char.goal and G.char.goal.k == "level" and G.char.goal.n == lv + 1, "objectif posé")
  local sentGoal = false
  for i = before + 1, #M.sentAddon do if tostring(M.sentAddon[i].msg):find("~r4;E;goal;level;" .. (lv + 1), 1, true) then sentGoal = true end end
  check(sentGoal, "objectif partagé (GRP E;goal)")
  settle(2)
  check(has("Objectif niveau " .. (lv + 1) .. " ensemble : 0/4"), "ligne d'objectif :\n" .. text())
  grp("Charlie", "4;E;goal;zone;Marais-test")
  settle(3)
  check(G.char.goal and G.char.goal.k == "zone" and G.char.goal.by == "Charlie", "objectif de Charlie reçu")
  check(has("Objectif : finir « Marais-test » — 2 quêtes restantes (toi 2)"), "objectif de zone : mes quêtes comptées :\n" .. text())
  M.RunSlash("/tai objectif off")
  check(not G.char.goal, "/tai objectif off")

  -- « Prêt ? » : chef = l'appel du jeu ; sinon la vérification TreeAI
  local calls = {}
  local realPI = rawget(_G, "C_PartyInfo")
  rawset(_G, "C_PartyInfo", { DoReadyCheck = function() calls[#calls + 1] = "ready" end })
  local rdy
  for _, l in ipairs(lines("Group")) do if l.control and l.label == "Prêt ?" then rdy = l end end
  check(rdy and rdy.onClick, "bouton « Prêt ? »")
  if rdy then M.Hw(rdy.onClick) end
  check(calls[1] == "ready", "chef : l'appel « prêt » du jeu (tout le monde le voit)")
  rawset(_G, "C_PartyInfo", realPI)
  grp("Bravo", "4;E;ask;3")
  M.Advance(1, 0.5)
  local ask = T._suggestions and T._suggestions["Group:ready"]
  check(ask and ask.text:find("Bravo demande : prêt ?", 1, true) and ask.onClick, "« prêt ? » de Bravo reçu (clic = oui)")
  if ask then M.Hw(ask.onClick) end
  M.Advance(6, 0.5)
  local answered = false
  for _, s in ipairs(M.sentAddon) do if tostring(s.msg):find("~r4;E;ans;3;1", 1, true) then answered = true end end
  check(answered, "réponse envoyée (GRP E;ans)")

  -- point de rencontre : envoyé par le protocole unique ; reçu → ligne + boussole
  M.pos = M.pos or { mapID = 1, x = 0.5, y = 0.5 }
  local okM = G:SendMeet()
  check(okM, "point de rencontre envoyé")
  grp("Charlie", "4;E;meet;1;420;615;Moulin-test")
  M.Advance(1, 0.5)
  local mt = T._suggestions and T._suggestions["Group:meet"]
  check(mt and mt.text:find("Rejoins Charlie au point de rencontre (Moulin-test)", 1, true) and mt.onClick, "rendez-vous reçu : " .. tostring(mt and mt.text))

  -- ami resté en 3.9 : Squad v2 décodé (une version) ; intrus ignoré
  M.group[4].online = true
  ctx.fire("UNIT_CONNECTION", "party4")
  local st = T:Serialize({ v = 2, c = "PRIEST", l = lv, z = "Marais-test", du = 70, b = 5, bt = 64, x = 33 })
  M.DeliverAddon("TREEAI", "1~SQS~1~1/1~t" .. st, "PARTY", "Echo-Royaume")
  M.DeliverAddon("TREEAI", "1~SQQ~2~1/1~r2:7001," .. lv .. ",,Sang-test de lion,i1/6", "PARTY", "Echo-Royaume")
  M.DeliverAddon("TREEAI", "1~SQS~3~1/1~t" .. st, "PARTY", "Intrus-Royaume")
  M.DeliverAddon("TREEAI", "1~GRP~4~1/1~r4;F;1;x=99", "PARTY", "Intrus-Royaume")
  M.Advance(2, 0.5)
  check(T.Squad:State("Echo") and T.Squad:State("Echo").du == 70 and T.Squad:Quests("Echo")[7001], "ami en 3.9 : état + journal décodés")
  check(T.Squad:State("Intrus") == nil and not G._test.members.Intrus, "hors groupe : ignoré")
  settle(2)
  local el = line("Echo · Prêtre")
  check(el and table.concat(type(el.tooltip) == "table" and el.tooltip or { tostring(el.tooltip) }, " "):find("3.9", 1, true),
    "carte d'Echo : « TreeAI 3.9 : demande-lui de mettre à jour »")

  -- façades lues par les autres modules (Dungeon, QuickTravel, Planner, Export, Todo, Tracker, Prof…)
  local ml = T.Squad:MemberList()
  check(#ml == 4 and T.Squad:IsActive() and #T.Squad:Members() == 4, "Squad:MemberList / Members (moi + amis TreeAI)")
  check(T.Squad:State("Bravo").du == 8 and T.Squad:State("Bravo").c == "MAGE" and T.Squad:State("Bravo").h, "Squad:State (+ état lu par le jeu)")
  check(type(T.Missions.board) == "table" and #T.Missions.board >= 2 and T.Missions.Engine.StatusLine(T.Missions.board[1]), "Missions.board / StatusLine (Todo)")
  local rows = T.QuickTravel and T.QuickTravel.MemberRows and T.QuickTravel.MemberRows()
  check(not rows or #rows == 5, "fenêtre « Tout le monde prêt ? » : une ligne par membre")

  -- messages : un seul type de groupe, ≤ 255 octets, PARTY
  local kinds = {}
  for _, s in ipairs(M.sentAddon) do
    local k = tostring(s.msg):match("^1~(%u+)~")
    if k then kinds[k] = true end
    check(#s.msg <= 255 and (s.channel == "PARTY" or s.channel == "RAID" or tostring(s.msg):find("~SELFTEST~", 1, true)),
      "message ≤ 255 B en PARTY (sauf l'autotest, chuchoté à soi-même)")
  end
  check(kinds.GRP, "le protocole v4 part (GRP)")
  for _, old in ipairs({ "SQS", "SQQ", "SQD", "SQR", "MSM", "MSC", "MST", "MSG", "MSR", "FSTAT", "FQUEST", "FREQ", "QTM" }) do
    check(not kinds[old], "plus d'ancien message de groupe envoyé : " .. old)
  end

  -- commandes (réponses courtes), résumés pour TreeAI Coach
  M.RunSlash("/tai groupe")
  check(T:GetActiveTab() == "group", "/tai groupe ouvre l'onglet Groupe")
  M.RunSlash("/tai partir 15")
  M.RunSlash("/tai pret off")
  check(G.char.ready == false and G.char.leaveAt > 0, "/tai pret off, /tai partir 15")
  M.RunSlash("/tai pret on")
  M.RunSlash("/tai partir 0")
  T:Fire("SUMMARY")
  local s = T:Summary()
  check(s.group and s.group.v == 2 and #s.group.members == 5, "summary.group v2")
  check(s.missions and s.missions.v == 1 and s.missions.board, "summary.missions v1")
  check(s.team and s.team.v == 1 and #s.team.members == 4, "summary.team v1 (sans noms)")

  -- module coupé : lignes parties, façades éteintes, « oubliez-moi » envoyé ; rallumé
  before = #M.sentAddon
  T:SetModuleEnabled("Group", false)
  M.Advance(1, 0.5)
  check(#lines("Group") == 0 and not T.Squad:IsEnabled(), "module coupé : plus de lignes, façades éteintes")
  local bye = false
  for i = before + 1, #M.sentAddon do if tostring(M.sentAddon[i].msg):find("~r4;B", 1, true) then bye = true end end
  check(bye, "« oubliez mes données » envoyé (GRP B)")
  T:SetModuleEnabled("Group", true)
  M.Advance(4, 0.5)
  M.group = {}
  ctx.fire("GROUP_ROSTER_UPDATE")
  M.Advance(3, 0.5)
  check(#lines("Group") == 0, "groupe quitté : plus de lignes")
  check(#(M.blocked or {}) == 0, "aucune action bloquée")
  T:ApplyPreset("simple")
end
