-- Scenario DUNGEON2 : fenêtre « Prêt ? », quêtes du donjon (QuestieDB), qui fait quoi, butin du groupe,
-- objet de quête sur un boss, bilan à la sortie, minuterie coupée hors instance. Noms factices uniquement.
-- Lancer avec --allow LibQuestieDB --allow QuestDB --allow NpcDB --allow ObjectDB --allow ItemDB.
return function(M, ctx)
  local T = TreeAI
  local D = T.modules.Dungeon
  ctx.check(D and D.ToggleChecklist and D.PrepRows, "Dungeon + DungeonPrep chargés")
  if not D then return end
  local S = T._suggestions
  local function text(lines)
    local t = {}
    for _, l in ipairs(lines or {}) do t[#t + 1] = l.text end
    return table.concat(t, "\n")
  end
  M.LeaveCombat()
  M.instance = false
  M.player.level = 28
  M.pet.happiness = 3
  M.inventory[0] = { id = 2515, count = 1800 }
  for _, s in pairs(M.inventory) do if s.durMax then s.dur = s.durMax end end

  -- groupe : guerrier (a la quête), mage (ne l'a pas), prêtre (journal inconnu)
  M.group = {
    { name = "Ami-un", class = "WARRIOR", level = 27, online = true, quests = { [1001] = true } },
    { name = "Ami-deux", class = "MAGE", level = 26, online = true, quests = {} },
    { name = "Ami-trois", class = "PRIEST", level = 28, online = true },
  }
  M.quests = {
    { header = "Profondeurs de Brassenoire" },
    { id = 1001, title = "Le cœur de la naga", level = 24, complete = false,
      objectives = { { text = "Cœur de Sarevess : 0/1", done = false } } },
    { header = "Les Carmines" },
    { id = 1100, title = "Une quête ailleurs", level = 27, complete = false, objectives = {} },
  }
  M.InstallQuestieDB({
    quests = {
      [1001] = { name = "Le cœur de la naga", zoneOrSort = 719, questLevel = 24, requiredLevel = 20,
        objectives = { nil, nil, { { 5001 } } } },
      [1002] = { name = "Dans les profondeurs", zoneOrSort = 719, questLevel = 24, requiredLevel = 18, startedBy = { { 9001 } } },
      [1003] = { name = "Trop dure", zoneOrSort = 719, questLevel = 40, requiredLevel = 38 },
      [1100] = { name = "Une quête ailleurs", zoneOrSort = 44, questLevel = 27, requiredLevel = 22 },
    },
    npcs = { [9001] = { name = "Garde-test d'Astranaar", zoneID = 331 }, [9100] = { name = "Dame Sarevess", zoneID = 719 } },
    items = { [5001] = { name = "Cœur de Sarevess", npcDrops = { 9100 } } },
  })
  local pushed = {}
  rawset(_G, "QuestLogPushQuest", function(i) pushed[#pushed + 1] = i end)
  ctx.fire("QUEST_LOG_UPDATE")
  M.Advance(1, 0.25)

  -- choisir le donjon depuis la pastille de la minimap (entrée Hub) puis ▶ / SetPlanned
  local hubEntry
  for _, e in ipairs(T.HUB_EXTRA or {}) do if e.id == "donjon" then hubEntry = e end end
  ctx.check(hubEntry and hubEntry.title:find("Prêt"), "entrée « Prêt pour le donjon ? » pour la pastille")
  local H = T.Hub
  if H and H.Entries then
    local found = false
    for _, e in ipairs(H.Entries()) do if e.id == "donjon" then found = true end end
    ctx.check(found, "Hub liste l'entrée Donjon")
  end
  M.Hw(hubEntry.fn)
  local W = D._prep
  ctx.check(W.frame and W.frame:IsShown(), "fenêtre « Prêt ? » ouverte au clic")
  D.SetPlanned("brassenoire")
  ctx.check(D.char.planned == "BFD", "donjon prévu : Brassenoire")
  M.Advance(2, 0.25)           -- index Questie construit par tranches (minuterie)
  ctx.check(D.QuestIndex() and D.db.qidx.areas[719], "index des quêtes du donjon construit et mémorisé")

  -- quêtes : dans le journal (qui ne l'a pas, objet sur quel boss) / à prendre (donneur)
  local d = D.Find("BFD")
  local qs = D.Quests(d, D.Members(true))
  local q1 = qs.inLog[1]
  ctx.check(q1 and q1.id == 1001 and #q1.missing == 1 and q1.missing[1] == "Ami-deux", "quête du journal : manque au mage seulement")
  ctx.check(q1 and q1.obj[1] and q1.obj[1].boss and q1.obj[1].boss.n == "Dame Sarevess", "objet de quête rattaché au boss (Questie npcDrops)")
  ctx.check(#qs.toGet == 1 and qs.toGet[1].id == 1002 and qs.toGet[1].giver == "Garde-test d'Astranaar", "quête à prendre + donneur ; trop dure filtrée")

  D:Refresh(true)
  local tx = text(D.lines)
  ctx.check(tx:find("manque à Ami%-deux"), "onglet : qui n'a pas la quête")
  ctx.check(tx:find("Prends « Dans les profondeurs » %[24%] chez Garde%-test"), "onglet : quête à prendre : " .. tx)
  ctx.check(tx:find("Dame Sarevess |T", 1, true), "liste des boss : icône de quête sur Sarevess")
  local share
  for _, l in ipairs(D.lines) do if l.control and l.label == "Partager" then share = l end end
  ctx.check(share, "bouton d'onglet « Partager »")
  ctx.check(not tx:find("une quête ailleurs", 1, true), "quête d'une autre zone ignorée")

  -- fenêtre : sections, bouton Partager sur la ligne, verdict
  D:RenderChecklist()
  local rows, headers, btn = W.last or {}, {}, nil
  for _, r in ipairs(rows) do if r.header then headers[#headers + 1] = r.text end if r.btn then btn = r end end
  ctx.check(table.concat(headers, ",") == "Groupe,Quêtes du donjon,Préparation", "sections de la fenêtre")
  ctx.check(btn and btn.text:find("Ami%-deux"), "ligne quête avec bouton Partager")
  ctx.check((W.warn or 0) >= 1, "verdict : points à régler")

  -- partage : un clic = QuestLogPushQuest(index du journal), jamais en combat
  M.EnterCombat()
  M.Hw(D.ShareQuests, D)
  ctx.check(#pushed == 0, "pas de partage en combat")
  M.LeaveCombat()
  M.Hw(D.ShareQuests, D)
  M.Advance(1, 0.2)
  ctx.check(#pushed == 1 and pushed[1] == 2, "partage de la quête du donjon (index 2) : " .. tostring(pushed[1]))

  -- qui fait quoi : mage = contrôle (libre), prêtre soigneur = dissipe (en second choix)
  local members = D.Members(true)
  local sar, kel = d.bosses[2], d.bosses[6]
  local jt = D.JobsText(sar, members)
  ctx.check(jt:find("Ami%-un tanke") and jt:find("Ami%-trois soigne") and jt:find("Ami%-deux contrôle l'add %(Métamorphose%)"), "jobs Sarevess : " .. jt)
  local jk = D.JobsText(kel, members)
  ctx.check(jk:find("Ami%-trois dissipe la magie"), "jobs Kelris : " .. jk)
  local solo = D.JobsText("curse", { members[1], members[3] })
  ctx.check(solo:find("Ami%-deux enlève les malédictions %(Délivrance"), "malédiction : mage niv 26 = Délivrance : " .. solo)
  local none = D.JobsText("curse", { members[1], members[2] })
  ctx.check(none:find("personne n'enlève les malédictions", 1, true), "élision : " .. none)

  -- butin : arbalète (Besoin chasseur), mailles (plus tard, niv 40), objet inconnu du client = classes des données
  M.AddItem(3078, { name = "Perce-cœur naga", equipLoc = "INVTYPE_RANGEDRIGHT", classID = 2, subclassID = 18, minLevel = 23, quality = 3,
    stats = { ITEM_MOD_AGILITY_SHORT = 4 } })
  M.AddItem(6907, { name = "Armure de tortue", equipLoc = "INVTYPE_CHEST", classID = 4, subclassID = 3, minLevel = 22, quality = 3 })
  D._itemGen = (D._itemGen or 0) + 1
  local plan = D.LootPlan(d, members)
  local xbow, mail
  for _, e in ipairs(plan) do if e.id == 3078 then xbow = e end if e.id == 6907 then mail = e end end
  ctx.check(xbow and xbow.verdict[members[1].name] == "Besoin" and xbow.verdict["Ami-un"] == "Cupidité", "arbalète : Besoin pour toi, Cupidité pour le guerrier")
  ctx.check(mail and #mail.need == 0 and mail.later[1] and mail.later[1].lvl == 40, "mailles : personne en Besoin, toi au niveau 40")
  D:Refresh(true)
  tx = text(D.lines)
  ctx.check(tx:find("Pour toi : "), "ligne butin pour toi")
  ctx.check(tx:find("Chasseur") and tx:find("Ami%-un garde l'aggro"), "chasseur : familier pas tank (guerrier là)")
  ctx.check(tx:find("À contrôler : "), "cibles à contrôler du donjon")

  -- fermer la fenêtre : plus d'évènements à elle
  D:ToggleChecklist(false)
  ctx.check(not W.frame:IsShown(), "fenêtre fermée")

  -- entrée dans l'instance : chrono, prochain boss, objet de quête sur le boss tué
  M.instance = true
  GetInstanceInfo = function() return "Profondeurs de Brassenoire", "party", 1, "Normal", 5, 0, false, 48 end
  ctx.fire("PLAYER_ENTERING_WORLD")
  ctx.check(D.char.run and D.char.run.key == "BFD" and D._ticker, "run + minuterie en instance")
  D:Refresh(true)
  tx = text(D.lines)
  ctx.check(tx:find("en cours 0:%d%d · 0/6 boss"), "en-tête : chrono + boss : " .. tx:sub(1, 120))
  ctx.check(tx:find("Prochain : Ghamoo%-Ra"), "prochain boss = le 1er")
  M.EnterCombat()
  ctx.fire("ENCOUNTER_START", 2916, "Ghamoo-Ra", 1, 5)
  ctx.fire("ENCOUNTER_END", 2916, "Ghamoo-Ra", 1, 5, 1)
  M.LeaveCombat()
  D:Refresh(true)
  tx = text(D.lines)
  ctx.check(tx:find("Prochain : Dame Sarevess") and tx:find("Quête : ramasse « Cœur de Sarevess » sur lui", 1, true), "prochain = Sarevess + objet de quête")
  ctx.check(tx:find("Butin : Perce%-cœur naga → Besoin : toi"), "butin du prochain boss avec Besoin / Cupidité")
  ctx.check(tx:find("Pull : Nagas Brassenoire"), "pull connu avant Sarevess")
  M.EnterCombat()
  ctx.fire("ENCOUNTER_START", 2915, "Dame Sarevess", 1, 5)
  M.group[2].dead = true
  ctx.fire("ENCOUNTER_END", 2915, "Dame Sarevess", 1, 5, 1)
  M.LeaveCombat()
  ctx.check(S["Dungeon:qdrop"] and S["Dungeon:qdrop"].text:find("Ramasse « Cœur de Sarevess » sur Dame Sarevess", 1, true), "rappel : ramasser l'objet de quête")
  ctx.check(D.char.run.pdeaths == 1, "mort d'un membre comptée (fin de combat)")
  M.group[2].dead = false
  rawset(_G, "LOOT_ITEM_SELF", "Vous recevez le butin : %s.")
  ctx.fire("CHAT_MSG_LOOT", "Vous recevez le butin : " .. M.ItemLink(3078) .. ".")
  ctx.check(D.char.run.loot[1] == "Perce-cœur naga", "butin reçu noté")
  M.quests[2].complete = true
  M.quests[2].objectives[1] = { text = "Cœur de Sarevess : 1/1", done = true }
  ctx.fire("QUEST_LOG_UPDATE")
  M.Advance(6, 0.5)            -- Quests relit le journal (différé après l'entrée dans l'instance)

  -- sortie : bilan (onglet + suggestion), minuterie arrêtée
  M.instance = false
  GetInstanceInfo = function() return "Sombrivage", "none", 0, "", 5, 0, false, 0 end
  ctx.fire("PLAYER_ENTERING_WORLD")
  local last = D.char.last
  ctx.check(last and last.kills == 2 and last.total == 6 and last.loot[1] == "Perce-cœur naga", "bilan : 2/6 boss, butin")
  ctx.check(last and last.quests[1] == "Le cœur de la naga", "bilan : quête finie pendant le run")
  ctx.check(S["Dungeon:summary"] and S["Dungeon:summary"].text:find("Bilan Profondeurs de Brassenoire : 0:%d%d · 2/6 boss · 1 mort · 1 quête finie"),
    "suggestion bilan : " .. tostring(S["Dungeon:summary"] and S["Dungeon:summary"].text))
  M.Advance(2, 1)
  ctx.check(not D._ticker, "minuterie coupée hors instance (fenêtre fermée)")
  D:Refresh(true)
  ctx.check(T.tabs.dungeon and text(D.lines):find("Rends tes quêtes finies"), "onglet bilan après la sortie")
  T:UpdateSummary()
  local s = T:Summary()
  ctx.check(s.dungeon and s.dungeon.last and s.dungeon.last.kills == 2, "summary.dungeon.last")
  rawset(_G, "QuestLogPushQuest", nil)
  rawset(_G, "LOOT_ITEM_SELF", nil)
end
