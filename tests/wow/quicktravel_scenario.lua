-- Scenario QUICK3 : module QuickTravel (raccourcis voyage / groupe / courses). Fausses données, faux noms.
-- Vérifie : meilleur vol (tables client réelles), carte « Prendre ce vol » + clic = TakeTaxiNode(slot) sous un
-- événement matériel, rien en combat ; aubergiste « Lier ta pierre ici » ; courses groupées (étapes fusionnées
-- par PNJ, triées) ; fenêtre « Tout le monde prêt ? » + boutons du chef ; point de rencontre (envoi bridé,
-- réception validée, boussole).
-- lua5.1 tests/wow/harness.lua -q --flavor forever --class HUNTER --scenario tests/wow/quick_scenario.lua wow_addon/TreeAI
return function(M, ctx)
  local T = TreeAI
  local check = ctx.check
  local Q = T.QuickTravel
  check(Q and Q.db and Q.db.enabled, "QuickTravel chargé et actif")
  if not Q then return end
  local D = T.Data and T.Data.Travel
  check(D and D.taxi and D.taxi[23] and D.taxi[25], "Data/Travel : Orgrimmar (23) et La Croisée (25)")
  M.LeaveCombat()
  local function near(a, b, e) return a and b and math.abs(a - b) <= (e or 1e-3) end
  local function sugg(id)
    for _, e in ipairs(T:GetSuggestions() or {}) do if e.id == id then return e end end
  end
  local function clean(text, where)
    local p = T.PlainText and T.PlainText(tostring(text or "")) or tostring(text or "")
    check(not p:find("%f[%a]nil%f[%A]"), where .. " sans nil : " .. p)
    check(not p:find("?", 1, true), where .. " sans « ? » : " .. p)
  end
  local saved = {}
  local function stub(tbl, k, v) saved[#saved + 1] = { tbl, k, rawget(tbl, k) } rawset(tbl, k, v) end
  local function restore() for i = #saved, 1, -1 do local s = saved[i] rawset(s[1], s[2], s[3]) end saved = {} end

  -- ------------------------------------------------------------ 1. vols (pur)
  local allowed = { [23] = true, [25] = true, [22] = true }
  local fl = Q.FlightTimes(23, D.flights, allowed)
  check(near(fl[25], 108.25), "vol direct Orgrimmar → La Croisée = 108,25 s : " .. tostring(fl[25]))
  check(fl[23] == nil, "pas de vol vers soi-même")
  local org = { c = 1, n = 1677.6, w = -4315.7 }
  local cro = { c = 1, n = -441.8, w = -2596.1 }
  local cands = { { slot = 2, id = 25, name = "La Croisée", pos = cro }, { slot = 3, id = 22, name = "Nœud-test",
    pos = { c = D.taxi[22][2], n = D.taxi[22][3], w = D.taxi[22][4] } } }
  local dest = { c = 1, n = -481.8, w = -2596.1, name = "Camp-test" }
  local b = Q.BestFlight(cands, dest, org, fl)
  check(b and b.id == 25 and b.slot == 2 and near(b.total, 108.25 + 20 + 40 * 1.3 / 7), "meilleur vol = La Croisée : "
    .. tostring(b and b.id) .. " " .. tostring(b and b.total))
  check(b and b.gain > 300, "gain du vol face à la marche")
  check(Q.BestFlight(cands, { c = 1, n = 1660, w = -4315.7 }, org, fl) == nil, "destination à côté : marcher, pas de vol")
  check(Q.BestFlight(cands, dest, org, {}) == nil, "aucun vol connu : rien")

  -- ------------------------------------------------------------ 2. carte des vols + clic
  local took = {}
  stub(_G, "GetTaxiMapID", function() return 1414 end)
  stub(_G, "C_TaxiMap", { GetAllTaxiNodes = function()
    return { { nodeID = 23, slotIndex = 1, state = 0, name = "Orgrimmar" }, { nodeID = 25, slotIndex = 2, state = 1, name = "La Croisée" },
      { nodeID = 22, slotIndex = 3, state = 1, name = "Nœud-test" }, { nodeID = 99, slotIndex = 4, state = 2, name = "Loin-test" } }
  end })
  stub(_G, "TaxiNodeCost", function(i) return i == 2 and 120 or 500 end)
  stub(_G, "TakeTaxiNode", function(slot) took[#took + 1] = { slot = slot, hw = M.hardware } end)
  local tf = CreateFrame("Frame", "TaxiFrame") tf:Show()
  stub(_G, "TaxiFrame", tf)
  local tb = CreateFrame("Button", "TaxiButton2", tf) tb:Show()
  stub(_G, "TaxiButton2", tb)
  local realDest = Q.Destination
  Q.Destination = function() return dest end
  M.FireEvent("TAXIMAP_OPENED", 1)
  M.Advance(0.3)
  check(Q.flight and Q.flight.id == 25 and Q.flight.slot == 2, "carte des vols : La Croisée choisie")
  local found
  for _, f in ipairs(M.frames) do
    if f.text and f.btn and f:IsShown() and tostring(f.text:GetText() or ""):find("La Croisée", 1, true) then found = f end
  end
  check(found, "texte de la carte : meilleur vol vers La Croisée")
  if found then
    clean(found.text:GetText(), "carte vol")
    check(found.text:GetText():find("Camp-test", 1, true), "la carte nomme la destination : " .. found.text:GetText())
    M.EnterCombat()
    found.btn:Click()
    check(#took == 0, "en combat : aucun vol pris")
    M.LeaveCombat()
    Q.UpdateTaxi()
    found.btn:Click()
    check(#took == 1 and took[1].slot == 2 and took[1].hw, "clic : TakeTaxiNode(2) sous un clic du joueur")
    check(not found:IsShown(), "carte rangée après le départ")
  end
  M.FireEvent("TAXIMAP_CLOSED")
  check(Q.flight == nil, "fermeture : plus de vol proposé")
  Q.Destination = function() return nil end
  M.FireEvent("TAXIMAP_OPENED", 1) M.Advance(0.3)
  check(Q.flight == nil, "sans destination : rien ne brille")
  Q.Destination = realDest
  restore()

  -- ------------------------------------------------------------ 3. aubergiste
  check(Q.InnWorth(100, 400) and not Q.InnWorth(380, 400) and not Q.InnWorth(100, 140) and Q.InnWorth(50, nil)
    and not Q.InnWorth(nil, 300), "lier ici : seulement si nettement mieux")
  local picked = {}
  stub(_G, "C_GossipInfo", { GetOptions = function()
    return { { gossipOptionID = 11, name = "Je voudrais acheter quelque chose." },
      { gossipOptionID = 77, name = "Faites de cette auberge votre foyer.", icon = 132052 } }
  end, SelectOption = function(id) picked[#picked + 1] = id end })
  local gf = CreateFrame("Frame", "GossipFrame") gf:Show()
  stub(_G, "GossipFrame", gf)
  local R = T.Route
  local rq, rp, rl, gp = Q.NextQuestPoints, R and R.BindPlace, R and R.LegSeconds, T.Geo and T.Geo.Player
  Q.NextQuestPoints = function() return { { map = 1, x = 10, y = 10 }, { map = 1, x = 12, y = 10 } } end
  if T.Geo then T.Geo.Player = function() return 1, 11, 10 end end
  if R then
    R.BindPlace = function() return { map = 1, x = 80, y = 80 } end
    R.LegSeconds = function(a, c) return math.sqrt((a.x - c.x) ^ 2 + (a.y - c.y) ^ 2) * 10 end
  end
  M.player.subzone = "Village-test"
  M.FireEvent("GOSSIP_SHOW")
  local inn
  for _, f in ipairs(M.frames) do
    if f.text and f.btn and f:IsShown() and tostring(f.text:GetText() or ""):find("Lie ta pierre ici", 1, true) then inn = f end
  end
  check(inn, "aubergiste : « Lie ta pierre ici » proposé")
  if inn then
    clean(inn.text:GetText(), "carte auberge")
    inn.btn:Click()
    check(#picked == 1 and picked[1] == 77, "clic : l'option « foyer » de l'aubergiste (77)")
  end
  M.FireEvent("GOSSIP_CLOSED")
  M.player.subzone = "Auberge-Test"            -- déjà lié ici (GetBindLocation du mock)
  M.FireEvent("GOSSIP_SHOW")
  local again
  for _, f in ipairs(M.frames) do
    if f.text and f.btn and f:IsShown() and tostring(f.text:GetText() or ""):find("Lie ta pierre", 1, true) then again = f end
  end
  check(not again, "déjà lié à cette auberge : rien")
  M.FireEvent("GOSSIP_CLOSED")
  Q.NextQuestPoints = rq
  if T.Geo then T.Geo.Player = gp end
  if R then R.BindPlace, R.LegSeconds = rp, rl end
  restore()

  -- ------------------------------------------------------------ 4. courses groupées
  local svc = {
    repair = { name = "Forgeron-test", mapID = 1, x = 0.5, y = 0.5, dist = 50 },
    vendor = { name = "Forgeron-test", mapID = 1, x = 0.5, y = 0.5, dist = 50 },
    ammo = { name = "Armurier-test", mapID = 1, x = 0.6, y = 0.5, dist = 200 },
    food = { name = "Aubergiste-test", mapID = 1, x = 0.4, y = 0.5, dist = 100 },
  }
  local steps = Q.ErrandSteps({ repair = 40, greys = 3, ammo = 120, food = 2, water = 1, mail = true },
    function(k) return svc[k] end)
  check(#steps == 4, "4 étapes (forgeron, aubergiste, armurier, courrier) : " .. #steps)
  check(steps[1].text:find("^Répare et vends les gris chez Forgeron%-test"), "étape 1 fusionnée : " .. tostring(steps[1].text))
  check(steps[2].text:find("^Achète à manger et à boire chez Aubergiste%-test"), "étape 2 : " .. tostring(steps[2].text))
  check(steps[3].text:find("^Achète des munitions chez Armurier%-test"), "étape 3 : " .. tostring(steps[3].text))
  check(steps[4].text == "Relève ton courrier" and steps[4].svc == nil, "étape sans lieu connu en dernier : " .. tostring(steps[4].text))
  for i, s in ipairs(steps) do clean(s.text, "étape " .. i) end
  check(#Q.ErrandSteps({ greys = 0 }, function() return nil end) == 0, "rien à faire : aucune étape")
  local ammoID = T.Data.Consumables and T.Data.Consumables.ammo[1][1]
  local tags = Q.SellTags({ ammoID, 999999 })
  check(tags.a == 1 and not tags.f, "marchand de munitions reconnu par ses objets")
  -- Questie (lecture seule) : qui vend les munitions de mon niveau, sur ma carte, de ma faction
  local Cp0 = T.Compass
  local rQ, rS, rMe = Cp0.Questie, Cp0.SpawnsOf, M.pos
  M.pos = { mapID = 1, x = 0.5, y = 0.5 }
  local best = {}
  for _, r in ipairs(T.Data.Consumables.ammo) do if r[2] <= 28 and (not best[1] or r[3] > best[1][3]) then best[1] = r end end
  local qdb = { QueryItemSingle = function(id, k) if k == "vendors" and id == best[1][1] then return { 501, 502 } end end,
    QueryNPCSingle = function(id, k) if k == "friendlyToFaction" then return id == 502 and "H" or "AH" end end }
  Cp0.Questie = function() return qdb, nil end
  Cp0.SpawnsOf = function(_, npc, _, _, _, out)
    out[#out + 1] = { mapID = 1, x = npc == 501 and 0.52 or 0.5, y = 0.5, name = npc == 501 and "Armurier-questie" or "Horde-test" }
  end
  local fac = rawget(_G, "UnitFactionGroup")
  rawset(_G, "UnitFactionGroup", function() return "Alliance", "Alliance" end)
  local qv = Q.QuestieVendor("ammo")
  check(qv and qv.name == "Armurier-questie" and qv.src == "questie", "Questie : marchand de munitions de ma faction trouvé : "
    .. tostring(qv and qv.name))
  rawset(_G, "UnitFactionGroup", fac)
  Cp0.Questie, Cp0.SpawnsOf, M.pos = rQ, rS, rMe
  local rn = Q.Needs
  Q.Needs = function() return { repair = 40, mail = true } end
  M.player.resting = true
  Q.UpdateErrands()
  local e = sugg("QuickTravel:errands")
  check(e and e.text:find("Courses avant de partir (2)", 1, true), "ligne courses au repos : " .. tostring(e and e.text))
  if e then clean(e.text, "ligne courses") end
  M.player.resting = false
  Q.UpdateErrands()
  check(not sugg("QuickTravel:errands"), "hors repos : ligne rangée")
  Q.Needs = rn

  -- ------------------------------------------------------------ 5. groupe : prêts, chef, rendez-vous
  local ok, why = Q.Verdict({ class = "MAGE", hp = 90, mana = 40 })
  check(not ok and why == "mana 40 %", "verdict mana bas")
  ok, why = Q.Verdict({ class = "HUNTER", hp = 95, mana = 90, ammo = 50, pet = true })
  check(not ok and why == "50 munitions", "verdict munitions")
  ok, why = Q.Verdict({ class = "WARLOCK", hp = 95, mana = 90, pet = false })
  check(not ok and why == "familier absent", "verdict familier")
  check(Q.Verdict({ class = "WARRIOR", hp = 100 }), "guerrier en forme : prêt")
  check(Q.NextLoot("group") == "freeforall" and Q.NextLoot("master") == "group", "cycle des modes de butin")

  M.group = { { name = "Bravo-test", class = "MAGE", level = 28, health = 900, healthMax = 900, power = 30, powerMax = 100 },
    { name = "Charlie-test", class = "WARRIOR", level = 28, health = 800, healthMax = 900, powerType = 1 } }
  M.FireEvent("GROUP_ROSTER_UPDATE")
  M.Advance(0.5)
  -- (4.0.1) TreeAI n'invite plus jamais de lui-même et ne retient plus les joueurs croisés en groupe
  check(Q.char.friends and #Q.char.friends == 0, "plus d'« amis » tirés du dernier groupe")
  check(Q.InviteFriends() == 0, "aucune invitation par TreeAI")
  local rows = Q.MemberRows()
  check(#rows == 3, "3 lignes (toi + 2) : " .. #rows)
  local bravo
  for _, r in ipairs(rows) do if r.name == "Bravo-test" then bravo = r end end
  check(bravo and not bravo.ok and bravo.why == "mana 30 %", "Bravo pas prêt (mana)")
  clean(Q.RowText(bravo or {}), "ligne membre")
  local calls = {}
  stub(_G, "C_PartyInfo", {
    DoReadyCheck = function() calls[#calls + 1] = "ready" end,
    ConvertToRaid = function() calls[#calls + 1] = "raid" end,
    ConvertToParty = function() calls[#calls + 1] = "party" end,
    InviteUnit = function(n) calls[#calls + 1] = "inv:" .. n end,
    GetLootMethod = function() return "group" end,
    SetLootMethod = function(m) calls[#calls + 1] = "loot:" .. m end,
  })
  local w = Q:ShowReady(true)
  check(w and w:IsShown(), "fenêtre « Tout le monde prêt ? » ouverte")
  -- (4.0 QA) le résumé tient dans la fenêtre : « n/m prêts : voir ci-dessous » (le détail est sur chaque ligne)
  check(w and w.sum and tostring(w.sum:GetText()):find("prêts : voir ci-dessous", 1, true) and w.sum:GetWidth() <= 340,
    "résumé court, dans la fenêtre : " .. tostring(w and w.sum and w.sum:GetText()))
  if w then
    check(w.bReady:IsShown() and w.bConvert:IsShown() and w.bLoot:IsShown(), "chef : appel, convertir, butin visibles")
    check(w.bInvite == nil, "plus de bouton « Inviter »")
    w.bReady:Click() w.bConvert:Click() w.bLoot:Click()
    check(calls[1] == "ready" and calls[2] == "raid" and calls[3] == "loot:freeforall", "clics du chef : " .. table.concat(calls, ","))
  end
  -- rendez-vous : envoi bridé, par le protocole unique du module Groupe (GRP « E;meet »)
  M.pos = M.pos or { mapID = 1, x = 0.5, y = 0.5 }
  local okS = Q.SendMeet()
  local okS2, why2 = Q.SendMeet()
  check(okS and not okS2 and why2 == "gap", "point de rencontre : 1 envoi, le 2e bridé (" .. tostring(why2) .. ")")
  local viaGroup = false
  for _, s in ipairs(M.sentAddon) do
    local msg = tostring(s.msg)
    if msg:find("~r4;E;meet;", 1, true) then viaGroup = true end
    check(not msg:match("^1~QTM~"), "plus de message QTM à part")
  end
  check(viaGroup, "rendez-vous envoyé par le module Groupe")
  -- réception : membre du groupe → boussole + ligne ; inconnu / payload invalide → rien
  local aimed
  local Cp = T.Compass
  local realSet = Cp and Cp.SetTarget
  if Cp then Cp.SetTarget = function(_, t) aimed = t return t end end
  check(Q.OnMeet({ v = 1, m = 1, x = 420, y = 615, l = "Moulin-test" }, "Inconnu-test") == nil, "inconnu : ignoré")
  check(Q.OnMeet({ v = 1, m = 1, x = 5000, y = 1, l = "x" }, "Bravo-test") == nil, "coordonnées hors bornes : ignorées")
  local m = Q.OnMeet({ v = 1, m = 1, x = 420, y = 615, l = "Moulin-test" }, "Bravo-test")
  check(m and near(m.x, 42) and near(m.y, 61.5), "rendez-vous reçu de Bravo")
  check(aimed and aimed.source == "meet" and (near(aimed.x, 42) or near(aimed.x, 0.42)), "la boussole vise le rendez-vous")
  local ml = sugg("Group:meet")
  check(ml and ml.text:find("Rejoins Bravo au point de rencontre", 1, true), "ligne rendez-vous : " .. tostring(ml and ml.text))
  check(Q.OnMeet({ v = 1, m = 1, x = 100, y = 100 }, "Bravo-test") == nil, "2e rendez-vous en < 10 s : bridé")
  if Cp then Cp.SetTarget = realSet end
  Q:ShowReady(false)
  M.group = {}
  M.FireEvent("GROUP_ROSTER_UPDATE")
  restore()
  check(#(M.blocked or {}) == 0, "aucune action bloquée")
end
