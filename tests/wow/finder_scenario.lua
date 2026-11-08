-- Scenario for Modules/Finder.lua (agent FINDER), run by tests/wow/test_finder.py on two API flavours.
-- Fake beasts / NPCs only (ids 90001+, invented names), fake QuestieDB, fake Shortest Path Forever.
-- lua5.1 tests/wow/harness.lua --class HUNTER --allow ShortestPathForever --scenario tests/wow/finder_scenario.lua wow_addon/TreeAI
return function(M, ctx)
  local T = TreeAI
  local check = ctx.check
  local F, P, R = T.Finder, T.Planner, T.Route
  check(F and F.db and F.db.enabled and T.Data and T.Data.Pets, "Finder + Data/Pets loaded")
  if not (F and T.Data and T.Data.Pets) then return end
  local D = T.Data.Pets
  local function sugg(id) for _, s in ipairs(T:GetSuggestions()) do if s.id == id then return s end end end
  local function has(list, pat) for _, r in ipairs(list or {}) do if tostring(r.title):find(pat) then return r end end end

  -- ------------------------------------------------------------ client facts (generated table)
  check(D.families[2].n == "Félin" and D.families[2].dmg == 10 and D.families[4].hp == 8, "family modifiers from the client")
  check(D.abilities["Morsure"].lv[4] == 24 and D.abilities["Hurlement furieux"].lv[2] == 24, "rank levels from the client")
  check(F.DietText(D.families[2].diet) == "viande, poisson", "diet mask → text: " .. F.DietText(D.families[2].diet))
  check(F.FamilyId("Félin") == 2 and F.FamilyId("Wolf") == 1, "family names FR/EN")

  -- ------------------------------------------------------------ value model (pure)
  local g, parts, info = F.PetValue(D, { n = "Bête-A", fam = 2, lo = 24, hi = 24, teach = { "Griffe", 4 }, atk = 1.0 },
    { level = 28, petFam = 1, known = { Griffe = 3 }, sec = 360 })
  check(g == 18, "gain = +8 Griffe 4, +9 Félin, +12 attaque 1,0 s, −6 niveau, −5 trajet: " .. tostring(g))
  check(parts[2] and parts[1][2] == "attaque 1,0 s" and info.rank == 4 and not info.probable, "parts sorted, explicit rank")
  local found = false
  for _, p in ipairs(parts) do if p[2] == "Griffe rang 4 (ton familier : rang 3)" then found = true end end
  check(found, "why text names the rank gained")
  check(F.PetValue(D, { n = "Trop-haut", fam = 2, lo = 30, hi = 31 }, { level = 28 }) == nil, "beast above my level: not tameable")
  local g2, _, i2 = F.PetValue(D, { n = "Loup-B", fam = 1, lo = 26, hi = 26 }, { level = 26, petFam = 1, known = {}, group = 4 })
  check(i2.ability == "Morsure" and i2.rank == 4 and i2.probable and g2 == 32, "probable rank from the level (Morsure 4): " .. tostring(g2))

  -- ------------------------------------------------------------ live ranking on fake beasts + fake QuestieDB
  rawset(_G, "UnitFactionGroup", function() return "Alliance", "Alliance" end)
  M.mapRects[1424] = M.mapRects[1424] or { top = 1000, left = 2000, w = 3000, h = 2000, continent = 0 }
  T.Geo.rects = {}
  M.pos = { mapID = 1424, x = 0.50, y = 0.40 }
  local NPC = {
    [90001] = { name = "Lynx-test", minLevel = 25, maxLevel = 26, spawns = { [267] = { { 55, 40 } } } },
    [90002] = { name = "Ours-trop-fort", minLevel = 30, maxLevel = 31, spawns = { [267] = { { 52, 40 } } } },
    [90003] = { name = "Hyène-ennemie", minLevel = 24, maxLevel = 25, spawns = { [267] = { { 51, 40 } } } },
    [90004] = { name = "Loup-loin", minLevel = 22, maxLevel = 23, spawns = { [267] = { { 95, 90 } } } },
    [90005] = { name = "Mauvais-id", minLevel = 5, maxLevel = 6, spawns = { [267] = { { 50, 41 } } } },
  }
  local qdb = {
    QueryQuestSingle = function() return nil end,
    QueryNPCSingle = function(id, key) local n = NPC[id] return n and n[key] end,
    QueryObjectSingle = function() return nil end,
  }
  rawset(_G, "QuestieLoader", { _modules = { QuestieDB = qdb, ZoneDB = { GetUiMapIdByAreaId = function(_, a) return a == 267 and 1424 or nil end } } })
  local oldBeasts = D.beasts
  D.beasts = {
    { npc = 90001, n = "Lynx-test", fam = 2, lo = 25, hi = 26, map = 1424, x = 55, y = 40, zone = "Z", fz = "", teach = { "Griffe", 4 }, atk = 1.5, rare = false },
    { npc = 90002, n = "Ours-trop-fort", fam = 4, lo = 30, hi = 31, map = 1424, x = 52, y = 40, zone = "Z", fz = "", rare = false },
    { npc = 90003, n = "Hyène-ennemie", fam = 25, lo = 24, hi = 25, map = 1424, x = 51, y = 40, zone = "Z", fz = "H", rare = false },
    { npc = 90004, n = "Loup-loin", fam = 1, lo = 22, hi = 23, map = 1424, x = 95, y = 90, zone = "Z", fz = "", rare = false },
    { npc = 90005, n = "Mauvais-id", fam = 2, lo = 24, hi = 25, map = 1424, x = 50, y = 41, zone = "Z", fz = "", rare = false },
  }
  for k in pairs(F._spots) do F._spots[k] = nil end
  local ranked = F.RankPets(5)
  check(ranked[1] and ranked[1].b.n == "Lynx-test" and ranked[1].sec and ranked[1].sec < 60, "best pet = the near cat with Griffe 4")
  local names = {}
  for _, e in ipairs(ranked) do names[e.b.n] = true end
  check(not names["Ours-trop-fort"] and not names["Hyène-ennemie"] and not names["Mauvais-id"] and names["Loup-loin"],
    "above level / enemy-guard zone / wrong NPC id (QuestieDB levels) left out")
  check(F._spots[90001].questie and F._spots[90005].bad, "spawns from QuestieDB, curated id re-checked")

  -- ------------------------------------------------------------ search, tab, « Maintenant », single owner
  local list = F:Refresh(true)
  local pet = has(list, "^Apprivoise Lynx%-test")
  check(pet and pet.map == 1424 and pet.score > 0 and pet.why[1], "search lists the pet with its gain")
  local s = sugg("Finder:now")
  check(s and s.text:match("^Apprivoise Lynx%-test à .- : Griffe 4$"), "Maintenant: one finder line: " .. tostring(s and s.text))
  local tab = T:GetTabLines("finder")
  local ctl, petLine = 0, nil
  for _, l in ipairs(tab) do
    if l.control then ctl = ctl + 1 end
    if tostring(l.text):find("^Apprivoise Lynx%-test") then petLine = l end
  end
  check(petLine and petLine.onClick and type(petLine.tooltip) == "table" and ctl == 2, "tab « Chercher »: lines + 2 buttons")
  check(T.tabs.finder and T.tabs.finder.title == "Chercher", "« Chercher » is a tab of the Plus menu")
  check(F.OwnsPet(), "Finder owns taming for a hunter")
  local Bo, HP = T.modules.Boost, T.modules.HunterPro
  if Bo and Bo.UpdatePet then Bo:UpdatePet() end
  if HP and HP.UpdatePet then HP:UpdatePet() end
  check(not sugg("Boost:pet") and not (HP and HP.Lines and HP.Lines.pet), "Boost / HunterPro taming lines defer to Finder")

  -- ------------------------------------------------------------ query words
  local cats, free = F.ParseQuery("Familier")
  check(cats and cats.pet and not free, "« familier » → pet category")
  cats, free = F.ParseQuery("lynx")
  check(not cats and free == "lynx", "free text filter")
  check(has(F.Search("lynx"), "Lynx") and #F.Search("lynx") == 1, "free text keeps the matching results")

  -- ------------------------------------------------------------ other categories
  local VIG = { ["V-1"] = { vignetteGUID = "V-1", objectGUID = "Creature-0-0-0-0-90100-1", name = "Croc-test", isDead = false,
    atlasName = "VignetteKill", type = 0, vignetteID = 1, onMinimap = true, pos = { 0.56, 0.42 } },
    ["V-2"] = { vignetteGUID = "V-2", objectGUID = "GameObject-0-0-0-0-90200-1", name = "Coffre-test", isDead = false,
    atlasName = "VignetteLoot", type = 0, vignetteID = 2, onMinimap = true, pos = { 0.48, 0.38 } } }
  local oldVig = rawget(_G, "C_VignetteInfo")
  rawset(_G, "C_VignetteInfo", {
    GetVignettes = function() return { "V-1", "V-2" } end,
    GetVignetteInfo = function(id) return VIG[id] end,
    GetVignettePosition = function(id, map) local v = VIG[id] if v and map == 1424 then return { x = v.pos[1], y = v.pos[2] } end end,
  })
  ctx.fire("VIGNETTES_UPDATED")
  M.Advance(2)
  check(has(F.Search("rare"), "^Vise le rare Croc%-test"), "rare from Detect")
  check(has(F.Search("trésor"), "^Ouvre le trésor"), "treasure from Detect")
  M.skills = { { name = "Arcs", rank = 120 }, { name = "Armes à feu", rank = 100 } }
  local wm = has(F.Search("maître"), "^Apprends .* chez ")
  check(wm and wm.stopKind == "train", "weapon master for a skill not learned yet: " .. tostring(wm and wm.title))
  local dj = F.Search("donjon")
  check(dj[1] and dj[1].cat == "dungeon" and not dj[1].map, "dungeon for the group (no position)")
  M.inventory[0] = { id = 2515, count = 120 }
  M.items[2515] = M.items[2515] or { name = "Flèche-test" }
  local am = has(F.Search("munitions"), "^Achète des munitions")
  check(am == nil or am.score >= 15, "ammo low → vendor result")

  -- ------------------------------------------------------------ « Ajouter au trajet » (Planner + Route + SPF)
  local sent = {}
  rawset(_G, "ShortestPathForever", { API = { version = 1, minor = 1, Estimate = function() return 60 end,
    NavigateRoute = function(owner, stops) if owner == "TreeAI" then sent[#sent + 1] = stops return true end return false end,
    CurrentStop = function() return 1 end, Ended = function() return nil end, Cancel = function() return true end,
    Active = function() return true end, Subscribe = function() return function() end end } })
  if T.SetGuideMode then T:SetGuideMode("auto") end   -- (3.9.9) SPF is opt-in: this part tests the hand-off
  if T.Compass and T.Compass.ResetSPF then T.Compass.ResetSPF() end
  local steps = {
    { k = "pick", q = 501, map = 1424, x = 60, y = 40, label = "Prendre « Test »" },
    { k = "turn", q = 501, map = 1424, x = 70, y = 40, label = "Rendre « Test »" },
  }
  local route = { id = "auto", title = "Plan auto (en jeu)", steps = steps, verdict = "ok" }
  P.auto, P.route, P.key, P.steps, P.cursor = route, route, "auto:auto", steps, 1
  R.following, R._sentAt, R.char.paused = true, -100, false
  local how = F:AddToRoute(pet)
  local ins
  for i, st in ipairs(P.steps) do if st.finder == pet.key then ins = i end end
  check(how == "route" and ins, "stop inserted into the plan (" .. tostring(how) .. ")")
  local last = sent[#sent] or {}
  local seen = false
  for _, st in ipairs(last) do if st.title == "Apprivoiser « Lynx-test »" then seen = true end end
  check(seen, "route re-sent to Shortest Path with the new stop")
  M.pos = { mapID = 1424, x = 0.55, y = 0.40 }
  P:Evaluate()
  check(P.steps[ins] and P.steps[ins]._done, "arrived at the stop → step done")
  -- no plan: compass instead
  P.auto, P.route, P.steps, P.cursor, P.key = nil, nil, nil, nil, nil
  R.following = nil
  M.pos = { mapID = 1424, x = 0.50, y = 0.40 }
  local r2 = has(F.Search("rare"), "Croc%-test")
  check(r2 and F:AddToRoute(r2) == "compass", "no plan → compass + kept for the next plan")
  local cur = T.Compass and T.Compass.char and T.Compass.char.custom
  check(cur and tostring(cur.name):find("Croc%-test"), "compass target set: " .. tostring(cur and cur.name))
  check(F.GoTo(pet) ~= nil, "« Y aller » = Compass.SetTarget")

  -- ------------------------------------------------------------ learned beast (target, out of combat)
  M.Advance(1)
  M.target = { exists = true, name = "Ours-vu", family = "Ours", level = 24, guid = "Creature-0-0-0-0-90009-0000AB", hostile = true }
  ctx.fire("PLAYER_TARGET_CHANGED")
  local e = F.db.seen and F.db.seen[90009]
  check(e and e.fam == 4 and e.lo == 24 and e.map == 1424, "targeted beast learned (family, level, place)")
  M.target = { exists = false }

  -- ------------------------------------------------------------ window, slash, combat (strict mock)
  F.Show(true)
  check(F.frame and F.frame:IsShown() and F._W.rows[1] and F._W.rows[1].r, "window shows ranked rows")
  local row = F._W.rows[1]
  row.go:GetScript("OnClick")(row.go)
  F.Show(false)
  local out0 = #M.output
  SlashCmdList["TREEAI"]("cherche familier")
  local printed = false
  for i = out0 + 1, #M.output do if M.output[i]:find("1%. Apprivoise") then printed = true end end
  check(printed, "/tai cherche familier prints the top")
  local b0 = #M.blocked
  M.inCombat = true
  check(F:Refresh(true) == nil and F:AddToRoute(pet) == false, "nothing heavy / no route change in combat")
  ctx.fire("UPDATE_MOUSEOVER_UNIT")
  M.inCombat = false
  check(#M.blocked == b0, "0 blocked actions")

  D.beasts = oldBeasts
  rawset(_G, "C_VignetteInfo", oldVig)
  rawset(_G, "QuestieLoader", nil)
  rawset(_G, "ShortestPathForever", nil)
  if T.SetGuideMode then T:SetGuideMode("treeai") end
  if T.Compass and T.Compass.ResetSPF then T.Compass.ResetSPF() end
  for k in pairs(F._spots) do F._spots[k] = nil end
end
