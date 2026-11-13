-- Harness scenario for Modules/GroupGear.lua (« Équipement du groupe », GEARTRADE 4.1) on the REAL addon: a group of
-- 4 (me = level-29 hunter, Bravo = warrior with TreeAI, Charlie = mage and Delta = priest without TreeAI). TreeAI gear
-- sharing over the group channel (GGE / GGB / GGQ, only on change), discreet inspection of the others (out of combat,
-- one at a time, ≥ 2 s apart, in range only, retried when back in range), trade suggestions (who gains most), the
-- « Groupe » page section, the item tooltip line, loot roll line, trade window on a click only. Fake names only.
-- lua5.1 tests/wow/harness.lua -q --flavor forever --class HUNTER --level 29 --allow InitiateTrade --allow C_TooltipInfo
--   --allow GetLootRollItemLink --allow BIND_TRADE_TIME_REMAINING --scenario tests/wow/groupgear_scenario.lua wow_addon/TreeAI
return function(M, ctx)
  local T = TreeAI
  local check = ctx.check
  local GG = T and T.modules and T.modules.GroupGear
  check(GG and T.GroupGear == GG and GG.title == "Équipement du groupe", "module « Équipement du groupe » enregistré")
  if not GG then return end
  check(T.SAFE_MODULES.GroupGear == true and T.MODULE_INFO.GroupGear and T.MODULE_INFO.GroupGear.gate == "group",
    "mode sûr + porte « groupe » (rien seul)")
  M.LeaveCombat()
  T:ApplyPreset("tout")
  M.Advance(2, 0.5)
  check(GG._active, "module actif")
  local chat0, out0 = #M.sentChat, #M.output
  local blocked0 = #M.blocked

  -- objets (noms factices) : stats au format GetItemStats
  local function st(t)
    local K = { STR = "ITEM_MOD_STRENGTH_SHORT", AGI = "ITEM_MOD_AGILITY_SHORT", STA = "ITEM_MOD_STAMINA_SHORT",
      INT = "ITEM_MOD_INTELLECT_SHORT", SPI = "ITEM_MOD_SPIRIT_SHORT", DPS = "ITEM_MOD_DAMAGE_PER_SECOND_SHORT" }
    local o = {}
    for k, v in pairs(t) do o[K[k]] = v end
    return o
  end
  local function item(id, name, q, ilvl, req, loc, cid, sub, stats)
    M.AddItem(id, { name = name, quality = q, ilvl = ilvl, minLevel = req, equipLoc = loc, classID = cid,
      subclassID = sub, stats = st(stats), type = cid == 2 and "Arme" or "Armure" })
  end
  item(99501, "Hache-test lourde", 2, 27, 22, "INVTYPE_2HWEAPON", 2, 1, { STR = 8, STA = 5, DPS = 15 })
  item(99502, "Robe-test de l'intellect", 2, 26, 24, "INVTYPE_ROBE", 4, 1, { INT = 7, SPI = 6, STA = 3 })
  item(99503, "Bâton-test des soins", 3, 28, 25, "INVTYPE_2HWEAPON", 2, 10, { INT = 6, SPI = 8, STA = 4, DPS = 14 })
  item(99504, "Bottes-test en maille", 2, 27, 25, "INVTYPE_FEET", 4, 3, { STR = 5, STA = 4 })
  item(99505, "Cape-test de force", 2, 25, 22, "INVTYPE_CLOAK", 4, 1, { STR = 4, STA = 3 })
  item(99506, "Gants-test liés", 2, 25, 22, "INVTYPE_HAND", 4, 2, { STR = 3, STA = 3 })
  item(99507, "Arc-test du faucon", 2, 27, 24, "INVTYPE_RANGED", 2, 2, { AGI = 5, DPS = 12 })
  -- équipement porté
  item(99520, "Hache-test rouillée", 0, 8, 5, "INVTYPE_WEAPON", 2, 0, { DPS = 5 })
  item(99540, "Robe-test du novice", 1, 10, 5, "INVTYPE_ROBE", 4, 1, { INT = 2, SPI = 2 })
  item(99550, "Cotte-test", 2, 22, 18, "INVTYPE_CHEST", 4, 3, { STR = 4, STA = 4 })
  item(99560, "Tunique-test en cuir", 2, 24, 20, "INVTYPE_CHEST", 4, 2, { AGI = 5, STA = 3 })
  item(99570, "Masse-test", 1, 12, 8, "INVTYPE_WEAPON", 2, 4, { STA = 1, DPS = 6 })
  item(99580, "Bâton-test de l'apprenti", 2, 24, 20, "INVTYPE_2HWEAPON", 2, 10, { INT = 8, SPI = 2, STA = 3, DPS = 11 })
  item(99590, "Cape-test usée", 1, 10, 5, "INVTYPE_CLOAK", 4, 1, { STA = 1 })
  item(99600, "Cape-test d'agilité", 2, 24, 20, "INVTYPE_CLOAK", 4, 1, { AGI = 3 })
  item(99610, "Arc-test courbe", 2, 20, 16, "INVTYPE_RANGED", 2, 2, { DPS = 8 })
  item(99620, "Couteaux-test de lancer", 1, 18, 14, "INVTYPE_THROWN", 2, 16, { DPS = 6 })
  item(99630, "Épée-test du chasseur", 2, 24, 20, "INVTYPE_2HWEAPON", 2, 8, { AGI = 6, STA = 4, DPS = 12 })
  M.inventory[5], M.inventory[15] = { id = 99560 }, { id = 99600 }
  M.inventory[16], M.inventory[18] = { id = 99630 }, { id = 99610 }
  -- mes sacs : 5 objets échangeables (non liés), une cape liée encore échangeable, des gants liés
  M.SetBagItem(0, 1, 99501) M.SetBagItem(0, 2, 99502) M.SetBagItem(0, 3, 99503) M.SetBagItem(0, 4, 99504)
  M.SetBagItem(0, 5, 99505) M.SetBagItem(0, 6, 99506)
  local boundSlot = { ["0:5"] = true, ["0:6"] = true }
  local C = _G.C_Container
  if C and C.GetContainerItemInfo then
    local orig = C.GetContainerItemInfo
    C.GetContainerItemInfo = function(bag, slot)
      local i = orig(bag, slot)
      if type(i) == "table" and boundSlot[bag .. ":" .. slot] then i.isBound = true end
      return i
    end
  end
  _G.BIND_TRADE_TIME_REMAINING = "Vous pouvez échanger cet objet avec les joueurs qui pouvaient également le recevoir pendant les %s prochaines."
  local tipCalls = 0
  _G.C_TooltipInfo = { GetBagItem = function(bag, slot)
    tipCalls = tipCalls + 1
    local s = M.bags[bag] and M.bags[bag].slots[slot]
    local it = s and M.items[s.id]
    if not it then return nil end
    local lines = { { leftText = it.name } }
    if boundSlot[bag .. ":" .. slot] then lines[#lines + 1] = { leftText = "Lié" } end
    if bag == 0 and slot == 5 then lines[#lines + 1] = { leftText = _G.BIND_TRADE_TIME_REMAINING:format("1 h 52 min") } end
    return { lines = lines }
  end }

  -- inspection : qui, quand (les demandes sont comptées) ; portée d'inspection (1) et d'échange (2) par membre
  local inspected = {}
  local notify = _G.NotifyInspect
  _G.NotifyInspect = function(u)
    inspected[#inspected + 1] = { unit = u, name = _G.UnitName(u), t = _G.GetTime(), combat = M.inCombat }
    return notify(u)
  end
  local clears = 0
  _G.ClearInspectPlayer = function() clears = clears + 1 end
  _G.CheckInteractDistance = function(u, i)
    local n = tostring(u):match("^party(%d)$")
    local d = n and M.group[tonumber(n)]
    if not d then return true end
    if i == 1 then return not d.far end
    return not d.farTrade and not d.far
  end
  local trades = {}
  _G.InitiateTrade = function(u) trades[#trades + 1] = { unit = u, hw = M.hardware, combat = M.inCombat } end

  ctx.fire("BAG_UPDATE_DELAYED")
  ctx.fire("PLAYER_EQUIPMENT_CHANGED", 16)
  local sent0 = #M.sentAddon
  M.group = {
    { name = "Bravo", class = "WARRIOR", level = 29, online = true, guid = "Player-0-B" },
    { name = "Charlie", class = "MAGE", level = 29, online = true, guid = "Player-0-C",
      gear = { [16] = 99580, [1] = 99590 } },                 -- pas de torse
    { name = "Delta", class = "PRIEST", level = 28, online = true, guid = "Player-0-D", far = true,
      gear = { [5] = 99540, [16] = 99570 } },
  }
  ctx.fire("GROUP_ROSTER_UPDATE")
  M.Advance(0.5, 0.5)
  local seq = 500
  local function send(kind, from, payload)
    seq = seq + 1
    M.DeliverAddon("TREEAI", "1~" .. kind .. "~" .. seq .. "~1/1~r" .. payload, "PARTY", from .. "-Royaume")
  end
  -- Bravo a TreeAI : son équipement (pas de bottes) et ses objets échangeables (un arc)
  send("GGE", "Bravo", "1;1;16=99520,15=99590,5=99550,18=99620")
  send("GGB", "Bravo", "1;99507")
  M.Advance(12, 0.5)

  local R = GG._test.R
  check(R.Bravo and R.Bravo.addon and R.Bravo.src == "treeai" and R.Bravo.gear[16] == "item:99520" and R.Bravo.sp == 1,
    "Bravo (TreeAI) : équipement reçu par le groupe")
  check(#R.Bravo.bags == 1 and R.Bravo.bags[1] == "item:99507", "Bravo : objet échangeable annoncé")
  local who = {}
  for _, e in ipairs(inspected) do who[e.name] = (who[e.name] or 0) + 1 end
  check(not who.Bravo, "ami TreeAI jamais inspecté (il partage lui-même)")
  check(who.Charlie == 1 and R.Charlie.src == "inspect" and R.Charlie.gear[16] and not R.Charlie.gear[5],
    "Charlie (sans TreeAI) inspecté une fois, à portée : " .. tostring(who.Charlie))
  check(not who.Delta and R.Delta.why == "trop loin" and not R.Delta.gearAt, "Delta trop loin : pas inspecté, dit honnêtement")
  check(clears >= 1, "ClearInspectPlayer après la lecture")
  check(GG.Summary(R.Charlie).age == "à l'instant", "âge de l'inspection : « à l'instant »")

  -- mes envois : GGE puis GGB, une fois, canal du groupe, ≤ 255 octets
  local function sentOf(kind)
    local out = {}
    for i = sent0 + 1, #M.sentAddon do
      local m = M.sentAddon[i]
      if m.msg:find("~" .. kind .. "~", 1, true) then out[#out + 1] = m end
    end
    return out
  end
  local gge, ggb = sentOf("GGE"), sentOf("GGB")
  check(#gge == 1 and gge[1].channel == "PARTY" and #gge[1].msg <= 255, "mon équipement envoyé une fois au groupe : " .. #gge)
  check(gge[1] and gge[1].msg:find("16=99630", 1, true) and gge[1].msg:find("18=99610", 1, true), "forme courte : emplacement=n°")
  check(#ggb == 1 and ggb[1].msg:find("99501", 1, true) and ggb[1].msg:find("99505", 1, true)
    and not ggb[1].msg:find("99506", 1, true), "mes objets échangeables annoncés (pas les gants liés)")
  check(#sentOf("GGQ") == 1, "une demande « envoyez-moi votre équipement » à l'arrivée dans le groupe")
  M.Advance(40, 1)
  check(#sentOf("GGE") == 1 and #sentOf("GGB") == 1, "rien ne change : rien n'est renvoyé")

  -- objets échangeables détectés
  local bags = GG.MyTradeable()
  local inBag = {}
  for _, b in ipairs(bags) do inBag[M.items[tonumber(b.link:match("item:(%d+)"))].name] = b end
  check(inBag["Hache-test lourde"] and inBag["Robe-test de l'intellect"] and inBag["Bottes-test en maille"],
    "objets non liés = échangeables")
  check(inBag["Cape-test de force"] and inBag["Cape-test de force"].timer == "1 h 52 min",
    "objet lié ramassé en groupe : encore échangeable (1 h 52 min, infobulle)")
  check(not inBag["Gants-test liés"], "objet lié sans délai : pas échangeable")

  -- échanges conseillés (Delta pas encore vu)
  local function tradeMap()
    local out = {}
    for _, tr in ipairs(GG.Trades()) do out[tr.it.name] = tr end
    return out
  end
  local tm = tradeMap()
  local function is(name, from, to)
    local tr = tm[name]
    return tr and (tr.from.me and "toi" or tr.from.name) == from and (tr.to.me and "toi" or tr.to.name) == to
  end
  check(is("Hache-test lourde", "toi", "Bravo") and tm["Hache-test lourde"].slot == 16, "hache à deux mains → le guerrier")
  check(is("Robe-test de l'intellect", "toi", "Charlie") and tm["Robe-test de l'intellect"].gain >= 999,
    "robe d'intelligence → le mage (torse vide)")
  check(is("Bottes-test en maille", "toi", "Bravo"), "bottes en mailles → le guerrier (le chasseur : niveau 40)")
  check(is("Cape-test de force", "toi", "Bravo") and tm["Cape-test de force"].timer == "1 h 52 min", "cape de force → le guerrier")
  check(is("Arc-test du faucon", "Bravo", "toi") and tm["Arc-test du faucon"].forMe, "l'arc de Bravo → pour toi (chasseur)")
  check(not tm["Gants-test liés"], "rien pour un objet lié")
  local staff = tm["Bâton-test des soins"]
  check(staff and staff.to.name == "Charlie" and #staff.unknown == 1 and staff.unknown[1] == "Delta",
    "bâton : Charlie tant que Delta est inconnu (dit)")

  -- combat : Delta revient à portée → aucune inspection en combat ; après le combat, oui
  M.group[3].far = false
  M.EnterCombat()
  local n0 = #inspected
  M.Advance(8, 1)
  check(#inspected == n0, "aucune inspection en combat")
  M.LeaveCombat()
  M.Advance(6, 1)
  check(#inspected == n0 + 1 and inspected[#inspected].name == "Delta" and not inspected[#inspected].combat,
    "hors combat et à portée : Delta inspecté")
  for i = 2, #inspected do check(inspected[i].t - inspected[i - 1].t >= 2, "≥ 2 s entre deux inspections") end
  check(R.Delta.src == "inspect" and R.Delta.gear[5], "Delta : équipement lu")
  tm = tradeMap()
  check(is("Bâton-test des soins", "toi", "Delta"), "bâton des soins → le prêtre (pas le guerrier, pas le mage)")
  check(is("Robe-test de l'intellect", "toi", "Charlie"), "robe : le mage reste devant (torse vide)")

  -- l'inspection ne se répète pas (mémoire 10 min) ; « il a changé d'objet » → revu, pas plus d'une fois par minute
  n0 = #inspected
  M.Advance(30, 1)
  check(#inspected == n0, "pas de nouvelle inspection avant 10 min")
  ctx.fire("UNIT_INVENTORY_CHANGED", "party2")
  M.Advance(70, 1)
  local tail = {}
  for i = n0 + 1, #inspected do tail[#tail + 1] = inspected[i].name end
  check(#inspected == n0 + 1 and inspected[#inspected].name == "Charlie", "Charlie a changé d'objet : revu une fois : "
    .. table.concat(tail, ","))
  check(GG.Summary(R.Delta).age and GG.Summary(R.Delta).age:find("il y a %d+ min"), "âge : « il y a N min »")
  -- pas de réponse du jeu → ce membre plus tard (30 s), jamais en rafale
  local saved = M.group[2].gear
  M.group[2].gear = nil            -- le serveur ne répond pas (pas d'INSPECT_READY)
  M.Advance(62, 1)
  ctx.fire("UNIT_INVENTORY_CHANGED", "party2")
  n0 = #inspected
  M.Advance(20, 1)
  check(#inspected == n0 + 1, "sans réponse : une demande, puis on attend (" .. (#inspected - n0) .. ")")
  check(R.Charlie.why == "pas de réponse du jeu" and R.Charlie.gearAt, "sans réponse : dit, l'ancien équipement gardé")
  M.group[2].gear = saved
  M.Advance(20, 1)
  check(#inspected == n0 + 2 and R.Charlie.why == nil, "réessai 30 s plus tard : lu")

  -- un ami TreeAI demande l'équipement → renvoyé (et seulement alors)
  send("GGQ", "Bravo", "1")
  M.Advance(12, 1)
  check(#sentOf("GGE") == 2, "demande d'un ami : équipement renvoyé")
  -- je change d'arme → renvoyé une fois (différence)
  M.inventory[16] = { id = 99520 }
  ctx.fire("PLAYER_EQUIPMENT_CHANGED", 16)
  M.Advance(15, 1)
  check(#sentOf("GGE") == 3 and sentOf("GGE")[3].msg:find("16=99520", 1, true), "changement d'équipement : renvoyé")
  M.inventory[16] = { id = 99630 }
  ctx.fire("PLAYER_EQUIPMENT_CHANGED", 16)
  M.Advance(15, 1)
  -- message abîmé / hors bornes : ignoré
  send("GGE", "Bravo", "2;1;16=99520")
  send("GGE", "Inconnu", "1;1;16=99520")
  send("GGB", "Bravo", "1;abc,,99999999999")
  M.Advance(1, 0.5)
  check(R.Bravo.gear[16] == "item:99520" and #R.Bravo.bags == 0 and not R.Inconnu, "messages invalides ignorés")
  send("GGB", "Bravo", "1;99507")
  M.Advance(3, 0.5)

  -- fenêtre : page « Groupe » → section « Équipement du groupe » + « Échanges conseillés »
  local W = T.Window
  check(W and W.Open, "fenêtre TreeAI")
  -- (the harness storm clicks every button at boot, section headers included: start from unfolded sections)
  if W.char and type(W.char.collapsed) == "table" then W.char.collapsed["gg:trade"], W.char.collapsed["gg:gear"] = nil, nil end
  W.Open("group")
  M.Advance(1, 0.5)
  W.Render()
  local labels = {}
  for _, r in ipairs(W.lastLay and W.lastLay.rects or {}) do labels[#labels + 1] = r.kind .. ":" .. r.label end
  local dump = table.concat(labels, "\n")
  local function shown(pat) return dump:find(pat, 1, true) ~= nil end
  check(shown("header:Équipement du groupe") and shown("header:Échanges conseillés"), "page Groupe : les deux sections\n" .. dump)
  check(shown("Bravo · Guerrier 29 · niv d'objet ≈") and shown("main gauche (vide)"), "carte de Bravo (niv d'objet, faible)\n" .. dump)
  check(shown("Charlie · Mage 29") and shown("torse (vide)"), "carte de Charlie : torse vide\n" .. dump)
  check(shown("Hache-test lourde · toi → Bravo · ≈ +") and shown("(main droite)"), "échange : hache → Bravo (gain, emplacement)\n" .. dump)
  check(shown("Arc-test du faucon · Bravo → toi"), "échange : l'arc de Bravo pour toi\n" .. dump)
  -- clic sur l'échange → InitiateTrade(party1) sur le clic (jamais d'objet posé)
  local pool = W._pool and W._pool("row")
  local function rowBtn(pat)
    for _, b in ipairs(pool and pool.list or {}) do
      if b.entry and T.PlainText(b.entry.text):find(pat, 1, true) and b:IsShown() then return b end
    end
  end
  local axeRow = rowBtn("Hache-test lourde")
  check(axeRow and axeRow.entry.onClick and axeRow.entry.clickHint:find("ouvrir l'échange", 1, true), "ligne cliquable « ouvrir l'échange »")
  M.group[1].farTrade = true
  if axeRow then axeRow:Click() end
  check(#trades == 0 and (GG.lastSay or ""):find("Trop loin de Bravo", 1, true), "trop loin : pas d'échange, dit dans la fenêtre")
  M.group[1].farTrade = false
  M.EnterCombat()
  if axeRow then axeRow:Click() end
  check(#trades == 0 and (GG.lastSay or ""):find("combat", 1, true), "en combat : pas d'échange")
  M.LeaveCombat()
  if axeRow then axeRow:Click() end
  check(#trades == 1 and trades[1].unit == "party1" and trades[1].hw and not trades[1].combat,
    "clic : fenêtre d'échange avec Bravo (clic matériel, hors combat)")
  GG.OpenTrade("Bravo")
  check(#trades == 1, "jamais d'échange hors d'un clic")
  local staffRow = rowBtn("Bâton-test des soins")
  check(staffRow and staffRow.entry.onClick, "échange vers Delta (sans TreeAI) : cliquable aussi")

  -- infobulle : la ligne « Groupe » d'Infos objets dit pour qui
  local II = T.ItemInfo
  check(II and II.Build and II.ReadItem and II.Sections and II.Sections._ggWrapped, "Infos objets : ligne « Groupe » branchée")
  if II and II.Build and II.ReadItem then
    local function tip(id, c)
      local it = II.ReadItem(M.ItemLink(id))
      it.id, it.link = id, M.ItemLink(id)
      local lines = II.Build(it, c or {}, false)
      local out = {}
      for _, l in ipairs(lines) do out[#out + 1] = T.PlainText(l[1]) end
      return table.concat(out, "\n")
    end
    local t1 = tip(99501, { bag = 0, slot = 1 })
    check(t1:find("Pour ton groupe : Mieux pour : Bravo (≈ +", 1, true), "infobulle (sac) : « Mieux pour : Bravo (≈ +… %) »\n" .. t1)
    local t2 = tip(99502, { bag = 0, slot = 2 })
    check(t2:find("Mieux pour : Charlie (emplacement vide, torse)", 1, true), "infobulle : Charlie, emplacement vide\n" .. t2)
    local t3 = tip(99503, {})
    check(t3:find("Besoin pour Delta", 1, true), "infobulle (butin / lien) : Besoin pour Delta\n" .. t3)
    local t4 = tip(99506, { bag = 0, slot = 6, bound = true })
    check(not t4:find("Mieux pour :", 1, true), "objet lié non échangeable : pas de « Mieux pour »\n" .. t4)
  end

  -- jet de butin : la ligne du groupe nomme qui y gagne (jamais de jet)
  _G.GetLootRollItemLink = function(id) if id == 7 then return M.ItemLink(99501) end end
  local rolled = false
  _G.RollOnLoot = function() rolled = true end
  ctx.fire("START_LOOT_ROLL", 7, 60000)
  M.Advance(1, 0.5)
  local Grp = T.modules.Group
  local roll
  for _, e in ipairs(Grp._test.news()) do if e.key == "roll7" then roll = e.text end end
  check(roll and roll:find("Jet « Hache-test lourde » : Besoin pour Bravo (≈ +", 1, true), "jet : Besoin pour Bravo\n" .. tostring(roll))
  check(not rolled, "jamais de jet à la place du joueur")
  -- Forever sans GetLootRollItemLink : l'objet du jet lu dans les données d'infobulle
  _G.GetLootRollItemLink = nil
  _G.C_TooltipInfo.GetLootRollItem = function(id) if id == 8 then return { id = 99502 } end end
  ctx.fire("START_LOOT_ROLL", 8, 60000)
  M.Advance(1, 0.5)
  roll = nil
  for _, e in ipairs(Grp._test.news()) do if e.key == "roll8" then roll = e.text end end
  check(roll and roll:find("Jet « Robe-test de l'intellect » : Besoin pour Charlie (emplacement vide, torse)", 1, true),
    "jet (données d'infobulle) : Besoin pour Charlie\n" .. tostring(roll))

  -- Team.GearOf (Donjon, Infos objets) voit l'équipement partagé par Bravo
  local gb = T.Team and T.Team.GearOf and T.Team.GearOf("Player-0-B")
  check(type(gb) == "table" and gb.links and gb.links[16] == "item:99520", "Team.GearOf : équipement partagé de Bravo")

  -- combat avec valeurs secrètes (Forever) : rien ne casse
  M.EnterCombat()
  ctx.fire("GROUP_ROSTER_UPDATE")
  ctx.fire("INSPECT_READY", "Player-0-C")
  M.Advance(4, 1)
  M.LeaveCombat()
  M.Advance(2, 1)

  -- module coupé : « oubliez mon équipement », minuteur arrêté
  local before = #sentOf("GGE")
  T:SetModuleEnabled("GroupGear", false)
  M.Advance(2, 0.5)
  check(#sentOf("GGE") == before + 1 and sentOf("GGE")[before + 1].msg:find("~r1;off$"), "module coupé : équipement oublié chez les amis")
  W.Render()
  local after = {}
  for _, r in ipairs(W.lastLay and W.lastLay.rects or {}) do after[#after + 1] = r.label end
  check(not table.concat(after, "\n"):find("Échanges conseillés", 1, true), "module coupé : section absente")
  T:SetModuleEnabled("GroupGear", true)
  M.Advance(3, 0.5)

  -- seul : rien
  M.group = {}
  ctx.fire("GROUP_ROSTER_UPDATE")
  M.Advance(4, 1)
  check(GG._ticker == nil, "seul : minuteur arrêté")

  check(#M.sentChat == chat0 and #M.output == out0, "rien dans le chat")
  check(#M.blocked == blocked0, "aucune action bloquée")
  io.write(("GROUPGEAR inspections=%d tooltipCalls=%d trades=%d\n"):format(#inspected, tipCalls, #trades))
end
