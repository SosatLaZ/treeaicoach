-- ROUTE6 (5.1) — AUDIT5 B8 : « Achète X » from every Questie table that can exist. Real data, nothing stubbed.
-- A quest of the log asks for 5 « Épices fortes », sold by a vendor 30 m from the first stop (QuestieDB: item.vendors → NPC
-- spawns). A second quest asks for « Épices rares », sold by a vendor that has no known place.
-- R6_QUESTIE = lib (the QuestieDB library alone: 5.0.0 said « Ramasse « Épices fortes » », no place) | loader (the old
-- Questie addon alone) | both. No forced rebuild. Run by tests/wow/test_route6.py, profiles « sur » and « tout ».
return function(M, ctx)
  local T = TreeAI
  local check = ctx.check
  local variant = os.getenv("R6_QUESTIE") or "lib"
  local W = dofile("tests/wow/route6_world.lua")(M, ctx, { questie = variant, pre = function(Mk, Wd)
    Wd.NPCS[7700] = Wd.npc("Marchande d'épices", 30, 400, 45.0, 50.0)
    Wd.NPCS[7701] = { name = "Colporteur", minLevel = 30, maxLevel = 30, rank = 0, spawns = {} }      -- no known place
    Wd.ITEMS[8700] = { name = "Épices fortes", vendors = { 7700 } }
    Wd.ITEMS[8701] = { name = "Épices rares", vendors = { 7701 } }
    Wd.QUESTS[6200] = Wd.quest("Ragoût épicé", 29, 25, 400, 7002, 7002, nil, { 8700 })
    Wd.QUESTS[6201] = Wd.quest("Ragoût rare", 29, 25, 400, 7002, 7002, nil, { 8701 })
    Wd.XP[6200], Wd.XP[6201] = 2400, 2400
    Mk.quests[#Mk.quests + 1] = { id = 6200, title = "Ragoût épicé", level = 29, map = 1441, xp = 2400,
      objectives = { Wd.obj("Épices fortes : 0/5", 0, 5, "item") } }
    Mk.quests[#Mk.quests + 1] = { id = 6201, title = "Ragoût rare", level = 29, map = 1441, xp = 2400,
      objectives = { Wd.obj("Épices rares : 0/5", 0, 5, "item") } }
  end })
  if not W then return end
  local P, R, X, Cm = T.Planner, T.Route, T.Export, T.Compass
  W.party(1)
  W.quiet(90)
  W.out(variant .. " : " .. W.stopsText(30))

  -- the one resolver answers in every flavour
  check(X.AreaToMap(400) == 1441 and X.AreaToMap(17) == 1413, variant .. ": Export.AreaToMap(400) = 1441 (got " .. tostring(X.AreaToMap(400)) .. ")")
  local db, zdb = Cm.Questie()
  local pts = {}
  if db then pcall(Cm.SpawnsOf, "npc", 7700, db, zdb, 1441, pts) end
  check(#pts == 1 and pts[1].mapID == 1441 and math.abs(pts[1].x - 0.45) < 1e-6, variant .. ": the compass finds the vendor's spawn (n = " .. #pts .. ")")
  local v = R.VendorForRaw(8700, { map = 1441, x = 46, y = 51 })
  check(v and v.map == 1441 and v.x == 45 and v.name == "Marchande d'épices" and v.src == "questie",
    variant .. ": Route.VendorForRaw (the real one) = the vendor of the item database")

  -- what the route says
  local buy, rare, iBuy
  local n = 0
  for i = P.cursor or 1, #(P.steps or {}) do
    local s = P.steps[i]
    if not s._done and not s._other then
      n = n + 1
      if s.q == 6200 and s.k == "obj" then buy, iBuy = s, n end
      if s.q == 6201 and s.k == "obj" then rare = s end
    end
  end
  local lb = buy and W.strip(P:StepLabel(buy)) or "?"
  check(buy and buy.buy and buy.map == 1441 and buy.x == 45 and buy.y == 50, variant .. ": the stop of « Épices fortes » is AT the vendor (45, 50), got "
    .. tostring(buy and buy.map) .. " " .. tostring(buy and buy.x) .. " " .. tostring(buy and buy.y))
  check(lb == "Achète « Épices fortes » chez Marchande d'épices", variant .. ": label « Achète « Épices fortes » chez Marchande d'épices », got « " .. lb .. " »")
  check(iBuy and iBuy <= 6, variant .. ": the vendor is 30 m from stop 1 — bought on the way (step " .. tostring(iBuy) .. "), not at the end of the route")
  local lr = rare and W.strip(P:StepLabel(rare)) or "?"
  check(lr == "« Épices rares » : à acheter (vendeur inconnu)", variant .. ": vendor without a known place → « « Épices rares » : à acheter (vendeur inconnu) », got « " .. lr .. " »")
  check(rare and not rare.x, variant .. ": no invented place for the unknown vendor")
  local all = W.stopsText(40)
  check(not all:find("Ramasse « Épices", 1, true), variant .. ": never « Ramasse » for a vendor item: " .. all)
  -- the compass / waypoint target of that stop is the vendor
  local st = R:Stops(64)
  local found
  for _, e in ipairs(st) do if e.step == buy and e.place then found = e.place end end
  check(found and found.map == 1441 and math.abs(found.x - 45) < 1e-6, variant .. ": the route entry of the stop carries the vendor's place (waypoint)")
  W.cleanup()
end
