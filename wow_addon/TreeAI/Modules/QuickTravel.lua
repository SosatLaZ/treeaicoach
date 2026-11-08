-- TreeAI — module « Raccourcis » (agent QUICK3) : voyage, groupe et courses en un clic.
--  1 maître de vol : la meilleure destination pour ta prochaine étape (plan, sinon cible de la boussole) brille
--    sur la carte des vols, et un bouton « Prendre ce vol » sous la fenêtre (TakeTaxiNode, sur TON clic).
--    Durées : vols directs des tables client (Data/Travel.lua), enchaînés comme le fait le jeu entre nœuds connus.
--  2 aubergiste : « Lier ta pierre ici » quand cette auberge est nettement plus près de tes prochaines quêtes que
--    ton auberge actuelle (le clic choisit l'option du PNJ ; la confirmation du jeu reste à toi).
--  3 courses groupées : en ville / au repos, ce qu'il reste à faire avant de partir (réparer, vendre les gris,
--    munitions, nourriture / boisson, entraîneur, courrier), une étape par clic (la boussole t'y mène). Les
--    marchands vus sont retenus avec ce qu'ils vendent (munitions / nourriture / boisson) : données du jeu.
--  4 groupe : fenêtre « Tout le monde prêt ? » (PV, mana, munitions, familier de chacun : Squad pour les amis
--    TreeAI, API du jeu pour les autres), boutons du chef (appel « prêt », inviter les amis du dernier groupe,
--    convertir en raid / groupe, mode de butin) et « Point de rencontre » (ta position envoyée au groupe par le
--    protocole unique du module Groupe, ≤ 1 envoi / 15 s ; la boussole des amis s'y pointe).
-- Toute action du jeu part d'un clic du joueur ; rien en combat ; aucun cadre Blizzard modifié ni ancré
-- (nos cadres sont posés sur UIParent à la position lue des fenêtres du jeu). Lua 5.1, aucune globale.
local T = _G.TreeAI
if not (T and T.RegisterModule) then return end

local Q = T:RegisterModule("QuickTravel", {
  title = "Voyage et groupe",
  defaults = { enabled = true, taxi = true, inn = true, errands = true, ready = true, meet = true, vendors = {} },
  charDefaults = { friends = {} },
  options = {
    { key = "taxi", type = "toggle", label = "Maître de vol : meilleure destination + « Prendre ce vol »", section = "Voyage" },
    { key = "inn", type = "toggle", label = "Aubergiste : « Lier ta pierre ici » si l'auberge est mieux placée", section = "Voyage" },
    { key = "errands", type = "toggle", label = "Courses groupées avant de quitter la ville", section = "Courses" },
    { key = "ready", type = "toggle", label = "Fenêtre « Tout le monde prêt ? » et boutons du chef", section = "Groupe" },
    { key = "meet", type = "toggle", label = "Point de rencontre partagé avec le groupe", section = "Groupe" },
  },
})
T.QuickTravel = Q
if type(T.MODULE_INFO) == "table" and not T.MODULE_INFO.QuickTravel then
  T.MODULE_INFO.QuickTravel = { lite = true, desc = "Raccourcis : vols, auberge, courses, outils du groupe" }
end
if type(T.MODULE_TAB) == "table" then T.MODULE_TAB.QuickTravel = T.MODULE_TAB.QuickTravel or "now" end
if type(T.TOPIC_IDS) == "table" then
  T.TOPIC_IDS["QuickTravel:errands"] = "errands"
  T.TOPIC_IDS["QuickTravel:meet"] = "regroup"
end

local floor, max, min, sqrt, huge = math.floor, math.max, math.min, math.sqrt, math.huge
local fmt, tconcat, tsort = string.format, table.concat, table.sort
local WALK, DETOUR, BOARD, MIN_GAIN = 7.0, 1.3, 20, 45
local AMMO_LOW, FOOD_LOW, WATER_LOW, REPAIR_AT = 600, 10, 10, 60
local MAX_VENDOR_MAPS, MAX_VENDORS = 40, 12

-- ------------------------------------------------------------------ utilitaires (appels gardés)
local function g(name) return rawget(_G, name) end
local function pass(ok, ...) if ok then return ... end return nil end
local function try(fn, ...) if type(fn) ~= "function" then return nil end return pass(pcall(fn, ...)) end
local function C(ns, fn) local t = g(ns) return type(t) == "table" and t[fn] or nil end
local function pub(v)
  local is = g("issecretvalue")
  if v ~= nil and is and try(is, v) then return nil end
  return v
end
local function num(v) return tonumber((pub(v))) end
local function str(v) v = pub(v) return type(v) == "string" and v ~= "" and v or nil end
local function opt(k) return not (Q.db and Q.db[k] == false) end
local function inCombat() return T.InCombat and T:InCombat() or false end
local function clock() return num(try(g("GetTime"))) or 0 end
local cut = T.Cut   -- (4.0) Core : n caractères UTF-8 (« … » compris), jamais une lettre accentuée coupée
local function shortName(n) return (tostring(n or ""):gsub("%-.*$", "")) end
local Fmt = T.Fmt or {}
local function money(c) return Fmt.Money and Fmt.Money(c) or (floor((c or 0) / 10000) .. " po") end
local function dur(s) return Fmt.Time and Fmt.Time(s) or (floor((s or 0) / 60 + 0.5) .. " min") end
local function travel() return T.Data and T.Data.Travel or nil end
local function geo() local Cp = T.Compass return type(Cp) == "table" and type(Cp.Geo) == "table" and Cp.Geo or nil end
local function detect() return type(T.Detect) == "table" and T.Detect or nil end
local function distText(yd)
  local D = detect()
  if D and D.Dist then return D.Dist(yd) end
  local m = (yd or 0) * 0.9144
  return m >= 1000 and (fmt("%.1f km", m / 1000):gsub("%.", ",")) or (floor(m + 0.5) .. " m")
end

-- position du joueur : { mapID, x, y } (0..1)
local function me()
  local D = detect()
  if D and D.Me then return D.Me() end
  local map = num(try(C("C_Map", "GetBestMapForUnit"), "player"))
  local p = map and try(C("C_Map", "GetPlayerMapPosition"), map, "player")
  local x, y
  if type(p) == "table" then
    if type(p.GetXY) == "function" then x, y = try(p.GetXY, p) else x, y = p.x, p.y end
  end
  x, y = num(x), num(y)
  if map and x and y and (x > 0 or y > 0) then return { mapID = map, x = x, y = y } end
end
-- point monde { c, n, w } d'un point de carte en % (même repère que Data/Travel.lua et Route)
local function worldPct(map, x, y)
  local Gx = T.Geo
  if not (Gx and Gx.World and map and x and y) then return nil end
  local n, w, c = Gx.World(map, x, y)
  if not n then return nil end
  return { c = c, n = n, w = w, map = map, x = x, y = y }
end
local function walkSec(a, b)
  if not (a and b) or a.c ~= b.c then return huge end
  return sqrt((a.n - b.n) ^ 2 + (a.w - b.w) ^ 2) * DETOUR / WALK
end
Q.WalkSec = walkSec

-- ================================================================== 1. maître de vol
--- Durées de vol depuis `src` vers tous les nœuds de `allowed` (set d'IDs), vols directs `edges` = { {de, vers, s} }
--- enchaînés (un vol à plusieurs sauts passe par des nœuds connus, comme le jeu). Dijkstra, ≤ 80 nœuds. Pure.
function Q.FlightTimes(src, edges, allowed)
  local adj = {}
  for _, e in ipairs(edges or {}) do
    local a, b, s = e[1], e[2], tonumber(e[3])
    if s and allowed[a] and allowed[b] then
      adj[a] = adj[a] or {}
      if not adj[a][b] or s < adj[a][b] then adj[a][b] = s end
    end
  end
  local dist, done = { [src] = 0 }, {}
  for _ = 1, 80 do
    local u, best = nil, huge
    for k, d in pairs(dist) do if not done[k] and d < best then u, best = k, d end end
    if not u then break end
    done[u] = true
    for v, s in pairs(adj[u] or {}) do
      if not dist[v] or best + s < dist[v] then dist[v] = best + s end
    end
  end
  dist[src] = nil
  return dist
end

--- Meilleur vol vers `dest` ({c,n,w}). cands = { {slot, id, name, cost, pos = {c,n,w}} }, here = position (monde)
--- du joueur, fly = { [id] = secondes }. → meilleur candidat (champs fly, walk, total, gain) ou nil si marcher
--- (ou ne pas voler) est aussi bien. Pure.
function Q.BestFlight(cands, dest, here, fly)
  if not (dest and type(cands) == "table") then return nil end
  local base = here and walkSec(here, dest) or huge
  local best
  for _, c in ipairs(cands) do
    local f = c.id and fly and fly[c.id]
    if f and c.pos then
      local w = walkSec(c.pos, dest)
      local total = f + BOARD + w
      if total < huge and (not best or total < best.total) then
        best = { slot = c.slot, id = c.id, name = c.name, cost = c.cost, fly = f, walk = w, total = total }
      end
    end
  end
  if not best then return nil end
  best.gain = base - best.total
  if base < huge and best.gain < MIN_GAIN then return nil end
  return best
end

-- destination : prochaine étape positionnée du plan (Route), sinon cible de la boussole
function Q.Destination()
  -- (GROUPJOIN) « Rejoindre » en cours : le meilleur vol est celui qui rapproche de l'ami / du point de rencontre
  local J = T.GroupJoin
  local jd = type(J) == "table" and type(J.Destination) == "function" and try(J.Destination) or nil
  if type(jd) == "table" and jd.c and jd.n and jd.w then return jd end
  local R = T.Route
  if type(R) == "table" and type(R.Stops) == "function" and not (R.char and R.char.paused) then
    local st = try(R.Stops, R, 3)
    for _, e in ipairs(type(st) == "table" and st or {}) do
      if e.kind == "quest" and type(e.place) == "table" and e.place.c then
        local P = T.Planner
        local label = P and type(P.StepLabel) == "function" and e.step and try(P.StepLabel, P, e.step)
        return { c = e.place.c, n = e.place.n, w = e.place.w, name = str(label) or "ta prochaine étape" }
      end
    end
  end
  local Cp = T.Compass
  local t = type(Cp) == "table" and type(Cp.Current) == "function" and try(Cp.Current) or nil
  if type(t) == "table" and t.mapID and t.x and t.y then
    local w = worldPct(t.mapID, t.x * 100, t.y * 100)
    if w then w.name = str(t.label) or str(t.title) or "la cible de la boussole" return w end
  end
  return nil
end

-- nœuds de la carte des vols ouverte : { slot, id, name, cost, state = "CURRENT"|"REACHABLE"|… }
local STATE_NAME = { [0] = "CURRENT", [1] = "REACHABLE", [2] = "UNREACHABLE" }
function Q.ReadTaxi()
  local out = {}
  local map = num(try(g("GetTaxiMapID")))
  if not map then
    local cont = me()
    map = cont and cont.mapID
  end
  local nodes = map and try(C("C_TaxiMap", "GetAllTaxiNodes"), map)
  for _, nd in ipairs(type(nodes) == "table" and nodes or {}) do
    if type(nd) == "table" and num(nd.slotIndex) then
      out[#out + 1] = { slot = num(nd.slotIndex), id = num(nd.nodeID), name = str(nd.name),
        state = STATE_NAME[num(nd.state) or -1] }
    end
  end
  if #out == 0 then
    local k = num(try(g("NumTaxiNodes"))) or 0
    for i = 1, min(k, 120) do
      out[#out + 1] = { slot = i, name = str(try(g("TaxiNodeName"), i)), state = str(try(g("TaxiNodeGetType"), i)) }
    end
  end
  -- IDs manquants (vieille API) : par le nom dans les tables client
  local D = travel()
  for _, nd in ipairs(out) do
    if not nd.id and nd.name and D and D.taxi then
      local low = nd.name:lower()
      for id, r in pairs(D.taxi) do if tostring(r[1]):lower() == low then nd.id = id break end end
    end
    nd.cost = num(try(g("TaxiNodeCost"), nd.slot))
  end
  return out
end

function Q.PlanFlight()
  local D = travel()
  if not D then return nil end
  local dest = Q.Destination()
  if not dest then return nil end
  local nodes = Q.ReadTaxi()
  local cur, allowed, cands = nil, {}, {}
  for _, nd in ipairs(nodes) do
    if nd.state == "CURRENT" then cur = nd end
    if nd.id and (nd.state == "CURRENT" or nd.state == "REACHABLE") then allowed[nd.id] = true end
  end
  if not (cur and cur.id) then return nil end
  for _, nd in ipairs(nodes) do
    local r = nd.id and D.taxi and D.taxi[nd.id]
    if nd.state == "REACHABLE" and r then
      cands[#cands + 1] = { slot = nd.slot, id = nd.id, name = nd.name or r[1], cost = nd.cost,
        pos = { c = r[2], n = r[3], w = r[4] } }
    end
  end
  local r0 = D.taxi and D.taxi[cur.id]
  local here = r0 and { c = r0[2], n = r0[3], w = r0[4] } or nil
  local best = Q.BestFlight(cands, dest, here, Q.FlightTimes(cur.id, D.flights, allowed))
  if best then best.dest = dest end
  return best
end

-- ------------------------------------------------------------------ cadres (sur UIParent, jamais ancrés au jeu)
local card, glow
local function uiScale() local U = g("UIParent") return U and num(try(U.GetEffectiveScale, U)) or 1 end
local function shownFrame(f) return type(f) == "table" and try(f.IsVisible, f) and true or false end
local function center(f)
  local x, y = try(f.GetCenter, f)
  x, y = num(x), num(y)
  if not (x and y) then return nil end
  local k = (num(try(f.GetEffectiveScale, f)) or 1) / max(0.01, uiScale())
  return x * k, y * k
end
local function bottomOf(f)
  local b = num(try(f.GetBottom, f))
  if not b then return nil end
  return b * (num(try(f.GetEffectiveScale, f)) or 1) / max(0.01, uiScale())
end
local function newFrame(kind, parent)
  local cf = g("CreateFrame")
  if type(cf) ~= "function" then return nil end
  return try(cf, kind, nil, parent or g("UIParent"))
end

local function ensureCard()
  if card then return card end
  local Th = T.Theme
  local f = Th and Th.Frame and Th:Frame(g("UIParent"), "panel", 260, 54) or newFrame("Frame")
  if not f then return nil end
  try(f.SetSize, f, 260, 54)
  try(f.SetFrameStrata, f, "FULLSCREEN_DIALOG")
  try(f.SetClampedToScreen, f, true)
  f.text = Th and Th.Text and Th:Text(f, "S", "text") or try(f.CreateFontString, f, nil, "OVERLAY", "GameFontHighlightSmall")
  if f.text then
    try(f.text.SetPoint, f.text, "TOPLEFT", f, "TOPLEFT", 8, -6)
    try(f.text.SetPoint, f.text, "TOPRIGHT", f, "TOPRIGHT", -8, -6)
    try(f.text.SetJustifyH, f.text, "LEFT")
    try(f.text.SetWordWrap, f.text, true)
  end
  local function click() if f.onClick and not inCombat() then pcall(f.onClick) end end
  f.btn = Th and Th.Button and Th:Button(f, "", 150, 20, click) or newFrame("Button", f)
  if f.btn then
    try(f.btn.SetSize, f.btn, 150, 20)
    try(f.btn.SetPoint, f.btn, "BOTTOM", f, "BOTTOM", 0, 6)
    if not (Th and Th.Button) then try(f.btn.SetScript, f.btn, "OnClick", click) end
    try(f.btn.SetScript, f.btn, "OnEnter", function(b)
      local tt = g("GameTooltip")
      if not (tt and f.tip) then return end
      try(tt.SetOwner, tt, b, "ANCHOR_RIGHT")
      for i, l in ipairs(f.tip) do
        if i == 1 then try(tt.SetText, tt, l) else try(tt.AddLine, tt, l, 0.9, 0.9, 0.9, true) end
      end
      try(tt.Show, tt)
    end)
    try(f.btn.SetScript, f.btn, "OnLeave", function() local tt = g("GameTooltip") if tt then try(tt.Hide, tt) end end)
  end
  try(f.Hide, f)
  card = f
  return f
end

--- carte d'action collée SOUS une fenêtre du jeu visible (lue, jamais modifiée)
function Q.ShowCard(owner, text, label, onClick, tip)
  if inCombat() then return nil end
  local f = ensureCard()
  if not f then return nil end
  local x, y = nil, nil
  if shownFrame(owner) then
    x = center(owner)
    y = bottomOf(owner)
  end
  try(f.ClearAllPoints, f)
  if x and y then try(f.SetPoint, f, "TOP", g("UIParent"), "BOTTOMLEFT", x, y - 4)
  else try(f.SetPoint, f, "TOP", g("UIParent"), "TOP", 0, -120) end
  if f.text then try(f.text.SetText, f.text, text or "") end
  if f.btn then
    if f.btn.SetLabel then f.btn:SetLabel(label or "") else try(f.btn.SetText, f.btn, label or "") end
  end
  f.onClick, f.tip, f.owner = onClick, tip, owner
  try(f.Show, f)
  return f
end
function Q.HideCard(owner)
  if card and (owner == nil or card.owner == owner or card.kind == owner) then
    card.onClick = nil
    try(card.Hide, card)
  end
end

local function ensureGlow()
  if glow then return glow end
  local f = newFrame("Frame")
  if not f then return nil end
  try(f.SetSize, f, 46, 46)
  try(f.SetFrameStrata, f, "FULLSCREEN_DIALOG")
  try(f.EnableMouse, f, false)
  local tex = try(f.CreateTexture, f, nil, "OVERLAY")
  if tex then
    try(tex.SetAllPoints, tex, f)
    try(tex.SetTexture, tex, "Interface\\Buttons\\UI-ActionButton-Border")
    try(tex.SetBlendMode, tex, "ADD")
    try(tex.SetVertexColor, tex, 1, 0.85, 0.2, 1)
  end
  local ag = try(f.CreateAnimationGroup, f)
  if ag then
    local a = try(ag.CreateAnimation, ag, "Alpha")
    if a then
      try(a.SetFromAlpha, a, 1) try(a.SetToAlpha, a, 0.35) try(a.SetDuration, a, 0.6)
      try(ag.SetLooping, ag, "BOUNCE")
    end
    f.anim = ag
  end
  try(f.Hide, f)
  glow = f
  return f
end

-- position écran d'un nœud de la carte des vols (TaxiFrame ou FlightMapFrame, lecture seule)
function Q.SlotSpot(slot)
  local b = g("TaxiButton" .. tostring(slot))
  if shownFrame(b) then return center(b) end
  local F = g("FlightMapFrame")
  if shownFrame(F) and type(F.dataProviders) == "table" then
    for prov in pairs(F.dataProviders) do
      local pins = type(prov) == "table" and rawget(prov, "slotIndexToPin") or nil
      local pin = type(pins) == "table" and pins[slot] or nil
      if shownFrame(pin) then return center(pin) end
    end
  end
  return nil
end
local function taxiWindow()
  local F = g("FlightMapFrame")
  if shownFrame(F) then return F end
  local Tf = g("TaxiFrame")
  if shownFrame(Tf) then return Tf end
  return nil
end

function Q.UpdateTaxi()
  Q.flight = nil
  if not opt("taxi") or inCombat() then return end
  local best = Q.PlanFlight()
  if not best then Q.HideTaxi() return end
  Q.flight = best
  local x, y = Q.SlotSpot(best.slot)
  local gl = (x and y) and ensureGlow() or nil
  if gl then
    try(gl.ClearAllPoints, gl)
    try(gl.SetPoint, gl, "CENTER", g("UIParent"), "BOTTOMLEFT", x, y)
    try(gl.Show, gl)
    if gl.anim then try(gl.anim.Play, gl.anim) end
  end
  local text = fmt("Meilleur vol pour %s : %s (%s au total, %s de gagné)", cut(best.dest.name, 28), cut(best.name, 26),
    dur(best.total), dur(best.gain))
  local tip = { "Prendre ce vol",
    fmt("Vol : %s, puis %s à pied jusqu'à %s", dur(best.fly + BOARD), dur(best.walk), cut(best.dest.name, 30)) }
  if best.cost and best.cost > 0 then tip[#tip + 1] = "Prix : " .. money(best.cost) end
  tip[#tip + 1] = "Clic : le jeu part vers ce nœud (même effet qu'un clic sur la carte)"
  local f = Q.ShowCard(taxiWindow(), text, "Prendre ce vol", function() Q.TakeFlight() end, tip)
  if f then f.kind = "taxi" end
end
function Q.TakeFlight()
  local b = Q.flight
  if not b or inCombat() then return false end
  local fn = g("TakeTaxiNode")
  if type(fn) ~= "function" then return false end
  local ok = pcall(fn, b.slot)
  Q.HideTaxi()
  return ok
end
function Q.HideTaxi()
  if glow then
    if glow.anim then try(glow.anim.Stop, glow.anim) end
    try(glow.Hide, glow)
  end
  Q.HideCard("taxi")
end

-- ================================================================== 2. aubergiste : lier la pierre ici
--- secondes moyennes de trajet de `from` (point { map, x, y } en %) vers les points `pts` ; fn(a, b) = secondes
local function avgFrom(from, pts, fn)
  if not from then return nil end
  local s, n = 0, 0
  for _, p in ipairs(pts) do
    local v = fn(from, p)
    if v and v < huge then s, n = s + v, n + 1 end
  end
  return n > 0 and s / n or nil
end
--- here / bind = moyennes (s) depuis l'auberge ici / l'auberge liée. → vrai si lier ici fait gagner assez. Pure.
function Q.InnWorth(here, bind)
  if not here then return false end
  if not bind then return true end
  return here < bind * 0.75 and bind - here >= 60
end

-- prochaines quêtes : étapes du plan (positions en %) sinon cibles de la boussole
function Q.NextQuestPoints(n)
  n = n or 6
  local out = {}
  local P = T.Planner
  local steps, cur = P and P.steps, P and P.cursor
  if type(steps) == "table" and cur then
    for i = cur, #steps do
      local s = steps[i]
      if type(s) == "table" and not s._done and not s._other and s.map and s.x and s.y then
        out[#out + 1] = { map = s.map, x = s.x, y = s.y }
        if #out >= n then return out end
      end
    end
  end
  if #out == 0 then
    local Cp = T.Compass
    for _, t in ipairs(type(Cp) == "table" and type(Cp.targets) == "table" and Cp.targets or {}) do
      if type(t) == "table" and t.mapID and t.x and t.y then
        out[#out + 1] = { map = t.mapID, x = t.x * 100, y = t.y * 100 }
        if #out >= n then break end
      end
    end
  end
  return out
end

local function legSec(a, b)
  local R = T.Route
  local s = R and type(R.LegSeconds) == "function" and try(R.LegSeconds, a, b) or nil
  if s then return s end
  return walkSec(worldPct(a.map, a.x, a.y), worldPct(b.map, b.x, b.y))
end

-- l'option « lier » de l'aubergiste : { id, index } ou nil
function Q.BinderOption()
  local opts = try(C("C_GossipInfo", "GetOptions"))
  for i, o in ipairs(type(opts) == "table" and opts or {}) do
    if type(o) == "table" then
      local name = tostring(str(o.name) or ""):lower()
      if o.type == "binder" or num(o.icon) == 132052 or name:find("foyer", 1, true) or name:find("make this inn", 1, true) then
        return { id = num(o.gossipOptionID), index = num(o.orderIndex) or i }
      end
    end
  end
  return nil
end

local function boundHere()
  local bind = str(try(g("GetBindLocation")))
  if not bind then return false, nil end
  local b = bind:lower()
  for _, fn in ipairs({ "GetSubZoneText", "GetMinimapZoneText" }) do
    local z = str(try(g(fn)))
    if z and z:lower() == b then return true, bind end
  end
  return false, bind
end

function Q.UpdateInn()
  if not opt("inn") or inCombat() then return end
  local optn = Q.BinderOption()
  if not optn then Q.HideCard("inn") return end
  local here, bindName = boundHere()
  if here then Q.HideCard("inn") return end
  local pts = Q.NextQuestPoints(6)
  if #pts == 0 then Q.HideCard("inn") return end
  local Gp = T.Geo and T.Geo.Player
  local pm, px, py
  if Gp then pm, px, py = Gp() end
  if not (pm and px) then return end
  local hereAvg = avgFrom({ map = pm, x = px, y = py }, pts, legSec)
  local R = T.Route
  local bp = R and type(R.BindPlace) == "function" and try(R.BindPlace) or nil
  local bindAvg = (type(bp) == "table" and bp.map and bp.x) and avgFrom({ map = bp.map, x = bp.x, y = bp.y }, pts, legSec) or nil
  if not Q.InnWorth(hereAvg, bindAvg) then Q.HideCard("inn") return end
  local text = bindAvg
    and fmt("Lie ta pierre ici : tes %d prochaines quêtes sont à %s en moyenne (contre %s depuis %s)", #pts,
      dur(hereAvg), dur(bindAvg), cut(bindName or "ton auberge", 22))
    or fmt("Lie ta pierre ici : tes %d prochaines quêtes sont à %s en moyenne", #pts, dur(hereAvg))
  local f = Q.ShowCard(g("GossipFrame"), text, "Lier ta pierre ici", function() Q.Bind(optn) end,
    { "Lier ta pierre ici", "Clic : choisit l'option de l'aubergiste", "Le jeu te demande ensuite de confirmer" })
  if f then f.kind = "inn" end
end
function Q.Bind(o)
  if inCombat() or not o then return false end
  local GI = g("C_GossipInfo")
  local ok = false
  if type(GI) == "table" and o.id and type(GI.SelectOption) == "function" then ok = pcall(GI.SelectOption, o.id)
  elseif o.index and type(g("SelectGossipOption")) == "function" then ok = pcall(g("SelectGossipOption"), o.index) end
  Q.HideCard("inn")
  return ok
end

-- ================================================================== 3. courses groupées
-- marchands vus (compte) : vendors[mapID] = { [nom] = { x, y, a = ammo, f = food, w = water, t } }
local consSets
local function consumables()
  if consSets then return consSets end
  consSets = { ammo = {}, food = {}, water = {} }
  local D = T.Data and T.Data.Consumables
  for k, set in pairs(consSets) do
    for _, r in ipairs(D and D[k] or {}) do if type(r) == "table" and r[1] then set[r[1]] = true end end
  end
  return consSets
end
Q.Consumables = consumables

--- itemIDs vendus → tags { a, f, w } (pure)
function Q.SellTags(ids)
  local S = consumables()
  local tags = {}
  for _, id in ipairs(ids or {}) do
    if S.ammo[id] then tags.a = 1 end
    if S.food[id] then tags.f = 1 end
    if S.water[id] then tags.w = 1 end
  end
  return tags
end

function Q.LearnVendor()
  if not Q.db then return nil end
  local p = me()
  local name = str(try(g("UnitName"), "npc"))
  if not (p and name) then return nil end
  local ids = {}
  local k = num(try(g("GetMerchantNumItems"))) or 0
  for i = 1, min(k, 80) do
    local id = num(try(g("GetMerchantItemID"), i))
    if not id then
      local link = str(try(g("GetMerchantItemLink"), i))
      id = link and tonumber(link:match("item:(%d+)"))
    end
    if id then ids[#ids + 1] = id end
  end
  local tags = Q.SellTags(ids)
  if not (tags.a or tags.f or tags.w) then return nil end
  Q.db.vendors = type(Q.db.vendors) == "table" and Q.db.vendors or {}
  local V, key = Q.db.vendors, tostring(p.mapID)
  local list = V[key]
  if type(list) ~= "table" then
    local n = 0 for _ in pairs(V) do n = n + 1 end
    if n >= MAX_VENDOR_MAPS then return nil end
    list = {}
    V[key] = list
  end
  local cnt = 0 for _ in pairs(list) do cnt = cnt + 1 end
  if not list[name] and cnt >= MAX_VENDORS then return nil end
  list[name] = { x = floor(p.x * 1e4) / 1e4, y = floor(p.y * 1e4) / 1e4, a = tags.a, f = tags.f, w = tags.w,
    t = num(try(g("time"))) or 0 }
  return list[name]
end

-- le plus proche marchand retenu qui vend `tag` sur la carte actuelle → entrée façon Detect
function Q.KnownVendor(tag)
  local p = me()
  local V = Q.db and type(Q.db.vendors) == "table" and Q.db.vendors or nil
  local list = p and V and V[tostring(p.mapID)]
  if type(list) ~= "table" then return nil end
  local Gm = geo()
  local best
  for name, e in pairs(list) do
    if type(e) == "table" and e[tag] and tonumber(e.x) and tonumber(e.y) then
      local d, b = nil, nil
      if Gm and Gm.Vector then d, b = Gm.Vector(p, { mapID = p.mapID, x = e.x, y = e.y }) end
      local c = { kind = "vendor", name = name, mapID = p.mapID, x = e.x, y = e.y, dist = d, bearing = b, src = "seen" }
      if not best or (d or 1e12) < (best.dist or 1e12) then best = c end
    end
  end
  return best
end

-- Questie installé (lecture seule) : qui vend ces objets ? itemDB.vendors → PNJ → apparitions sur ta carte.
-- tag = "ammo" | "food" | "water" ; les 4 meilleurs objets de ton niveau ; cache 60 s par carte. → entrée façon Detect
local qvCache = {}
function Q.QuestieVendor(tag)
  local Cp, p = T.Compass, me()
  if not (p and type(Cp) == "table" and type(Cp.Questie) == "function" and type(Cp.SpawnsOf) == "function") then return nil end
  local key = tag .. ":" .. p.mapID
  local c = qvCache[key]
  if c and clock() - c.t < 60 then return c.e end
  local db, zdb = Cp.Questie()
  local best
  if db and type(db.QueryItemSingle) == "function" then
    local lv = num(try(g("UnitLevel"), "player")) or 60
    local fac = ({ Alliance = "A", Horde = "H" })[try(g("UnitFactionGroup"), "player") or ""]
    local list, items = (T.Data and T.Data.Consumables or {})[tag] or {}, {}
    for _, r in ipairs(list) do if type(r) == "table" and (r[2] or 1) <= lv then items[#items + 1] = r end end
    tsort(items, function(a, b) return (a[3] or 0) > (b[3] or 0) end)
    local Gm, seen, budget = geo(), {}, 24
    for i = 1, min(4, #items) do
      local vend = try(db.QueryItemSingle, items[i][1], "vendors")
      for _, npc in ipairs(type(vend) == "table" and vend or {}) do
        if budget <= 0 then break end
        if type(npc) == "number" and not seen[npc] then
          seen[npc], budget = true, budget - 1
          local f = db.QueryNPCSingle and try(db.QueryNPCSingle, npc, "friendlyToFaction")
          if not (fac and type(f) == "string" and not f:find(fac, 1, true)) then
            local pts = {}
            pcall(Cp.SpawnsOf, "npc", npc, db, zdb, p.mapID, pts)
            for _, pt in ipairs(pts) do
              if pt.mapID == p.mapID then
                local d, bg = nil, nil
                if Gm and Gm.Vector then d, bg = Gm.Vector(p, pt) end
                if not best or (d or 1e12) < (best.dist or 1e12) then
                  best = { kind = "vendor", name = pt.name or "Marchand", mapID = pt.mapID, x = pt.x, y = pt.y, dist = d,
                    bearing = bg, src = "questie" }
                end
              end
            end
          end
        end
      end
    end
  end
  qvCache[key] = { t = clock(), e = best }
  return best
end

local function findService(kind)
  if kind == "ammo" then return Q.KnownVendor("a") or Q.QuestieVendor("ammo") or Q.FindService("vendor")
  elseif kind == "food" then
    return Q.KnownVendor("f") or Q.KnownVendor("w") or Q.QuestieVendor("food") or Q.FindService("innkeeper")
  end
  local D = detect()
  return D and D.Nearest and try(D.Nearest, kind) or nil
end
function Q.FindService(kind) local D = detect() return D and D.Nearest and try(D.Nearest, kind) or nil end

local VERB = { repair = "répare", greys = "vends les gris", ammo = "achète des munitions", food = "achète à manger",
  water = "achète à boire", foodwater = "achète à manger et à boire", train = "apprends tes nouveaux sorts",
  mail = "relève ton courrier" }
local KIND_OF = { repair = "repair", greys = "vendor", ammo = "ammo", food = "food", water = "food", foodwater = "food",
  train = "trainer", mail = "mailbox" }
local function cap1(s) return (s:gsub("^%l", string.upper)) end

--- needs = { repair = %, greys = n, ammo = n, food = n, water = n, train = niveau, mail = true }
--- find(kind) → service { name, mapID, x, y, dist, bearing } | nil. → étapes triées { what = {…}, svc, text }. Pure.
function Q.ErrandSteps(needs, find)
  needs = needs or {}
  local wants = {}
  if needs.repair then wants[#wants + 1] = "repair" end
  if needs.greys and needs.greys > 0 then wants[#wants + 1] = "greys" end
  if needs.ammo then wants[#wants + 1] = "ammo" end
  if needs.food and needs.water then wants[#wants + 1] = "foodwater"
  elseif needs.food then wants[#wants + 1] = "food"
  elseif needs.water then wants[#wants + 1] = "water" end
  if needs.train then wants[#wants + 1] = "train" end
  if needs.mail then wants[#wants + 1] = "mail" end
  local steps, byNpc = {}, {}
  for _, w in ipairs(wants) do
    local svc = find and find(KIND_OF[w]) or nil
    if w == "greys" and not svc and steps[1] and steps[1].what[1] == "repair" then svc = steps[1].svc end
    local key = svc and (tostring(svc.mapID) .. ":" .. tostring(svc.name)) or ("?" .. w)
    local s = byNpc[key]
    if s then s.what[#s.what + 1] = w
    else
      s = { what = { w }, svc = svc }
      byNpc[key] = s
      steps[#steps + 1] = s
    end
  end
  tsort(steps, function(a, b)
    local da, db = a.svc and a.svc.dist or 1e12, b.svc and b.svc.dist or 1e12
    if da ~= db then return da < db end
    return a.what[1] < b.what[1]
  end)
  for _, s in ipairs(steps) do
    local verbs = {}
    for _, w in ipairs(s.what) do verbs[#verbs + 1] = VERB[w] end
    local txt = #verbs > 1 and (tconcat(verbs, ", ", 1, #verbs - 1) .. " et " .. verbs[#verbs]) or verbs[1]
    local where = ""
    if s.svc then
      local D = detect()
      where = " chez " .. cut(s.svc.name or "le PNJ", 22)
      if s.svc.dist then
        where = where .. " — " .. distText(s.svc.dist) .. (s.svc.bearing and D and D.Cardinal and (" " .. D.Cardinal(s.svc.bearing)) or "")
      end
    end
    s.text = cap1(txt) .. where
  end
  return steps
end

local function bagCounts()
  local S = consumables()
  local food, water = 0, 0
  local API = T.API or {}
  if type(API.IterBags) ~= "function" then return nil, nil end
  for _, _, it in API.IterBags() do
    local id = type(it) == "table" and it.itemID
    if id then
      if S.food[id] then food = food + (it.count or 1) end
      if S.water[id] then water = water + (it.count or 1) end
    end
  end
  return food, water
end
local function durabilityPct()
  local I = T.modules and T.modules.Inventory
  if I and type(I.Durability) == "function" then return (try(I.Durability)) end
  local low
  for slot = 1, 18 do
    local c, m = try(g("GetInventoryItemDurability"), slot)
    c, m = num(c), num(m)
    if c and m and m > 0 then local p = floor(c * 100 / m) if not low or p < low then low = p end end
  end
  return low
end
local function greyCount()
  local I = T.modules and T.modules.Inventory
  if I and type(I.GreyItems) == "function" then
    local list = try(I.GreyItems)
    return type(list) == "table" and #list or 0
  end
  return 0
end

function Q.Needs()
  local n = {}
  local d = durabilityPct()
  if d and d <= REPAIR_AT then n.repair = d end
  n.greys = greyCount()
  local class = select(2, try(g("UnitClass"), "player"))
  if class == "HUNTER" then
    local a = num(try(g("GetInventoryItemCount"), "player", 0))
    if a and a < AMMO_LOW then n.ammo = a end
  end
  local food, water = bagCounts()
  if food and food < FOOD_LOW then n.food = food end
  local pt = num(try(g("UnitPowerType"), "player"))
  if water and pt == 0 and water < WATER_LOW then n.water = water end
  local sum = T.Summary and try(T.Summary, T)
  local lv = type(sum) == "table" and type(sum.leveling) == "table" and sum.leveling.trainPending or nil
  if lv then n.train = lv end
  if pub(try(g("HasNewMail"))) then n.mail = true end
  return n
end

local errPending = false
function Q.UpdateErrands()
  errPending = false
  if not opt("errands") then Q:Clear("errands") return end
  local st = T.PlayerState and T:PlayerState() or {}
  if inCombat() or st.dead or st.ghost or st.taxi or st.inInstance or not pub(try(g("IsResting"))) then
    Q:Clear("errands") return
  end
  local steps = Q.ErrandSteps(Q.Needs(), findService)
  Q.steps = steps
  if #steps == 0 then Q:Clear("errands") return end
  local s1 = steps[1]
  local head = #steps > 1 and fmt("Courses avant de partir (%d) : ", #steps) or "Avant de partir : "
  local tip = { "Courses groupées" }
  for i, s in ipairs(steps) do tip[#tip + 1] = fmt("%d. %s", i, s.text) end
  tip[#tip + 1] = s1.svc and "Clic : la boussole te mène à la 1re étape ; la suivante s'affiche quand c'est fait"
    or "Parle à un PNJ de ce genre : il sera retenu pour la prochaine fois"
  Q:Suggest("errands", head .. s1.text, 1, { ttl = 0, tab = "now", sound = false, tooltip = tip,
    onClick = s1.svc and function() Q.GoTo(s1.svc) end or nil })
end
function Q.GoTo(svc)
  local D = detect()
  if D and D.Target then return try(D.Target, svc) end
  local Cp = T.Compass
  if type(Cp) == "table" and type(Cp.SetTarget) == "function" then
    return try(Cp.SetTarget, Cp, { mapID = svc.mapID, x = svc.x, y = svc.y, label = cut(svc.name, 40), source = "errands" })
  end
end
local function errandsSoon()
  if errPending then return end
  errPending = true
  if T.After then T:After(1.5, function() pcall(Q.UpdateErrands) end) else pcall(Q.UpdateErrands) end
end
Q.ErrandsSoon = errandsSoon

-- ================================================================== 4. groupe
local MANA = { MAGE = true, PRIEST = true, WARLOCK = true, DRUID = true, PALADIN = true, SHAMAN = true, HUNTER = true }
local PET = { HUNTER = true, WARLOCK = true }

--- r = { class, dead, off, afk, hp, mana, ammo, pet (true/false/nil) } → prêt ?, raison. Pure.
function Q.Verdict(r)
  if r.off then return false, "hors ligne" end
  if r.dead then return false, "mort" end
  if r.afk then return false, "absent" end
  if r.hp and r.hp < 70 then return false, fmt("PV %d %%", r.hp) end
  if r.mana and MANA[r.class] and r.mana < 70 then return false, fmt("mana %d %%", r.mana) end
  if r.class == "HUNTER" and r.ammo and r.ammo < 200 then return false, fmt("%d munitions", r.ammo) end
  if PET[r.class] and r.pet == false then return false, "familier absent" end
  return true, nil
end

local function unitRow(u)
  local name = str(try(g("UnitName"), u))
  if not name then return nil end
  local _, class = try(g("UnitClass"), u)
  local r = { name = name, class = str(class) or "?", unit = u }
  r.off = try(g("UnitIsConnected"), u) == false or nil
  r.dead = pub(try(g("UnitIsDeadOrGhost"), u)) and true or nil
  r.afk = pub(try(g("UnitIsAFK"), u)) and true or nil
  if not inCombat() then
    local h, hm = num(try(g("UnitHealth"), u)), num(try(g("UnitHealthMax"), u))
    if h and hm and hm > 0 then r.hp = floor(h * 100 / hm + 0.5) end
    if num(try(g("UnitPowerType"), u)) == 0 then
      local p, pm = num(try(g("UnitPower"), u, 0)), num(try(g("UnitPowerMax"), u, 0))
      if p and pm and pm > 0 then r.mana = floor(p * 100 / pm + 0.5) end
    end
  end
  if PET[r.class] then r.pet = pub(try(g("UnitExists"), u == "player" and "pet" or (u .. "pet"))) and true or false end
  if u == "player" and r.class == "HUNTER" then r.ammo = num(try(g("GetInventoryItemCount"), "player", 0)) end
  return r
end
local function stRow(name, st)
  local r = { name = name, class = st.c or "?", dead = (st.d or 0) > 0 or nil, afk = (st.af or 0) == 1 or nil,
    hp = st.h, mana = (st.p or -1) >= 0 and st.p or nil, ammo = (st.am or -1) >= 0 and st.am or nil, treeai = true }
  if PET[r.class] and (st.pt or -1) >= 0 then r.pet = st.pt == 1 end
  return r
end

--- une ligne par membre (toi d'abord) : Squad pour les amis TreeAI, API du jeu pour les autres
function Q.MemberRows()
  local rows, seen = {}, {}
  local myName = str(try(g("UnitName"), "player"))
  local mine = unitRow("player")
  if mine then rows[1] = mine seen[mine.name] = true end
  local S = T.Squad
  local list = type(S) == "table" and type(S.MemberList) == "function" and S.IsActive and try(S.IsActive, S)
    and try(S.MemberList, S) or nil
  local squad = {}
  for _, m in ipairs(type(list) == "table" and list or {}) do
    if type(m) == "table" and m.st and m.name then squad[shortName(m.name)] = m end
  end
  if mine and squad[shortName(myName)] then
    local s = squad[shortName(myName)].st
    if not mine.ammo and (s.am or -1) >= 0 then mine.ammo = s.am end
  end
  local raid = try(g("IsInRaid"))
  local n = num(try(g("GetNumGroupMembers"))) or 0
  for i = 1, min(raid and n or n - 1, 40) do
    local u = raid and ("raid" .. i) or ("party" .. i)
    local r = unitRow(u)
    if r and not seen[r.name] then
      seen[r.name] = true
      local m = squad[shortName(r.name)]
      if m then
        local sr = stRow(r.name, m.st)
        sr.off, sr.unit = r.off, u
        if inCombat() or not sr.hp then sr.hp = r.hp end
        r = sr
      end
      rows[#rows + 1] = r
    end
  end
  for _, r in ipairs(rows) do r.ok, r.why = Q.Verdict(r) end
  return rows
end

function Q.RowText(r)
  local bits = {}
  if r.off then bits[#bits + 1] = "hors ligne"
  elseif r.dead then bits[#bits + 1] = "mort"
  else
    if r.hp then bits[#bits + 1] = fmt("PV %d %%", r.hp) end
    if r.mana and MANA[r.class] then bits[#bits + 1] = fmt("mana %d %%", r.mana) end
    if r.class == "HUNTER" and r.ammo then bits[#bits + 1] = (Fmt.Num and Fmt.Num(r.ammo) or tostring(r.ammo)) .. " munitions" end
    if PET[r.class] and r.pet ~= nil then bits[#bits + 1] = r.pet and "familier là" or "sans familier" end
    if r.afk then bits[#bits + 1] = "absent" end
  end
  return shortName(r.name) .. " · " .. (#bits > 0 and tconcat(bits, " · ") or "état inconnu")
end

-- --- actions du chef (toujours sur clic)
local function isLeader() return pub(try(g("UnitIsGroupLeader"), "player")) and true or false end
local function inGroup() return (num(try(g("GetNumGroupMembers"))) or 0) > 0 end
local function pcallNs(ns, fn, ...)
  local f = C(ns, fn) or g(fn)
  if type(f) ~= "function" then return false end
  return pcall(f, ...)
end
function Q.ReadyCheck()
  if inCombat() then return false end
  if inGroup() and (isLeader() or pub(try(g("UnitIsGroupAssistant"), "player"))) then
    return pcallNs("C_PartyInfo", "DoReadyCheck")
  end
  local Mi = T.modules and T.modules.Missions
  if Mi and type(Mi.ReadyAsk) == "function" then return pcall(Mi.ReadyAsk, Mi) end
  return false
end
function Q.FriendsToInvite()
  local out, inG = {}, {}
  local raid = try(g("IsInRaid"))
  local n = num(try(g("GetNumGroupMembers"))) or 0
  for i = 1, min(n, 40) do
    local nm = str(try(g("UnitName"), raid and ("raid" .. i) or ("party" .. i)))
    if nm then inG[shortName(nm)] = true end
  end
  for _, nm in ipairs(Q.char and Q.char.friends or {}) do
    if type(nm) == "string" and not inG[shortName(nm)] then out[#out + 1] = nm end
  end
  return out
end
function Q.InviteFriends()
  if inCombat() or (inGroup() and not isLeader()) then return 0 end
  local k = 0
  for _, nm in ipairs(Q.FriendsToInvite()) do
    if k >= 4 then break end
    if pcallNs("C_PartyInfo", "InviteUnit", nm) then k = k + 1 end
  end
  return k
end
function Q.Convert()
  if inCombat() or not isLeader() then return false end
  if try(g("IsInRaid")) then return pcallNs("C_PartyInfo", "ConvertToParty") end
  return pcallNs("C_PartyInfo", "ConvertToRaid")
end
local LOOT_ORDER = { "group", "freeforall", "roundrobin", "master" }
local LOOT_FR = { group = "groupe", freeforall = "libre", roundrobin = "chacun son tour", master = "maître du butin",
  needbeforegreed = "besoin avant cupidité", personalloot = "personnel" }
local LOOT_ENUM = { [0] = "freeforall", [1] = "roundrobin", [2] = "master", [3] = "group", [4] = "needbeforegreed",
  [5] = "personalloot" }
function Q.LootMethod()
  local m = try(C("C_PartyInfo", "GetLootMethod"))
  if m == nil then m = try(g("GetLootMethod")) end
  if type(m) == "number" then m = LOOT_ENUM[m] end
  return type(m) == "string" and m or nil
end
function Q.CanSetLoot() return (C("C_PartyInfo", "SetLootMethod") or g("SetLootMethod")) ~= nil and Q.LootMethod() ~= nil end
function Q.NextLoot(cur)
  for i, m in ipairs(LOOT_ORDER) do if m == cur then return LOOT_ORDER[i % #LOOT_ORDER == 0 and 1 or i + 1] end end
  return "group"
end
function Q.CycleLoot()
  if inCombat() or not isLeader() or not Q.CanSetLoot() then return nil end
  local nxt = Q.NextLoot(Q.LootMethod())
  local SP = C("C_PartyInfo", "SetLootMethod") or g("SetLootMethod")
  local ok
  if nxt == "master" then ok = pcall(SP, nxt, str(try(g("UnitName"), "player"))) else ok = pcall(SP, nxt) end
  return ok and nxt or nil
end

-- --- point de rencontre : envoyé / reçu par le module Groupe (protocole unique « GRP ») ; ici, les mêmes fonctions
--     pour la fenêtre et /tai rdv (GROUPMERGE, 4.0 : plus de message « QTM » à part ; les QTM des amis encore en 3.9
--     sont décodés par Groupe)
local function groupMod()
  local Gm = T.modules and T.modules.Group
  return (type(Gm) == "table" and type(Gm.SendMeet) == "function" and Gm.IsEnabled and Gm:IsEnabled()) and Gm or nil
end
function Q.EncodeMeet(map, x, y, label)
  return { v = 1, m = floor(map), x = floor(x * 10 + 0.5), y = floor(y * 10 + 0.5), l = cut(label or "", 30) }
end
--- payload reçu → { map, x, y (0..100), label } ou nil (bornes, types). Pure.
function Q.DecodeMeet(p)
  if type(p) ~= "table" or tonumber(p.v) ~= 1 then return nil end
  local m, x, y = tonumber(p.m), tonumber(p.x), tonumber(p.y)
  if not (m and x and y) or m < 1 or m > 100000 or x < 0 or x > 1000 or y < 0 or y > 1000 then return nil end
  local l = type(p.l) == "string" and T.Cut((p.l:gsub("[|%c]", "")), 60, "") or ""
  return { map = floor(m), x = x / 10, y = y / 10, label = l }
end
function Q.SendMeet()
  if not (opt("meet") and inGroup()) then return false, "group" end
  local Gm = groupMod()
  if not Gm then return false, "group" end
  return Gm:SendMeet()
end
--- (compat 3.9.x) un QTM reçu : décodé ici, affiché par Groupe (membre du groupe seulement, ≤ 1 / 10 s)
function Q.OnMeet(payload, sender)
  if not opt("meet") or type(sender) ~= "string" then return nil end
  local Gm = groupMod()
  if not (Gm and Gm.IsMember and Gm:IsMember(sender)) then return nil end
  local m = Q.DecodeMeet(payload)
  if not m then return nil end
  return Gm:OnMeet(cut(shortName(sender), 14), m.map, m.x / 100, m.y / 100, m.label)
end

-- --- fenêtre « Tout le monde prêt ? »
local win, ticker, startTicker
local ICON = { ok = "Interface\\RaidFrame\\ReadyCheck-Ready", no = "Interface\\RaidFrame\\ReadyCheck-NotReady" }
local ROW_H, WIN_W = 20, 340
local function buildWindow()
  if win then return win end
  local Th = T.Theme
  local f = newFrame("Frame")
  if not f then return nil end
  f.name = "TreeAIQuickReady"
  try(f.SetSize, f, WIN_W, 120)
  try(f.SetPoint, f, "CENTER", g("UIParent"), "CENTER", 0, 120)
  try(f.SetFrameStrata, f, "DIALOG")
  try(f.SetClampedToScreen, f, true)
  try(f.EnableMouse, f, true)
  try(f.SetMovable, f, true)
  local hdrH = 0
  if Th and Th.Window then
    Th:Window(f, { id = "quickready", title = "Tout le monde prêt ?", icon = "Interface\\RaidFrame\\ReadyCheck-Ready" })
    hdrH = (Th.HEADER_H or 20) + 4
  end
  f.hdrH = hdrH
  f.sum = Th and Th.Text and Th:Text(f, "M", "gold") or try(f.CreateFontString, f, nil, "OVERLAY", "GameFontNormal")
  if f.sum then
    try(f.sum.SetPoint, f.sum, "TOPLEFT", f, "TOPLEFT", 10, -(hdrH + 6))
    try(f.sum.SetWidth, f.sum, WIN_W - 20)       -- (4.0 QA) dans la fenêtre, jamais par-dessus le monde
    try(f.sum.SetWordWrap, f.sum, false)
  end
  f.rows = {}
  local function btn(label, fn, tip)
    local b = Th and Th.Button and Th:Button(f, label, 100, 20, function() if not inCombat() then pcall(fn) Q.RefreshReady() end end, tip)
    if not b then
      b = newFrame("Button", f)
      if b then try(b.SetSize, b, 100, 20) try(b.SetScript, b, "OnClick", function() if not inCombat() then pcall(fn) Q.RefreshReady() end end) end
    end
    return b
  end
  f.bReady = btn("Appel « prêt »", Q.ReadyCheck, { "Chef : l'appel du jeu ; sinon la vérification TreeAI" })
  f.bMeet = btn("Rendez-vous ici", Q.SendMeet, { "Envoie ta position au groupe (1 fois / 15 s)" })
  f.bInvite = btn("Inviter", Q.InviteFriends, { "Réinvite les amis de ton dernier groupe" })
  f.bConvert = btn("En raid", Q.Convert, { "Chef : convertir le groupe" })
  f.bLoot = btn("Butin", Q.CycleLoot, { "Chef : change le mode de butin (clic suivant = mode suivant)" })
  try(f.Hide, f)
  win = f
  return f
end

function Q.RefreshReady()
  local f = win
  if not (f and try(f.IsShown, f)) then return end
  local rows = Q.MemberRows()
  local okN = 0
  for _, r in ipairs(rows) do if r.ok then okN = okN + 1 end end
  if f.sum then   -- (4.0 QA) le détail est sur chaque ligne dessous : le résumé ne le répète pas
    try(f.sum.SetText, f.sum, okN == #rows and fmt("Tout le monde est prêt (%d/%d)", okN, #rows)
      or fmt("%d/%d prêts : voir ci-dessous", okN, #rows))
  end
  local Th = T.Theme
  local y = -(f.hdrH + 26)
  for i = 1, max(#rows, #f.rows) do
    local r, w = rows[i], f.rows[i]
    if r and not w then
      w = newFrame("Frame", f)
      if w then
        try(w.SetSize, w, WIN_W - 20, ROW_H)
        w.icon = try(w.CreateTexture, w, nil, "ARTWORK")
        if w.icon then try(w.icon.SetSize, w.icon, 14, 14) try(w.icon.SetPoint, w.icon, "LEFT", w, "LEFT", 0, 0) end
        w.text = Th and Th.Text and Th:Text(w, "S", "text") or try(w.CreateFontString, w, nil, "OVERLAY", "GameFontHighlightSmall")
        if w.text then
          try(w.text.SetPoint, w.text, "LEFT", w, "LEFT", 18, 0)
          try(w.text.SetPoint, w.text, "RIGHT", w, "RIGHT", 0, 0)
          try(w.text.SetJustifyH, w.text, "LEFT")
          try(w.text.SetWordWrap, w.text, false)
        end
        f.rows[i] = w
      end
    end
    if w then
      if r then
        try(w.ClearAllPoints, w)
        try(w.SetPoint, w, "TOPLEFT", f, "TOPLEFT", 10, y)
        if w.icon then try(w.icon.SetTexture, w.icon, r.ok and ICON.ok or ICON.no) end
        if w.text then
          local col = Th and Th.ClassColor and Th:ClassColor(r.class)
          try(w.text.SetText, w.text, Q.RowText(r))
          if type(col) == "table" and col[1] then try(w.text.SetTextColor, w.text, col[1], col[2], col[3]) end
        end
        w.row = r
        try(w.Show, w)
        y = y - ROW_H
      else
        try(w.Hide, w)
      end
    end
  end
  -- boutons : ligne 1 pour tous, ligne 2 pour le chef
  y = y - 6
  local function place(b, col, row, show)
    if not b then return end
    if not show then try(b.Hide, b) return end
    try(b.ClearAllPoints, b)
    try(b.SetPoint, b, "TOPLEFT", f, "TOPLEFT", 10 + col * 108, y - row * 24)
    try(b.Show, b)
  end
  local lead = isLeader() or not inGroup()
  place(f.bReady, 0, 0, inGroup())
  place(f.bMeet, 1, 0, inGroup() and opt("meet") and groupMod() ~= nil)
  local inv = #Q.FriendsToInvite()
  if f.bInvite then
    local lbl = inv > 0 and fmt("Inviter (%d)", inv) or "Inviter"
    if f.bInvite.SetLabel then f.bInvite:SetLabel(lbl) else try(f.bInvite.SetText, f.bInvite, lbl) end
  end
  place(f.bInvite, inGroup() and 2 or 0, 0, lead and inv > 0)
  local raid = try(g("IsInRaid")) and true or false
  if f.bConvert then
    local lbl = raid and "En groupe" or "En raid"
    if f.bConvert.SetLabel then f.bConvert:SetLabel(lbl) else try(f.bConvert.SetText, f.bConvert, lbl) end
  end
  place(f.bConvert, 0, 1, inGroup() and isLeader())
  local lm = Q.LootMethod()
  if f.bLoot then
    local lbl = "Butin : " .. (LOOT_FR[lm] or "?")
    if f.bLoot.SetLabel then f.bLoot:SetLabel(lbl) else try(f.bLoot.SetText, f.bLoot, lbl) end
  end
  place(f.bLoot, 1, 1, inGroup() and isLeader() and Q.CanSetLoot() and LOOT_FR[lm] ~= nil)
  try(f.SetHeight, f, -(y - 2 * 24) + 8)
end

function Q.ShowReady(_, on)
  if not opt("ready") then return nil end
  local f = buildWindow()
  if not f then return nil end
  if on == false or (on == nil and try(f.IsShown, f)) then try(f.Hide, f) return f end
  try(f.Show, f)
  Q.RefreshReady()
  startTicker()
  return f
end
function Q.ReadyWindow() return win end

-- amis du dernier groupe (pour « Inviter ») : noms vus dans le groupe, gardés par perso (≤ 4)
function Q.RememberGroup()
  if not Q.char or not inGroup() then return end
  local names = {}
  local raid = try(g("IsInRaid"))
  local n = num(try(g("GetNumGroupMembers"))) or 0
  for i = 1, min(raid and n or n - 1, 40) do
    local u = raid and ("raid" .. i) or ("party" .. i)
    local nm = str(try(g("UnitName"), u))
    local realm = select(2, try(g("UnitName"), u))
    if nm and nm ~= str(try(g("UnitName"), "player")) then
      names[#names + 1] = (type(realm) == "string" and realm ~= "") and (nm .. "-" .. realm) or nm
      if #names >= 4 then break end
    end
  end
  if #names > 0 then Q.char.friends = names end
end

-- onglet « Groupe » : un bouton qui ouvre la fenêtre
function Q.PushTab()
  if not T.SetTabLines then return end
  local lines = {}
  if opt("ready") and (inGroup() or #Q.FriendsToInvite() > 0) then
    lines[1] = { text = "Outils du groupe : prêts, rendez-vous, invitations", label = "Outils du groupe", control = true,
      prio = 1, tooltip = { "Tout le monde prêt ?", "PV, mana, munitions et familier de chacun", "Boutons du chef" },
      onClick = function() Q:ShowReady() end }
  end
  T:SetTabLines("group", lines, "QuickTravel")
end

-- ================================================================== cycle de vie
function startTicker()
  if ticker or not T.NewTicker then return end
  ticker = T:NewTicker(1, function()
    if not (win and try(win.IsShown, win)) then if ticker then ticker:Cancel() ticker = nil end return end
    if not inCombat() then pcall(Q.RefreshReady) end
  end, Q)
end

function Q:OnEnable()
  self.db.vendors = type(self.db.vendors) == "table" and self.db.vendors or {}
  self.char.friends = type(self.char.friends) == "table" and self.char.friends or {}
  self:RegisterEvent("TAXIMAP_OPENED", function() if T.After then T:After(0.1, function() pcall(Q.UpdateTaxi) end) else pcall(Q.UpdateTaxi) end end)
  self:RegisterEvent("TAXIMAP_CLOSED", function() Q.flight = nil Q.HideTaxi() end)
  self:RegisterEvent("GOSSIP_SHOW", function() pcall(Q.UpdateInn) end)
  self:RegisterEvent("GOSSIP_CLOSED", function() Q.HideCard("inn") end)
  self:RegisterEvent("MERCHANT_SHOW", function() pcall(Q.LearnVendor) end)
  for _, ev in ipairs({ "PLAYER_UPDATE_RESTING", "MERCHANT_CLOSED", "UPDATE_INVENTORY_DURABILITY", "MAIL_CLOSED",
      "TRAINER_CLOSED", "ZONE_CHANGED_NEW_AREA", "PLAYER_ENTERING_WORLD", "BAG_UPDATE_DELAYED", "UPDATE_PENDING_MAIL" }) do
    self:RegisterEvent(ev, errandsSoon)
  end
  self:RegisterEvent("GROUP_ROSTER_UPDATE", function()
    pcall(Q.RememberGroup) pcall(Q.PushTab)
    if win and try(win.IsShown, win) then pcall(Q.RefreshReady) end
  end)
  self:RegisterEvent("PARTY_LEADER_CHANGED", function() if win and try(win.IsShown, win) then pcall(Q.RefreshReady) end end)
  self:RegisterEvent("READY_CHECK", function() if opt("ready") and not inCombat() then Q:ShowReady(true) end end)
  self:On("COMBAT_START", function() Q.HideTaxi() Q.HideCard() end)
  pcall(Q.PushTab)
  errandsSoon()
end

function Q:OnDisable()
  Q.HideTaxi()
  Q.HideCard()
  if win then try(win.Hide, win) end
  if ticker then ticker:Cancel() ticker = nil end
  if T.SetTabLines then T:SetTabLines("group", {}, "QuickTravel") end
end

if T.RegisterSlash then
  T:RegisterSlash("prets", function() Q:ShowReady() end, "prets : fenêtre « Tout le monde prêt ? » et outils du groupe")
  T:RegisterSlash("rdv", function()
    local ok, why = Q.SendMeet()
    if not ok then
      Q:Print(why == "gap" and "Point de rencontre déjà envoyé il y a moins de 15 s." or
        why == "group" and "Pas de groupe." or "Position inconnue ici.")
    end
  end, "rdv : envoie ta position au groupe comme point de rencontre")
  T:RegisterSlash("courses", function() Q.UpdateErrands()
    if not (Q.steps and #Q.steps > 0) then Q:Print("Rien à faire avant de partir (ou tu n'es pas au repos en ville).") end
  end, "courses : ce qu'il reste à faire en ville avant de partir")
end
