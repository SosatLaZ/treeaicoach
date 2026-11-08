-- Harness scenario: Modules/Speed.lua (death choice, hearthstone timing, bind memory) + Planner `tip` line.
-- lua5.1 tests/wow/harness.lua --allow C_DeathInfo --scenario tests/wow/speed_scenario.lua wow_addon/TreeAI
return function(M, ctx)
  local check = ctx.check
  local T = TreeAI
  local S, P, X = T.Speed, T.Planner, T.Export
  check(S and P and X, "Speed + Planner + Export chargés")
  if not (S and P and X) then return end
  local E = S.Engine

  -- ---------------------------------------------------------------- moteur (nombres exacts)
  check(E.Sickness(9) == 0 and E.Sickness(10) == 60 and E.Sickness(15) == 360 and E.Sickness(20) == 600
    and E.Sickness(60) == 600, "mal de résurrection : 0 / 1 / 6 / 10 / 10 min")
  check(E.Saved(89) == "89 s" and E.Saved(90) == "2 min" and E.Saved(260) == "4 min", "arrondi des gains")
  check(math.abs(E.HearthGain(2100, 210, 7) - 260) < 1e-9, "foyer : 300 s de marche contre 10 + 30 s")
  check(E.HearthText(260) == "Pierre de foyer maintenant : 4 min gagnées", "texte foyer")
  local c, g, sick = E.DeathChoice(25, 1200, 1500, 300, 7, true)
  check(c == "corpse" and math.abs(g - (600 - 2700 / 7)) < 1e-6 and sick == 600,
    "niv 25, combat juste après : le corps (le mal de 10 min coûte plus) " .. tostring(g))
  c, g = E.DeathChoice(25, 1200, 1500, 300, 7, false)
  check(c == "spirit" and math.abs(g - 2400 / 7) < 1e-6, "niv 25, rendre une quête : l'ange gagne 342,9 s")
  c, g, sick = E.DeathChoice(8, 1200, 1500, 300, 7, true)
  check(c == "spirit" and sick == 0 and math.abs(g - 2400 / 7) < 1e-6, "niv 8 : pas de mal, l'ange gagne")
  check(E.DeathText(2400 / 7, 600) == "Gardien des esprits : 6 min gagnées (mal de résurrection 10 min, −25 % durabilité)",
    "texte mort : " .. E.DeathText(2400 / 7, 600))
  check(E.DeathText(2400 / 7, 0) == "Gardien des esprits : 6 min gagnées (−25 % durabilité)", "texte mort niv < 10")

  -- ---------------------------------------------------------------- en jeu (géométrie 30 yd / % en x)
  local Geo = T.Geo
  local oldPlayer, oldRect, oldSteps = Geo.Player, Geo.rects[1424], P.GetActiveSteps
  local pos = { 80, 50 }
  Geo.Player = function() return 1424, pos[1], pos[2] end
  Geo.rects[1424] = { c = 0, x0 = 0, y0 = 0, x1 = 2000, y1 = 3000 }   -- client frame: x0→x1 follows the map y (Geo.World)
  local step = { k = "turn", map = 1424, x = 60, y = 50, label = "Rendre « Test »" }
  P.GetActiveSteps = function() return { step }, 1, 1, "test" end
  local oldCount = rawget(_G, "GetItemCount")
  rawset(_G, "GetItemCount", function(i) if i == 6948 then return 1 end return oldCount and oldCount(i) or 0 end)
  local function sugg(id)
    for _, e in ipairs(T:GetSuggestions() or {}) do if e.id == id then return e end end
  end

  -- auberge mémorisée à la liaison, puis passée à l'export
  M.FireEvent("HEARTHSTONE_BOUND")
  local b = S:Bind()
  check(b and b.map == 1424 and b.x == 80 and b.y == 50, "auberge mémorisée à la liaison")
  local ps = X:PlayerState()
  check(ps.hearth and ps.hearth.map == 1424 and ps.hearth.x == 80 and ps.hearth.y == 50, "auberge dans l'export (planificateur)")

  -- foyer : je suis à 10 %, l'étape à 75 %, l'auberge à 80 % → 1950/7 − (10 + 150/7) = 247 s
  pos = { 10, 50 }
  step.x = 75
  S:Tick()
  local h = sugg("Speed:hearth")
  check(h and h.text == "Pierre de foyer maintenant : 4 min gagnées", "foyer conseillé : " .. tostring(h and h.text))
  step.x = 20                                                   -- étape à côté : rien
  S:Tick()
  check(not sugg("Speed:hearth"), "étape proche : pas de foyer")

  -- mort : fantôme à 50 %, corps à 10 %, étape (rendre) à 60 % → l'ange fait gagner 6 min
  rawset(_G, "C_DeathInfo", { GetCorpseMapPosition = function() return { x = 0.10, y = 0.50 } end })
  M.player.ghost, M.player.dead = true, true
  M.player.level = 25
  T:InvalidatePlayerState()
  pos = { 50, 50 }
  step.x = 60
  S:Tick()
  local d = sugg("Speed:death")
  check(d and d.text == "Gardien des esprits : 6 min gagnées (mal de résurrection 10 min, −25 % durabilité)",
    "mort : ange conseillé : " .. tostring(d and d.text))
  check(not sugg("Speed:hearth"), "fantôme : pas de foyer")
  step.k = "obj"                                                -- combat juste après : le mal coûte trop
  S:Tick()
  check(not sugg("Speed:death"), "combat juste après au niv 25 : course au corps (silence)")
  M.player.ghost, M.player.dead = false, false
  T:InvalidatePlayerState()
  S:Tick()
  check(not sugg("Speed:death"), "vivant : plus de ligne de mort")

  -- Planner : la ligne `tip` de l'appli sous l'étape courante
  P.GetActiveSteps = oldSteps
  local oR, oS, oC = P.route, P.steps, P.cursor
  P.route = { id = "xph", title = "XP/h max", xph = 20000, minutes = 30, verdict = "ok", reasons = {} }
  P.steps = { { k = "pick", q = 999001, map = 1424, x = 60, y = 50, label = "Prendre « Test »",
    tip = "Tue en chemin : 4 Loups gris pour « Test »" } }
  P.cursor = 1
  local found
  for _, l in ipairs(P:Lines()) do if l.id == "plan:tip" then found = l.text end end
  check(found == "Tue en chemin : 4 Loups gris pour « Test »", "astuce affichée sous l'étape : " .. tostring(found))
  local pub = P:GetActiveSteps(1)
  check(pub and pub[1].tip == "Tue en chemin : 4 Loups gris pour « Test »", "astuce dans le contrat du Tracker")
  check(P:StepLabel({ k = "fp" }) == "Prendre le point de vol", "libellé du point de vol")
  P.route, P.steps, P.cursor = oR, oS, oC

  Geo.Player, Geo.rects[1424] = oldPlayer, oldRect
  rawset(_G, "GetItemCount", oldCount)
end
