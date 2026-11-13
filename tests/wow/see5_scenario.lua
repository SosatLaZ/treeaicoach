-- Harness scenario (5.0 INTEG) : « CE QUE LE JOUEUR VOIT ». Level-30 hunter, group of 4, WoW: Forever mock, the
-- default profile, real-client geometry. For each moment of play the exact texts on screen are dumped (every visible
-- TreeAI frame with its lines, the strip, the alert, « Maintenant », notes at the top of the screen, item tooltips):
-- login in town, opening bags, merchant, trainer, taking a quest, killing a quest mob, loot, a crowd of 40 players,
-- then quest guidance switched off. Checks only what must never happen: nothing in the chat, no « nil » / raw item
-- number in a line, never two sell / repair cards, never two verdict lines on one item, no blocked action.
-- Fake names only.
return function(M, ctx)
  local T = TreeAI
  local check = ctx.check
  local geo = M.geo
  local UIP = UIParent
  local function out(s) io.write("SEE5 ", tostring(s), "\n") end
  local function plain(s) return (T.PlainText and T.PlainText(tostring(s or "")) or tostring(s or "")):gsub("|T.-|t", ""):gsub("%s+", " ") end
  local chat0, b0, v0 = #M.output, #(M.blocked or {}), #M.violations
  local noteAt = #M.output
  local bad = {}
  local function line(prefix, s)
    s = plain(s)
    if s == "" or s == " " then return end
    out(prefix .. s)
    if s:find("%f[%a]nil%f[%A]") or s:find("objet %d+") or s:find("table: 0x") or s:find("function: 0x") then bad[#bad + 1] = s end
  end
  local function texts(f, acc, depth)
    acc, depth = acc or {}, depth or 0
    if depth > 6 or type(f) ~= "table" then return acc end
    for _, c in ipairs(rawget(f, "__children") or {}) do
      if type(c) == "table" and (c.IsShown == nil or c:IsShown()) then
        if c.__kind == "FontString" then
          local t = c:GetText()
          if type(t) == "string" and t ~= "" then acc[#acc + 1] = t end
        else texts(c, acc, depth + 1) end
      end
    end
    return acc
  end
  local function frames()
    local list = {}
    for i = 1, #M.frames do
      local f = M.frames[i]
      local n = f.GetName and f:GetName()
      if type(n) == "string" and n:find("^TreeAI") and rawget(f, "__parent") == UIP and f:IsShown() then list[#list + 1] = f end
    end
    table.sort(list, function(a, b) return a:GetName() < b:GetName() end)
    return list
  end
  local function screen(title)
    out("")
    out("=== " .. title)
    for _, f in ipairs(frames()) do
      local n = f:GetName()
      if n == "TreeAIStrip" then
        line("  [bande] ", T.Shortcuts and T.Shortcuts.DumpLine and T.Shortcuts.DumpLine() or "?")
      elseif n ~= "TreeAIMinimapButton" then
        local tx = texts(f)
        local r = geo and geo.Rect(f)
        out(("  [%s]%s"):format(n, r and (" %d×%d"):format(r.w, r.h) or ""))
        for i, t in ipairs(tx) do if i <= 14 then line("      ", t) end end
      end
    end
    local okL, now = pcall(T.GetTabLines, T, "now")
    for i, l in ipairs(okL and type(now) == "table" and now or {}) do if i <= 8 then line("  [Maintenant] ", l.text) end end
    for i = noteAt + 1, #M.output do
      local o = tostring(M.output[i])
      if o:find("^%[UIError%]") then line("  [haut de l'écran] ", o:gsub("^%[UIError%]%s*", "")) else line("  [CHAT !] ", o) end
    end
    noteAt = #M.output
  end
  local function tooltip(id, bag, slot, label)
    local lines = {}
    local II = T.modules.ItemInfo
    if II and II.Build then
      local name, _, q, _, _, _, _, _, _, _, sell, cls = T.API.ItemInfo(id)
      local ok, l = pcall(II.Build, { id = id, name = name, classID = cls, quality = q, sellPrice = sell, link = M.ItemLink(id) }, { bag = true, count = T.API.ItemCount(id) }, false)
      for _, x in ipairs(ok and type(l) == "table" and l or {}) do lines[#lines + 1] = plain(x[1]) end
    end
    local Gm = T.Gear
    if Gm and Gm.Score and Gm.ScoreLine then
      local ok, a, b2 = pcall(function() return Gm.ScoreLine(Gm.Score(M.ItemLink(id))) end)
      if ok and a then lines[#lines + 1] = plain(a .. " : " .. tostring(b2)) end
    end
    local tip = T.Bags and T.Bags.TipLine and T.Bags.TipLine(id, bag, slot)
    if tip then lines[#lines + 1] = plain(tip) end
    out("  [infobulle " .. label .. "]")
    local verdicts = 0
    for _, l in ipairs(lines) do
      line("      ", l)
      local low = l:lower()
      if low:find("vends", 1, true) or low:find("vendre", 1, true) or low:find("garde", 1, true) or low:find("en trop", 1, true) then verdicts = verdicts + 1 end
    end
    check(verdicts <= 1, "infobulle " .. label .. " : un seul avis vendre / garder (" .. verdicts .. ")")
  end

  -- ------------------------------------------------------------------ the character
  M.LeaveCombat()
  M.player.level = 30
  M.player.zone, M.player.subzone = "Orgrimmar", "La Vallée de l'Honneur"
  M.player.resting = true
  M.mapNames[1454] = "Orgrimmar"
  M.pos = { mapID = 1454, x = 0.50, y = 0.60 }
  M.skills = { { name = "Travail du cuir", rank = 143, max = 150 }, { name = "Dépeçage", rank = 140, max = 150 } }
  local function item(id, name, t) t.name = name M.AddItem(id, t) end
  item(9101, "Dent ébréchée", { quality = 0, stack = 20, sellPrice = 12 })
  item(9102, "Plastron de plaques terni", { quality = 1, stack = 1, sellPrice = 300, classID = 4, subclassID = 4, equipLoc = "INVTYPE_CHEST" })
  item(9103, "Collier du traqueur", { quality = 2, stack = 1, sellPrice = 900, classID = 4, subclassID = 0, equipLoc = "INVTYPE_NECK", minLevel = 20,
    stats = { ITEM_MOD_AGILITY_SHORT = 6 } })
  item(2319, "Cuir moyen", { stack = 20, sellPrice = 50, classID = 7 })
  item(2604, "Teinture rouge", { stack = 20, sellPrice = 50, classID = 7 })
  item(929, "Potion de soins", { stack = 5, sellPrice = 75, classID = 0, subclassID = 1, minLevel = 12 })
  item(2515, "Flèche acérée", { stack = 200, sellPrice = 0, classID = 6, subclassID = 2 })
  item(2504, "Arc court usé", { stack = 1, sellPrice = 50, classID = 2, subclassID = 2, equipLoc = "INVTYPE_RANGED" })
  M.inventory[18] = { id = 2504, count = 1, dur = 20, durMax = 60 }
  for b = 0, 4 do M.bags[b].slots = {} end
  M.SetBagItem(0, 1, 6948, 1) M.SetBagItem(0, 2, 929, 3) M.SetBagItem(0, 3, 9101, 7) M.SetBagItem(0, 4, 9102, 1)
  M.SetBagItem(0, 5, 9103, 1) M.SetBagItem(0, 6, 2319, 6) M.SetBagItem(0, 7, 2604, 5) M.SetBagItem(1, 1, 2515, 150)
  M.quests = {
    { header = "Les Tarides" },
    { id = 801, title = "La revanche de test", level = 28, complete = true, objectives = {} },
    { id = 803, title = "Plumes de test", level = 28, objectives = { { text = "Plume de test : 2/6", done = false, have = 2, need = 6 } } },
  }
  M.group = { { name = "Amie", class = "PRIEST", level = 28, online = true }, { name = "Compagnon", class = "WARRIOR", level = 29, online = true },
    { name = "Camarade", class = "MAGE", level = 30, online = true } }
  if T.Tour and T.Tour.Store then T.Tour.Store().done = 999 end
  ctx.fire("PLAYER_ENTERING_WORLD", true, false)
  ctx.fire("GROUP_ROSTER_UPDATE") ctx.fire("QUEST_LOG_UPDATE") ctx.fire("SKILL_LINES_CHANGED") ctx.fire("PLAYER_UPDATE_RESTING")
  ctx.fire("BAG_UPDATE", 0) ctx.fire("BAG_UPDATE_DELAYED")
  M.Advance(30, 0.1)
  if T.GetWizard and T:GetWizard() then T:GetWizard():Hide() end
  M.Advance(2, 0.1)
  screen("1. Connexion en ville (Orgrimmar), 30 s après")

  -- ------------------------------------------------------------------ bags
  local Sc = T.Shortcuts
  if Sc and Sc.Do then M.Hw(function() T:UserAction(function() Sc.Do("sacs") end) end) end
  ctx.fire("BAG_OPEN", 0)
  M.Advance(3, 0.1)
  screen("2. Clic sur « Sacs » : les sacs s'ouvrent")
  local B = T.Bags
  if B and B.Lists then
    local L = B.Lists()
    for _, k in ipairs({ "sell", "ah", "use", "bank", "destroy", "keep" }) do
      for _, e in ipairs(L[k]) do line("  [pièce sur la case : " .. k .. "] ", e.name .. " ×" .. e.n .. " — " .. tostring(B.Text(e.v))) end
    end
  end
  tooltip(929, 0, 2, "Potion de soins")
  tooltip(9102, 0, 4, "Plastron de plaques terni")
  tooltip(9103, 0, 5, "Collier du traqueur")
  tooltip(2319, 0, 6, "Cuir moyen")
  tooltip(2604, 0, 7, "Teinture rouge")
  tooltip(9101, 0, 3, "Dent ébréchée")
  ctx.fire("BAG_CLOSED", 0)

  -- ------------------------------------------------------------------ merchant
  M.units.npc = { name = "Marchand-Test" }
  M.merchantItems = { { id = 2515, price = 10, stack = 200, maxStack = 200 } }
  M.SetMerchant(true)
  M.Advance(3, 0.1)
  screen("3. Chez un marchand (qui répare)")
  local cards = 0
  local bc = B and B.GetCard and B.GetCard("merchant")
  if bc and bc:IsShown() then
    cards = cards + 1
    line("  [carte sous le marchand — Sacs] ", (B.cardModel and B.cardModel.label or "?") .. " | " .. tostring(B.cardModel and B.cardModel.info or ""))
  end
  local PLv = T.ProfLevel
  local pc = PLv and PLv.GetCard and PLv.GetCard()
  if pc and pc:IsShown() then
    local tx = texts(pc)
    line("  [carte sous le marchand — Métiers] ", table.concat(tx, " | "))
    local all = table.concat(tx, " | ")
    if all:find("Vendre", 1, true) or all:find("Réparer", 1, true) then cards = cards + 1 end
  end
  check(cards <= 1, "une seule carte « vendre / réparer » chez le marchand (" .. cards .. ")")
  M.SetMerchant(false)
  M.Advance(1, 0.1)

  -- ------------------------------------------------------------------ trainer
  M.units.npc = { name = "Maître-Cuir-Test" }
  M.trainer = { open = false, tradeskill = true, line = "Travail du cuir", rank = 143, services = {
    { name = "Renfort d'armure lourd", cat = "used", req = 100, cost = 1000 },
    { name = "Gants d'homme des collines en cuir", cat = "available", req = 145, cost = 1500 } } }
  M.SetTrainer(true)
  M.Advance(2, 0.1)
  screen("4. Chez le maître de travail du cuir")
  pc = PLv and PLv.GetCard and PLv.GetCard()
  if pc and pc:IsShown() then line("  [carte sous le maître — Métiers] ", table.concat(texts(pc), " | ")) end
  M.SetTrainer(false)
  M.Advance(1, 0.1)

  -- ------------------------------------------------------------------ taking a quest
  M.player.resting = false
  M.player.zone, M.player.subzone = "Les Tarides", "La Croisée"
  M.pos = { mapID = 1413, x = 0.52, y = 0.30 }
  ctx.fire("ZONE_CHANGED_NEW_AREA") ctx.fire("PLAYER_UPDATE_RESTING")
  M.Advance(3, 0.1)
  M.quests[#M.quests + 1] = { id = 804, title = "Sabots de test", level = 29,
    objectives = { { text = "Sabot de test : 0/8", done = false, have = 0, need = 8, type = "item" } } }
  ctx.fire("QUEST_ACCEPTED", #M.quests, 804) ctx.fire("QUEST_LOG_UPDATE") ctx.fire("UNIT_QUEST_LOG_CHANGED", "player")
  M.Advance(4, 0.1)
  screen("5. Une quête prise (« Sabots de test »)")
  local Qm = T.modules.Quests
  local inLog = false
  for _, q in ipairs(M.quests) do if q.id == 804 then inLog = true end end
  check(inLog and T:QuestGen() ~= nil, "la quête prise est dans le journal lu")

  -- ------------------------------------------------------------------ killing a quest mob + loot
  M.EnterCombat() ctx.fire("PLAYER_REGEN_DISABLED")
  M.Advance(4, 0.1)
  M.LeaveCombat() ctx.fire("PLAYER_REGEN_ENABLED")
  M.quests[3].objectives[1] = { text = "Plume de test : 3/6", done = false, have = 3, need = 6 }
  ctx.fire("QUEST_WATCH_UPDATE", 803) ctx.fire("QUEST_LOG_UPDATE") ctx.fire("UNIT_QUEST_LOG_CHANGED", "player")
  M.Advance(3, 0.1)
  screen("6. Un monstre de quête tué (Plume de test 3/6)")
  item(9104, "Griffe émoussée", { quality = 0, stack = 20, sellPrice = 9 })
  ctx.fire("LOOT_READY", true) ctx.fire("LOOT_OPENED", true)
  M.SetBagItem(0, 8, 9104, 2) M.SetBagItem(0, 9, 2319, 2)
  ctx.fire("LOOT_SLOT_CLEARED", 1) ctx.fire("BAG_UPDATE", 0) ctx.fire("LOOT_CLOSED") ctx.fire("BAG_UPDATE_DELAYED")
  M.Advance(3, 0.1)
  screen("7. Butin ramassé (2 Griffe émoussée, 2 Cuir moyen), sacs fermés")

  -- ------------------------------------------------------------------ a crowd of 40 players
  M.player.resting = true
  M.player.zone, M.player.subzone = "Orgrimmar", "La Vallée de la Force"
  M.pos = { mapID = 1454, x = 0.52, y = 0.62 }
  ctx.fire("ZONE_CHANGED_NEW_AREA") ctx.fire("PLAYER_UPDATE_RESTING")
  local CLS = { "WARRIOR", "MAGE", "PRIEST", "ROGUE", "HUNTER", "SHAMAN", "DRUID", "WARLOCK" }
  for i = 1, 40 do
    local u = "nameplate" .. i
    M.units[u] = { name = "Joueur" .. i, isPlayer = true, hostile = false, class = CLS[1 + i % #CLS], level = 20 + i % 40,
      guid = ("Player-1-%08X"):format(50000 + i), health = 2000, healthMax = 2000 }
    ctx.fire("NAME_PLATE_CREATED", u) ctx.fire("NAME_PLATE_UNIT_ADDED", u) ctx.fire("UNIT_AURA", u, { isFullUpdate = true })
    if i % 8 == 0 then M.Advance(0.1, 0.05) end
  end
  M.Advance(6, 0.1)
  screen("8. Foule : 40 joueurs autour de toi en ville")
  local extra = 0
  for i = 1, #M.frames do
    local f = M.frames[i]
    if rawget(f, "__parent") ~= UIP and f.IsShown and f:IsShown() and rawget(f, "_taiPlate") then extra = extra + 1 end
  end
  line("  [barres de nom] ", extra .. " repère(s) TreeAI posé(s) sur les 40 barres de nom")
  for i = 1, 40 do local u = "nameplate" .. i ctx.fire("NAME_PLATE_UNIT_REMOVED", u) M.units[u] = nil end
  M.Advance(2, 0.1)

  -- ------------------------------------------------------------------ « zone choisie oubliée » ? the player picks a zone
  local QB, Zp = T.QuestBrain, T.modules.ZonePlan
  local picked
  if QB and QB.PickZone and Zp and Zp.char then
    picked = QB.PickZone(1413, "player")
    M.Advance(2, 0.1)
    local zn = QB.ZoneNow and QB.ZoneNow() or nil
    local ztxt, zby = type(zn) == "table" and zn.label or nil, type(zn) == "table" and (zn.who or zn.by) or nil
    line("  [zone choisie] ", tostring(ztxt or "?") .. " — choisie par : " .. tostring(zby or "?"))
    check(picked and Zp.char.mode == "zones" and Zp.char.zones[1413] and Zp.char.zoneBy == "player", "zone choisie par le joueur : gardée")
  end

  -- ------------------------------------------------------------------ quest guidance off
  SlashCmdList["TREEAI"]("quetes off")
  M.Advance(3, 0.1)
  screen("9. Guidage des quêtes coupé (/tai quetes off)")
  for name in pairs(T.QUEST_GUIDE or {}) do
    local m = T.modules[name]
    check(not (m and m._active), "guidage coupé : « " .. name .. " » est arrêté")
  end
  local okN, now = pcall(T.GetTabLines, T, "now")
  for _, l in ipairs(okN and type(now) == "table" and now or {}) do
    local s = plain(l.text)
    check(not (s:find("Arrêt %d") or s:find("Trajet", 1, true) or s:find("Rendre «", 1, true)), "guidage coupé : plus de conseil de quête (« " .. s .. " »)")
  end
  for _, n in ipairs({ "QuestWatchFrame", "ObjectiveTrackerFrame", "WatchFrame" }) do
    local f = rawget(_G, n)
    if type(f) == "table" and geo then check(f:IsShown() ~= false or true, "suivi du jeu intact") end
  end
  SlashCmdList["TREEAI"]("quetes on")
  M.Advance(3, 0.1)
  -- the chosen zone survives the switch, a zone change, a loading screen and a quest turned in elsewhere
  if picked then
    M.player.zone = "Orgrimmar" M.pos = { mapID = 1454, x = 0.5, y = 0.6 }
    ctx.fire("ZONE_CHANGED_NEW_AREA") ctx.fire("PLAYER_ENTERING_WORLD", false, false)
    M.Advance(8, 0.2)
    ctx.fire("QUEST_LOG_UPDATE") ctx.fire("GROUP_ROSTER_UPDATE")
    M.Advance(8, 0.2)
    local zn = QB.ZoneNow and QB.ZoneNow() or nil
    local ztxt, zby = type(zn) == "table" and zn.label or nil, type(zn) == "table" and (zn.who or zn.by) or nil
    line("  [zone choisie, après guidage coupé / rallumé + changement de zone + écran de chargement] ",
      tostring(ztxt or "?") .. " — choisie par : " .. tostring(zby or "?"))
    check(Zp.char.mode == "zones" and Zp.char.zones[1413] and Zp.char.zoneBy == "player", "zone choisie : pas oubliée (mode " .. tostring(Zp.char.mode) .. ")")
  end
  -- « quête prise » : the accepted quest is known, a quest that is not in the log is not
  local Qm2 = T.modules.Quests
  if Qm2 and Qm2.snapshot then
    local has804, has999 = false, false
    for _, q in ipairs(type(Qm2.snapshot) == "table" and Qm2.snapshot.quests or {}) do
      if q.questID == 804 or q.id == 804 then has804 = true end
      if q.questID == 999 or q.id == 999 then has999 = true end
    end
    line("  [quête prise] ", "« Sabots de test » dans le relevé du journal : " .. tostring(has804))
    check(not has999, "une quête absente du journal n'est pas « prise »")
  end

  -- ------------------------------------------------------------------ hygiene
  out("")
  for i = chat0 + 1, #M.output do
    if not tostring(M.output[i]):find("^%[UIError%]") then out("chat: " .. tostring(M.output[i])) check(false, "rien dans le chat") end
  end
  for _, s in ipairs(bad) do check(false, "ligne à trou (nil / n° d'objet) : " .. s) end
  check(#(M.blocked or {}) == b0, "aucune action bloquée")
  for i = v0 + 1, #M.violations do out("violation: " .. tostring(M.violations[i].kind) .. " " .. tostring(M.violations[i].msg)) end
  check(#M.violations == v0, "aucune violation")
end
