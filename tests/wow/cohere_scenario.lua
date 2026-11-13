-- Harness scenario (COHERE, after the 3.9.5 screenshot): Shortest Path Forever (fake public API, as used by
-- Modules/Route.lua + Compass.lua) and Questie's tracker are loaded. Asserts: ONE arrow (TreeAI compass hidden
-- while SPF guides), no stale « près de la remise » / « Suite » after QUEST_TURNED_IN, one line per meaning
-- (SPF / Questie own the quest list, « Maintenant » keeps one step header + what they don't show), option
-- « Guidage : TreeAI » gives everything back, pet training points never read as the raw total, panel placed
-- outside the mocked SPF column / Questie tracker / chat / party frames. Fake quests, fake names.
-- lua5.1 tests/wow/harness.lua --allow ShortestPathForever --allow QuestieLoader --scenario tests/wow/cohere_scenario.lua wow_addon/TreeAI
return function(M, ctx)
  local T = TreeAI
  local check = ctx.check
  M.inCombat = false
  if T.ApplyPreset then T:ApplyPreset("normal") end
  if T.SetProfile then T:SetProfile("account") end
  local function nowLines()
    T:MarkDirty()
    M.Advance(1.1)
    return T:GetTabLines("now")
  end
  local function has(lines, pat)
    for _, l in ipairs(lines) do if tostring(l.text):find(pat) then return l end end
  end
  local function count(lines, pat)
    local n = 0
    for _, l in ipairs(lines) do if tostring(l.text):find(pat) then n = n + 1 end end
    return n
  end

  -- ------------------------------------------------------------ fake SPF + Questie
  local cur, sent = nil, {}
  rawset(_G, "ShortestPathForever", { API = {
    version = 1, minor = 1,
    Estimate = function() return 60 end,
    NavigateRoute = function(owner, stops) sent[#sent + 1] = stops cur = owner == "TreeAI" and 1 or nil return true end,
    CurrentStop = function(owner) return owner == "TreeAI" and cur or nil end,
    Ended = function() return cur == nil and "cancelled" or nil end,
    Cancel = function() cur = nil return true end,
    Active = function() return cur ~= nil end,
  } })
  rawset(_G, "QuestieLoader", { ImportModule = function() return nil end })
  local Cm = T.Compass or T:GetModule("Compass")
  if Cm and Cm.ResetSPF then Cm.ResetSPF() end
  -- (3.9.8) défaut : TreeAI seul, même avec SPF chargé ; le joueur peut choisir Suivi Forever
  check(T:GuideMode() == "treeai" and T:ExternalGuide() == nil, "défaut : TreeAI guide seul, SPF ignoré")
  T:SetGuideMode("auto")
  check(T:GuideMode() == "auto" and T:ExternalGuide() == "spf", "SPF choisi : guidage confié à Suivi Forever")

  -- ------------------------------------------------------------ 1. one arrow
  local C = T:GetModule("Compass")
  check(C and C.SetWaypoint, "boussole présente")
  C.SetWaypoint(0.5, 0.5, "Complot")
  local st = C.Refresh()
  check(st and st.dup == nil, "SPF inactif : la boussole TreeAI guide (" .. tostring(st and st.dup) .. ")")
  cur = 1                                           -- SPF suit le trajet envoyé par TreeAI (« Arrêt 1 sur 9 »)
  st = C.Refresh()
  check(st and st.dup == "spf", "SPF guide : boussole TreeAI cachée, une seule flèche")
  local fr = _G.TreeAICompassFrame
  -- (ZONEFIX) une seule flèche : la boussole s'efface, réduite à une ligne qui dit pourquoi (« Suivi Forever te guide »)
  if fr then
    local wd = C.Widgets and C.Widgets()
    check(not fr:IsShown() or (wd and wd.mini and fr:GetHeight() < 40 and tostring(wd.tag:GetText()):find("Suivi Forever", 1, true)),
      "cadre de la boussole effacé (une ligne : « Suivi Forever te guide »)")
  end
  cur = nil
  C.ClearWaypoint()

  -- ------------------------------------------------------------ 2. stale quest lines after QUEST_TURNED_IN
  local Q = T:GetModule("Quests")
  M.quests = {
    { header = "Contreforts" },
    { id = 7101, title = "Protéger le guide", level = 28, complete = true, xp = 2000, objectives = {} },
    { id = 7102, title = "Oeufs de wyverne", level = 29, complete = false, xp = 2500,
      objectives = { { text = "Oeuf de wyverne : 0/10", done = false, have = 0, need = 10 } } },
  }
  Q.Update()
  -- (QUESTQA) « près de la remise » est la ligne du journal SANS trajet (avec un trajet, l'arrêt le dit déjà)
  local PL = T.Planner
  local oldAS = PL and PL.GetActiveSteps
  if PL then PL.GetActiveSteps = function() return nil end end
  Q.last.turnIn[1].dist = 50                        -- à 50 m du PNJ
  Q.CheckNear()
  check(T._suggestions["Quests:near"], "près de la remise proposé")
  -- the quest is turned in: the log still shows it for a moment (race) → no line about it any more
  ctx.fire("QUEST_TURNED_IN", 7101, 2000, 0)
  check(not T._suggestions["Quests:near"], "QUEST_TURNED_IN : « près de la remise » effacé tout de suite")
  for _, q in ipairs(Q.last.turnIn) do check(q.id ~= 7101, "quête rendue absente du relevé même si le journal traîne") end
  Q.CheckNear()
  check(not T._suggestions["Quests:near"], "pas de retour de la ligne périmée")
  if PL then PL.GetActiveSteps = oldAS end
  table.remove(M.quests, 2)
  ctx.fire("QUEST_REMOVED", 7101)
  M.Advance(1)
  -- « Suite : X » never names a quest already in the log
  local qd = Q.QuestieNext and select(2, pcall(Q.QuestieNext, 1))
  check(qd == nil or not tostring(qd):find("Oeufs de wyverne"), "pas de « Suite » vers une quête déjà prise")
  check(Q.InLog(7102) and not Q.InLog(7101), "InLog suit le journal")

  -- ------------------------------------------------------------ 3. one line per meaning in « Maintenant »
  -- (5.1 INTEG) the gear plan of the login (sliced, one step per free image) publishes its own « Gear:* » lines when it
  -- ends: let it settle first, so the line injected below is not replaced by the module's real answer
  for _ = 1, 60 do if not (T.Gear and T.Gear.Busy and T.Gear.Busy()) then break end M.Advance(0.5, 0.05) end
  if T.ApplyPreset then T:ApplyPreset("tout") end   -- 8 lignes : on voit tout ce qui passe le filtre
  T:Suggest("Quests:prog:7102", "Oeuf de wyverne : 1/10", 2, { ttl = 60 })
  T:Suggest("Quests:near", "Tu es près de la remise de « Oeufs de wyverne ».", 2, { ttl = 60 })
  T:Suggest("Quests:chain", "Suite : « Oeufs de wyverne » chez Garde", 2, { ttl = 60 })
  T:Suggest("Gear:upgrade", "Équipe Brassards du terreau : +21 %", 2, { ttl = 60, onClick = function() end })
  T:Suggest("Fight:ccbreak", "Le mouton va casser", 3, { ttl = 60, danger = true })
  T:Suggest("Leveling:eta", "≈ 14 quêtes avant niv 29 · 666 XP/h", 1, { ttl = 60 })
  T:SetTabLines("quests", { { text = "Étape : finir « Oeufs de wyverne »", prio = 1, pin = true } }, "Quests")
  local function stepPins(lines)
    local n, first = 0, nil
    for _, l in ipairs(lines) do
      if l.pin and T.STEP_SOURCES[l.source or ""] then n = n + 1; first = first or l end
    end
    return n, first
  end
  local QL = T:GetTabLines("quests")
  local qn = stepPins(QL)
  check(qn >= 2, "onglet Quêtes complet : étape du trajet + « Étape » (" .. qn .. ")")
  local L = nowLines()
  if os.getenv("COHERE_DEBUG") then for _, l in ipairs(L) do io.stderr:write("NOW | ", tostring(l.prio), " | ",
    tostring(l.source), " | ", tostring(l.id), " | ", tostring(l.text), "\n") end end
  check(not has(L, "Oeuf de wyverne : 1/10") and not has(L, "près de la remise") and not has(L, "^Suite"),
    "SPF/Questie présents : objectifs / remise / suite pas répétés dans « Maintenant »")
  check(has(L, "Équipe Brassards") and not has(L, "^Cible suivante"), "équipement gardé, pas de « Cible suivante » (boussole)")
  local n, step = stepPins(L)
  check(n == 1 and step and step.source == "plan", "une seule ligne d'étape : celle du trajet (= arrêt courant de SPF)")
  check(L[1] and L[1].prio == 3, "ordre : danger d'abord (" .. tostring(L[1] and L[1].text) .. ")")
  local okOrder, last = true, 0
  for _, e in ipairs(L) do
    local g = T:PanelSectionOf(e, "now")
    if g < last then okOrder = false end
    last = g
  end
  check(okOrder, "ordre logique : danger → étape → actions → infos")
  check(step and (T:PanelSectionOf(step, "now")) == 2, "l'étape du trajet est l'en-tête « Quête en cours »")
  -- option « Guidage : TreeAI » : TreeAI lines back
  T:SetGuideMode("treeai")
  check(T:ExternalGuide() == nil, "Guidage : TreeAI")
  L = nowLines()
  check(has(L, "près de la remise") or has(L, "1/10") or has(L, "^Suite"), "Guidage TreeAI : lignes de quête de TreeAI rendues")
  check(stepPins(L) == 1, "toujours une seule ligne d'étape épinglée")
  T:SetGuideMode("auto")
  for _, id in ipairs({ "Quests:prog:7102", "Quests:near", "Quests:chain", "Gear:upgrade", "Fight:ccbreak", "Leveling:eta" }) do
    T:ClearSuggestion(id)
  end
  if T.ApplyPreset then T:ApplyPreset("normal") end

  -- ------------------------------------------------------------ 3b. never « Rendre » a quest that is not in MY log
  -- (stale plan from the app / another character / a group member's quest): no step, no SPF stop, no target
  local P, R = T.Planner, T.Route
  if P and P.Evaluate then
    local steps = {
      { k = "turn", q = 9901, map = 1424, x = 40, y = 40, label = "Rendre « Fantôme »" },        -- pas dans le journal
      { k = "obj", q = 9902, o = 1, map = 1424, x = 41, y = 40, label = "Tue « Absent »" },       -- pas dans le journal
      { k = "turn", q = 7102, map = 1424, x = 42, y = 40, label = "Rendre « Oeufs de wyverne »" }, -- dans le journal, pas finie
      { k = "pick", q = 9903, map = 1424, x = 43, y = 40, label = "Prendre « Nouvelle »" },
      { k = "turn", q = 9903, map = 1424, x = 44, y = 40, label = "Rendre « Nouvelle »" },        -- après sa prise : futur
    }
    P.route = { id = "auto", title = "Plan", steps = steps, verdict = "ok" }
    P.steps, P.cursor, P.log = steps, 1, {}
    local cur = P:Evaluate()
    local cs = cur and steps[cur]
    check(cs and cs.q == 9903 and cs.k == "pick", "étape courante = la prise, jamais « Rendre » d'une quête absente ("
      .. tostring(cs and cs.label) .. ")")
    check(steps[1]._absent and steps[2]._absent and steps[3]._absent and not steps[5]._absent,
      "rendre / objectif d'une quête absente ou pas finie : sautés ; rendre après sa prise : gardé")
    check(P.stale, "plan marqué périmé (à recalculer par l'appli)")
    if R and R.SPFStops then
      for _, st in ipairs(R:SPFStops()) do
        check(st.questID ~= 9901 and st.questID ~= 9902 and not (st.questID == 7102 and st.kind == "turnin"),
          "aucun arrêt SPF « rendre » une quête absente / pas finie")
      end
    end
    if P.Render then P._sig = nil; pcall(P.Render, P) end
    T:MarkDirty()
    M.Advance(1.1)
    for _, l in ipairs(T:GetTabLines("quests")) do
      check(not tostring(l.text):find("Fantôme") and not tostring(l.text):find("Absent"),
        "aucune ligne pour une quête absente du journal : " .. tostring(l.text))
    end
    P.route, P.steps, P.cursor = nil, nil, nil
  end
  local Cmp = T:GetModule("Compass")
  for _, t in ipairs(Cmp.Rebuild("force") or {}) do
    check(t.questID ~= 9901 and not (tostring(t.label):find("^Rendre") and t.questID ~= 7101),
      "boussole : pas de cible « Rendre » hors journal (" .. tostring(t.label) .. ")")
  end
  -- group member's quest, not mine: labelled with the member, never « Rendez ensemble »
  local ME = T.Missions and T.Missions.Engine
  if ME and ME.Next then
    local nx = ME.Next({ { id = 9901, title = "Fantôme", multi = true, allDone = true, holders = { "Bravo", "Charlie" },
      done = { "Bravo", "Charlie" } } })
    check(nx and nx.kind ~= "turnin" and nx.text:find("^Bravo, Charlie peuvent rendre"), "quête des potes : nommée, pas « à toi » ("
      .. tostring(nx and nx.text) .. ")")
  end

  -- ------------------------------------------------------------ 4. pet training points
  local API = T.API
  M.pet.exists, M.pet.tp, M.pet.tpUsed = true, 112, 0
  check(API.PetFreeTrainingPoints() == nil, "« 112 libres » avec 0 utilisé = total brut : pas fiable, silence")
  M.pet.tp, M.pet.tpUsed = 15, 97
  check(API.PetFreeTrainingPoints() == 15, "libres = total − utilisés")
  M.pet.tp, M.pet.tpUsed = 5, nil

  -- ------------------------------------------------------------ 5. placement outside SPF / Questie / chat / party
  UIParent:SetSize(2000, 1125)
  local function fake(name, l, b, w, h, parent)
    local f = CreateFrame("Frame", name, parent or UIParent)
    f.GetRect = function() return l, b, w, h end
    if name then _G[name] = f end
    return f
  end
  local real = {
    { "DetailsBaseFrame1", 0, 995, 375, 130 },
    { "PartyMemberFrame1", 20, 925, 115, 55 },
    { "PartyMemberFrame2", 20, 865, 115, 55 },
    { "MinimapCluster", 1750, 863, 245, 262 },
    { "ObjectiveTrackerFrame", 1740, 160, 260, 690 },  -- « Tous les objectifs » (Questie dedans), à droite
    { "Questie_BaseFrame", 1740, 160, 260, 690 },
    { "ChatFrame1", 30, 75, 420, 170 },
    { "MainMenuBar", 435, 0, 1120, 50 },
    { "MultiBarBottomLeft", 730, 250, 525, 45 },
    { "PlayerFrame", 545, 245, 180, 65 },
  }
  for _, r in ipairs(real) do fake(r[1], r[2], r[3], r[4], r[5]) end
  -- SPF : petite ancre, ses lignes pendent dessous (« Suivi Forever / Nettoyer mon journal / Trajet / Composants »)
  local anchor = fake("ShortestPathForeverTracker", 20, 555, 4, 4)
  fake(nil, 20, 470, 240, 90, anchor)
  fake(nil, 20, 330, 240, 140, anchor)
  fake(nil, 20, 200, 240, 130, anchor)
  local spfCol = { name = "Suivi Forever", l = 20, b = 200, w = 240, h = 360 }
  local placed = T:AutoLayout(true)
  check(placed and placed.panel, "panneau placé")
  local function overlaps(a, b)
    return a.l < b.l + b.w and b.l < a.l + a.w and a.b < b.b + b.h and b.b < a.b + a.h
  end
  local obstacles = { spfCol }
  for _, r in ipairs(real) do obstacles[#obstacles + 1] = { name = r[1], l = r[2], b = r[3], w = r[4], h = r[5] } end
  for role, r in pairs(placed) do
    for _, o in ipairs(obstacles) do
      check(not overlaps(r, o), ("%s ne chevauche pas %s (%d,%d %dx%d)"):format(role, o.name, r.l, r.b, r.w, r.h))
    end
  end
  local scanned = T:ScanScreen().named.ShortestPathForeverTracker
  check(scanned and scanned.h >= 300, "colonne Suivi Forever lue (union des lignes)")
  local light = T._LightRects and T._LightRects(2000, 1125) or {}
  local inLight
  for _, r in ipairs(light) do if r.name == "ShortestPathForeverTracker" and r.h >= 300 then inLight = true end end
  check(inLight, "le placement vivant (reflow) voit aussi la colonne SPF")

  rawset(_G, "ShortestPathForever", nil)
  rawset(_G, "QuestieLoader", nil)
  check(T:ExternalGuide() == nil, "sans SPF / Questie : TreeAI guide")
end
