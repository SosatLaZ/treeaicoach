-- TreeAI — module Watch « Suivi TreeAI » (agent REPLACE, 4.0.1) : LE suivi de quêtes à l'écran, à la place de celui
-- du jeu (et de celui de Questie quand il est silencieux).
--  * les quêtes du trajet, rangées par zone dans l'ordre du trajet (n° d'arrêt = celui de l'onglet et de la carte),
--    puis « Hors trajet » ; bascule « Par distance » (zones et quêtes par distance, retriées toutes les 4 s en marchant) ;
--  * chaque objectif : barre de progression, texte du jeu, et à droite la distance + la direction + la durée à pied
--    (NavTime) jusqu'à sa zone (amas d'apparitions de QuestieDB, sinon le point du jeu), EN DIRECT en marchant : une
--    lecture par seconde, seuls les textes dont la valeur arrondie change sont réécrits, rien à l'arrêt ;
--  * quête prête : « Rendre à X · 380 m E · 2 min » ; objet de quête : bouton sécurisé (type=item, comme la barre de
--    quête) posé HORS COMBAT seulement ; clic = la boussole vise ; clic droit = menu (guider, ignorer dans le trajet,
--    partager au groupe, retirer du suivi, abandonner : page « Journal »).
--  * « Remplacer le suivi du jeu » (défaut oui) : le suivi de Blizzard est caché HORS COMBAT (Hide + crochet OnShow
--    qui le recache ; en combat seulement SetAlpha(0), jamais Hide / SetPoint : ses boutons d'objet sont sécurisés) ;
--    mode édition du jeu ouvert = on le laisse ; option coupée = tout est rendu (Show + alpha 1). Questie : son suivi
--    se coupe dans SES options (une ligne propose le clic, recharge de l'interface) ; Shortest Path : page « Mes addons ».
-- Données lues : Quests.lastList (journal réel), QuestPins (positions : export QuestieDB / points du jeu), Route:Stops
-- (ordre et numéros ; on ne calcule aucun trajet ici), NavTime (durées). Cadre : enfant d'UIParent, Theme:Window,
-- posé une fois à la place du suivi du jeu puis jamais déplacé par TreeAI (PLACEBUG : place du joueur gardée,
-- mode édition TreeAI). Taint : aucun cadre de Blizzard modifié hormis Hide / Show / SetAlpha du suivi (hors combat
-- pour Hide / Show), aucun crochet sécurisé détourné. Lua 5.1, aucune globale.
local T = _G.TreeAI
if not (T and T.RegisterModule) then return end
local G = _G

local W = T:RegisterModule("Watch", {
  title = "Suivi TreeAI",
  description = "Ton suivi de quêtes à l'écran : les quêtes du trajet par zone, chaque objectif avec sa distance, "
    .. "sa direction et sa durée en direct, à côté du suivi du jeu (qui reste là).",
  -- (4.0.5) coupé d'office et ne cache plus jamais le suivi du jeu (le joueur doit pouvoir cliquer ses quêtes)
  defaults = { enabled = false, shown = true, replace = false, sort = 1, maxQuests = 10, scale = 100, lock = false,
               offRoute = true },
  charDefaults = { pos = nil, hidden = {}, collapsed = false },
  options = {
    { key = "shown", type = "toggle", label = "Afficher le Suivi TreeAI", section = "Suivi" },
    { key = "replace", type = "toggle", label = "Cacher le suivi du jeu (déconseillé : ses quêtes ne sont plus cliquables)", section = "Suivi" },
    { key = "sort", type = "choice", label = "Ordre", values = { "Ordre du trajet", "Par distance" }, section = "Suivi" },
    { key = "offRoute", type = "toggle", label = "Montrer aussi les quêtes hors trajet", section = "Suivi" },
    { key = "maxQuests", type = "range", label = "Quêtes affichées", min = 3, max = 20, step = 1, section = "Cadre" },
    { key = "scale", type = "range", label = "Taille (%)", min = 70, max = 150, step = 5, section = "Cadre" },
    { key = "lock", type = "toggle", label = "Verrouiller (ne se déplace plus)", section = "Cadre" },
  },
})
T.Watch = W

do
  local fam = type(T.OPTIONS_FAMILY) == "table" and T.OPTIONS_FAMILY[1]
  if type(fam) == "table" and type(fam.mods) == "table" then
    local has = false
    for _, n in ipairs(fam.mods) do if n == "Watch" then has = true end end
    if not has then table.insert(fam.mods, 1, "Watch") end
  end
end

local floor, max, min, abs, sqrt, atan2, pi = math.floor, math.max, math.min, math.abs, math.sqrt, math.atan2, math.pi
local fmt, tsort, tconcat = string.format, table.sort, table.concat
local function pass(ok, ...) if ok then return ... end return nil end
local function try(fn, ...) if type(fn) ~= "function" then return nil end return pass(pcall(fn, ...)) end
local function g(name) return rawget(G, name) end
local function meth(o, m, ...) local f = type(o) == "table" and o[m] if type(f) == "function" then return pass(pcall(f, o, ...)) end end
local function secret(v) local f = g("issecretvalue") return type(f) == "function" and try(f, v) and true or false end
local function num(v) if v == nil or secret(v) then return nil end return tonumber(v) end
local function opt(k) return W.db and W.db[k] ~= false end
local function now() return try(g("GetTime")) or 0 end
local function inCombat() return T.InCombat and T:InCombat() or false end
local cut = T.Cut or function(s, n) s = tostring(s or "") return #s > n and s:sub(1, n) or s end
local function Th() local t = T.Theme if type(t) == "table" and t.Color then return t end end
local function QP() local m = T.QuestPins return type(m) == "table" and type(m.Build) == "function" and m or nil end
local function questsMod() local m = T.modules and T.modules.Quests return type(m) == "table" and m or nil end
-- (BRAIN) quête mise en pause par le pilote auto : rangée dans « En pause (n) » (jamais une quête prête à rendre)
local function parked(q)
  local B = T.Brain
  if type(B) ~= "table" or type(B.Parked) ~= "function" or q.complete then return nil end
  return try(B.Parked, q.id)
end
W.Parked = parked

local WIDTH, HEAD, ROW_Q, ROW_O, ROW_G, PADX = 270, 22, 18, 15, 16, 8
W.WIDTH = WIDTH
local DIRS = { "N", "NE", "E", "SE", "S", "SO", "O", "NO" }

-- ===================================================================== distance, direction, durée (pur)
--- direction du point (n, w) vu du joueur (me) : N, NE, E, … (le nord en haut, l'est = −ouest)
function W.Dir(me, p)
  local dn, de = p.n - me.n, -(p.w - me.w)
  local ang = atan2(de, dn)                      -- 0 = nord, sens horaire
  local i = floor(((ang * 180 / pi) + 360 + 22.5) % 360 / 45) + 1
  return DIRS[i], i
end
--- distance (yards) jusqu'au bord de la zone (0 = dedans) ; nil sans position sur ce continent
function W.EdgeDist(me, p)
  if not (me and p and p.n and p.c == me.c) then return nil end
  local d = sqrt((p.n - me.n) ^ 2 + (p.w - me.w) ^ 2)
  return max(0, d - (p.ryd or 0)), d
end
local function meters(yd)
  local m = yd * 0.9144
  if m >= 1000 then return (fmt("%.1f km", floor(m / 100 + 0.5) / 10):gsub("%.", ",")) end
  return (floor(m / 10 + 0.5) * 10) .. " m"
end
W.Meters = meters
local function etaSec(yd, map)
  local NT = T.NavTime
  return (NT and type(NT.Model) == "function" and num(try(NT.Model, yd, map))) or yd * 1.3 / 7
end
--- « 120 m NE · 2 min » / « ici » ; clé arrondie (pour ne réécrire que ce qui change)
function W.DistText(me, p)
  local d = W.EdgeDist(me, p)
  if not d then return nil, nil end
  if d < 8 then return "ici", "ici" end
  local dir, di = W.Dir(me, p)
  local m = d * 0.9144
  local rm = m >= 1000 and floor(m / 100 + 0.5) * 100 or floor(m / 10 + 0.5) * 10
  local mins = max(1, floor(etaSec(d, p.map) / 60 + 0.5))
  local key = rm * 100 + di * 1000000 + mins
  return fmt("%s %s · %d min", meters(d), dir, mins), key
end

-- ===================================================================== modèle : quêtes, objectifs, positions
local npcCache = {}
local function npcName(pin)
  local npc = pin and pin.npc
  if not (npc and npc.id) then return nil end
  local k = tostring(npc.k) .. ":" .. npc.id
  if npcCache[k] == nil then
    local P = QP()
    npcCache[k] = (P and P.NpcName and P.NpcName(npc)) or false
  end
  return npcCache[k] or nil
end

-- positions des quêtes du journal (mêmes données que les icônes), refaites quand le journal / l'export / le trajet changent
local posCache, posSig = {}, {}
local function positions()
  local P = QP()
  local Xm = T.Export
  local exp = Xm and type(Xm.Data) == "function" and try(Xm.Data, Xm) or nil
  local Pl, R = T.Planner, T.Route
  local qg = T.QuestGen and T:QuestGen() or 0
  local s = posSig
  if s.qg == qg and s.exp == exp and s.made == (exp and exp.made) and s.st == (Pl and Pl.steps) and s.cur == (Pl and Pl.cursor)
      and s.pause == (R and R.char and R.char.paused) then
    return posCache, s.route
  end
  s.qg, s.exp, s.made, s.st, s.cur, s.pause = qg, exp, exp and exp.made, Pl and Pl.steps, Pl and Pl.cursor, R and R.char and R.char.paused
  local idx = {}
  local route = P and P.RouteIndex and P.RouteIndex() or nil
  if P then
    local snap = Xm and type(Xm.LogSnapshot) == "function" and try(Xm.LogSnapshot) or {}
    local lvl = num(try(g("UnitLevel"), "player")) or 1
    local ok, pins = pcall(P.Build, exp, type(snap) == "table" and snap or {}, lvl, route,
      { avail = false, turnin = true, objectives = true })
    for _, p in ipairs(ok and pins or {}) do
      for _, q in ipairs(p.qs or {}) do
        local e = idx[q.id]
        if not e then e = { obj = {} }; idx[q.id] = e end
        if p.kind == "turnin" then e.turn = p
        else
          local j = tonumber((tostring(p.key):match("^o:%d+:(%d+)"))) or 0
          local l = e.obj[j]
          if not l then l = {}; e.obj[j] = l end
          l[#l + 1] = p
        end
      end
    end
  end
  posCache, s.route = idx, route
  return idx, route
end
W.Positions = positions

-- la cible la plus proche d'une liste de zones (pins) : pin, distance au bord
local function nearest(me, list)
  local best, bd
  for _, p in ipairs(list or {}) do
    local d = W.EdgeDist(me, p)
    if d and (not bd or d < bd) then best, bd = p, d end
  end
  return best or (list and list[1]), bd
end

--- modèle affiché : { groups = { {zone, quests = {qe}} }, n } ; qe = { q, stop, rows = { {kind, text, have, need, done, pins} } }
function W.Model(me)
  local Q = questsMod()
  local list = Q and Q.lastList or {}
  local pos, route = positions()
  local hidden = W.char and W.char.hidden or {}
  -- pas de trajet en cours (pause, aucun) : rangées par zone et par distance, jamais toutes « Hors trajet »
  local byDist = (tonumber(W.db and W.db.sort) or 1) == 2 or not (route and next(route))
  local entries, paused = {}, 0
  for _, q in ipairs(list) do
    local pz = q.id and not hidden[q.id] and parked(q)
    if pz then paused = paused + 1 end
    if q.id and not hidden[q.id] and not pz then
      local rq = route and route[q.id] or nil
      local e = pos[q.id] or { obj = {} }
      local qe = { q = q, rows = {} }
      local stop
      if q.complete then
        stop = rq and rq.turn
        local pin = e.turn
        local nm = npcName(pin)
        qe.rows[1] = { kind = "turn", text = nm and ("Rendre à " .. cut(nm, 26)) or "Prête à rendre", done = false,
          pins = pin and { pin } or nil }
      elseif q.failed then
        qe.rows[1] = { kind = "fail", text = "Échouée : abandonne-la puis reprends-la", done = false }
      else
        for j, o in ipairs(q.obj or {}) do
          local need = tonumber(o.need) or 1
          local row = { kind = "obj", j = j, text = o.name or o.text or "?", have = tonumber(o.have) or 0, need = need,
            done = o.finished and true or false, pins = (not o.finished) and e.obj[j] or nil }
          qe.rows[#qe.rows + 1] = row
          local s2 = rq and not o.finished and (rq.obj[j] or rq.anyObj) or nil
          if s2 and (not stop or s2 < stop) then stop = s2 end
        end
        if #qe.rows == 0 then qe.rows[1] = { kind = "obj", text = "En cours", have = 0, need = 1, done = false } end
      end
      qe.stop = stop or (rq and rq.pick)
      -- distance de la quête : la cible pas finie la plus proche
      local qd
      for _, r in ipairs(qe.rows) do
        if r.pins then
          local _, d = nearest(me, r.pins)
          if d and (not qd or d < qd) then qd = d end
        end
      end
      qe.dist = qd
      entries[#entries + 1] = qe
    end
  end
  -- ordre : trajet (n° d'arrêt) puis hors trajet par distance ; ou tout par distance
  local function dkey(qe) return qe.dist or 1e9 end
  tsort(entries, function(a, b)
    if not byDist then
      if (a.stop ~= nil) ~= (b.stop ~= nil) then return a.stop ~= nil end
      if a.stop and b.stop and a.stop ~= b.stop then return a.stop < b.stop end
    end
    if dkey(a) ~= dkey(b) then return dkey(a) < dkey(b) end
    return a.q.id < b.q.id
  end)
  local maxQ = tonumber(W.db and W.db.maxQuests) or 10
  local groups, byZone, shown = {}, {}, 0
  local offRoute = opt("offRoute")
  for _, qe in ipairs(entries) do
    if shown >= maxQ then break end
    if byDist or qe.stop or offRoute then
      local zone = (not byDist and not qe.stop) and "Hors trajet" or tostring(qe.q.zone or "Autres quêtes")
      local gr = byZone[zone]
      if not gr then gr = { zone = zone, quests = {} }; byZone[zone] = gr; groups[#groups + 1] = gr end
      gr.quests[#gr.quests + 1] = qe
      shown = shown + 1
    end
  end
  -- « Hors trajet » toujours en dernier
  if not byDist then
    for i, gr in ipairs(groups) do
      if gr.zone == "Hors trajet" and i < #groups then table.remove(groups, i) groups[#groups + 1] = gr break end
    end
  end
  return { groups = groups, n = shown, total = #entries, paused = paused }
end

-- ===================================================================== cadre
local frame, body, rows, items = nil, nil, {}, {}
W.rows, W.items = rows, items
W.renders, W.ticks, W.textWrites = 0, 0, 0
local function playerWorld() local P = QP() return P and P.PlayerWorld and P.PlayerWorld() or nil end

local function savePos()
  if not frame then return end
  local p, _, rp, x, y = meth(frame, "GetPoint", 1)
  if p and tonumber(x) and tonumber(y) and W.char then
    W.char.pos = { point = p, rel = rp or p, x = floor(x + 0.5), y = floor(y + 0.5) }
  end
end
-- première place : celle du suivi du jeu (on prend sa colonne), sinon sous la minimap à droite
local function trackerFrame()
  for _, n in ipairs({ "ObjectiveTrackerFrame", "QuestWatchFrame", "WatchFrame" }) do
    local f = g(n)
    if type(f) == "table" and type(f.Hide) == "function" then return f, n end
  end
  return nil
end
W.TrackerFrame = trackerFrame
local function firstPlace()
  local up = g("UIParent")
  local UW, UH
  if type(T._ScreenSize) == "function" then UW, UH = T._ScreenSize() end   -- la même mesure que le placement
  if not (UW and UH) then UW, UH = num(meth(up, "GetWidth")), num(meth(up, "GetHeight")) end
  if not (UW and UH) or UW <= 200 or UH <= 200 then return nil end
  local function onScreen(p)                 -- coin haut-droit du cadre : au moins 3/4 de sa largeur visibles
    return p and p.x <= 0 and p.x >= -(UW - WIDTH * 0.75) and p.y <= 0 and p.y >= -(UH - 60) and p or nil
  end
  local tf = trackerFrame()
  local l, b, w, h = nil, nil, nil, nil
  -- le rectangle du suivi du jeu se lit même caché (ancré par le mode édition) : sa colonne devient la nôtre
  if tf then l, b, w, h = num(meth(tf, "GetLeft")), num(meth(tf, "GetBottom")), num(meth(tf, "GetWidth")), num(meth(tf, "GetHeight")) end
  if l and b and w and h and w > 20 and h > 10 then
    local k = (num(meth(tf, "GetEffectiveScale")) or 1) / (num(meth(up, "GetEffectiveScale")) or 1)
    local right, top = (l + w) * k, (b + h) * k
    local p = onScreen({ point = "TOPRIGHT", rel = "TOPRIGHT", x = floor(right - UW + 0.5), y = floor(top - UH + 0.5) })
    if p then return p end
  end
  local mm = g("MinimapCluster") or g("Minimap")
  local mb = mm and num(meth(mm, "GetBottom"))
  local mr = mm and num(meth(mm, "GetRight"))
  if mb and mr then
    local k = (num(meth(mm, "GetEffectiveScale")) or 1) / (num(meth(up, "GetEffectiveScale")) or 1)
    local p = onScreen({ point = "TOPRIGHT", rel = "TOPRIGHT", x = floor(mr * k - UW + 0.5), y = floor(mb * k - UH - 12 + 0.5) })
    if p then return p end
  end
  return { point = "TOPRIGHT", rel = "TOPRIGHT", x = -40, y = -260 }
end
W.FirstPlace = firstPlace

-- place : celle du joueur / la première place notée ; la première place se prend sur un écran posé et stable
-- (PLACEBUG : à la connexion les cadres du jeu ne sont pas encore à leur place), sinon une place provisoire
local placeTries = 0
local function applyPos()
  if not frame then return end
  local p = W.char and W.char.pos
  if type(p) ~= "table" then
    local stable = not T.ScreenStable or T:ScreenStable()
    p = (stable or placeTries >= 8) and firstPlace() or nil
    if p and W.char then
      W.char.pos = p
    else
      p = { point = "TOPRIGHT", rel = "TOPRIGHT", x = -40, y = -260 }
      placeTries = placeTries + 1
      if T.After and placeTries <= 8 then T:After(1.5, function() if frame and not (W.char and W.char.pos) then applyPos() W.PlaceItems() end end) end
    end
  end
  if inCombat() then return end
  meth(frame, "ClearAllPoints")
  meth(frame, "SetPoint", p.point or "TOPRIGHT", g("UIParent"), p.rel or p.point or "TOPRIGHT", p.x or -40, p.y or -260)
end

local function hideTip() local tt = g("GameTooltip") if tt then try(tt.Hide, tt) end end

local function newRow(i)
  local r = rows[i]
  if r then return r end
  local CF = g("CreateFrame")
  r = CF("Button", nil, body)
  r:SetHeight(ROW_O)
  try(r.RegisterForClicks, r, "LeftButtonUp", "RightButtonUp")
  r.fs = r:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
  r.fs:SetPoint("LEFT", r, "LEFT", 0, 0)
  try(r.fs.SetJustifyH, r.fs, "LEFT")
  try(r.fs.SetWordWrap, r.fs, false)
  r.rt = r:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
  r.rt:SetPoint("RIGHT", r, "RIGHT", -2, 0)
  try(r.rt.SetJustifyH, r.rt, "RIGHT")
  try(r.rt.SetWordWrap, r.rt, false)
  r.barBg = r:CreateTexture(nil, "BACKGROUND")
  r.barBg:SetTexture("Interface\\Buttons\\WHITE8X8")
  r.barBg:Hide()
  r.bar = r:CreateTexture(nil, "ARTWORK")
  r.bar:SetTexture("Interface\\Buttons\\WHITE8X8")
  r.bar:Hide()
  r.badge = r:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
  r.badge:SetPoint("LEFT", r, "LEFT", 0, 0)
  r.badge:Hide()
  r:SetScript("OnClick", function(self, btn) W.RowClick(self, btn) end)
  r:SetScript("OnEnter", function(self) W.RowTip(self) end)
  r:SetScript("OnLeave", hideTip)
  r:Hide()
  rows[i] = r
  return r
end

local function ensureFrame()
  if frame then return frame end
  local CF, up = g("CreateFrame"), g("UIParent")
  if not (CF and up) or inCombat() then return nil end
  frame = CF("Frame", "TreeAIWatchFrame", up)
  frame._taiScan = true                     -- (UI.lua) vu par le placement comme un suivi : rien ne se pose dessus
  frame:SetSize(WIDTH, HEAD + 20)
  frame:SetFrameStrata("MEDIUM")
  try(frame.SetClampedToScreen, frame, true)
  frame:SetMovable(true)
  frame:EnableMouse(true)
  local th = Th()
  local hd
  if th and th.Window then
    hd = th:Window(frame, { id = "watch", title = "Suivi", icon = "Interface\\Icons\\INV_Misc_Map02",
      esc = false,
      locked = function() return (W.db and W.db.lock) or inCombat() end,
      onMoved = function() savePos() W.PlaceItems() end,
      onClose = function() if W.db then W.db.shown = false end W.Apply() end,
      onCollapse = function(v) if W.char then W.char.collapsed = v and true or false end W.PlaceItems() end })
  end
  body = CF("Frame", nil, frame)
  body:SetPoint("TOPLEFT", frame, "TOPLEFT", 0, -HEAD)
  body:SetPoint("TOPRIGHT", frame, "TOPRIGHT", 0, -HEAD)
  body:SetHeight(10)
  -- bascule de l'ordre : dans l'en-tête, à gauche de « réduire »
  local sb = CF("Button", nil, hd or frame)
  sb:SetSize(92, 16)
  if hd and hd.min then sb:SetPoint("RIGHT", hd.min, "LEFT", -4, 0) else sb:SetPoint("TOPRIGHT", frame, "TOPRIGHT", -44, -4) end
  sb.t = sb:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
  sb.t:SetAllPoints(sb)
  try(sb.t.SetJustifyH, sb.t, "RIGHT")
  sb:SetScript("OnClick", function() T:UserAction(W.ToggleSort) end)
  sb:SetScript("OnEnter", function(self)
    local tt = g("GameTooltip")
    if not tt then return end
    try(tt.SetOwner, tt, self, "ANCHOR_BOTTOM")
    try(tt.AddLine, tt, "Ordre du suivi", 1, 0.82, 0.2)
    try(tt.AddLine, tt, "Clic : ordre du trajet / par distance", 0.9, 0.9, 0.9, true)
    try(tt.Show, tt)
  end)
  sb:SetScript("OnLeave", hideTip)
  frame.sortBtn = sb
  W.frame, W.header = frame, hd
  if T.RegisterMovable then
    -- noReflow : jamais écarté par le placement vivant (PLACEBUG : il reste où il est)
    T:RegisterMovable(frame, "watch", "Suivi TreeAI", { noReflow = true,
      onMoved = function() savePos() W.PlaceItems() end,
      onReset = function() if W.char then W.char.pos = nil end applyPos() W.PlaceItems() end })
  end
  applyPos()
  frame:Hide()
  return frame
end

-- ----------------------------------------------------- boutons d'objet de quête (sécurisés, hors combat seulement)
-- enfants d'UIParent posés à la position LUE de la ligne (jamais ancrés sur nos lignes : celles-ci restent libres)
local wantItems = {}                      -- { {itemID, link, icon, row} } décidé au rendu
local function newItem(i)
  local b = items[i]
  if b then return b end
  local CF = g("CreateFrame")
  local ok, nb = pcall(CF, "Button", "TreeAIWatchItem" .. i, g("UIParent"), "SecureActionButtonTemplate")
  if not ok or not nb then return nil end
  b = nb
  b:SetSize(16, 16)
  b:SetFrameStrata("MEDIUM")
  try(b.RegisterForClicks, b, "AnyUp", "AnyDown")
  b.icon = b:CreateTexture(nil, "ARTWORK")
  b.icon:SetAllPoints(b)
  b:SetScript("OnEnter", function(self)
    local tt = g("GameTooltip")
    if not tt then return end
    try(tt.SetOwner, tt, self, "ANCHOR_LEFT")
    if self.link then try(tt.SetHyperlink, tt, self.link) end
    try(tt.AddLine, tt, "Clic : utiliser l'objet de quête", 0.4, 0.8, 1, true)
    try(tt.Show, tt)
  end)
  b:SetScript("OnLeave", hideTip)
  b:Hide()
  items[i] = b
  return b
end
local function placeItemsNow()
  local scale = (frame and num(meth(frame, "GetEffectiveScale")) or 1) / (num(meth(g("UIParent"), "GetEffectiveScale")) or 1)
  local collapsed = W.char and W.char.collapsed
  local visible = frame and meth(frame, "IsShown") and not collapsed
  local used = 0
  for i, it in ipairs(wantItems) do
    local r = it.row
    local l, top = r and num(meth(r, "GetRight")), r and num(meth(r, "GetTop"))
    if visible and l and top then
      local b = newItem(i)
      if not b then break end
      used = i
      if b.itemID ~= it.id then
        b:SetAttribute("type", "item")
        b:SetAttribute("item", "item:" .. it.id)
        b.itemID = it.id
      end
      b.link = it.link
      b.icon:SetTexture(it.icon or 134400)
      b:ClearAllPoints()
      b:SetPoint("TOPRIGHT", g("UIParent"), "BOTTOMLEFT", l * scale - 2, top * scale - 1)
      try(b.SetScale, b, 1)
      b:Show()
    end
  end
  for i = used + 1, #items do
    local b = items[i]
    if b:IsShown() then b:Hide() end
  end
  W.itemsShown = used
end
function W.PlaceItems()
  if T.RunOutOfCombat then T:RunOutOfCombat("Watch:items", placeItemsNow) elseif not inCombat() then placeItemsNow() end
end

-- ----------------------------------------------------- rendu
local live = {}                           -- lignes à distance vivante : { row, pins, key }
W.live = live
local function color(fs, r, gg, b) meth(fs, "SetTextColor", r, gg, b) end
local function sortLabel() return (tonumber(W.db and W.db.sort) or 1) == 2 and "Par distance" or "Ordre du trajet" end

local function objText(rd)
  if rd.kind == "obj" and (rd.need or 1) > 1 then return fmt("%s %d/%d", cut(rd.text, 26), rd.have or 0, rd.need) end
  return cut(rd.text, 32)
end
-- texte, couleur et barre d'une ligne d'objectif (mise en page complète ou simple progression)
local function paintObj(orow, rd)
  if rd.kind == "obj" then
    local frac = rd.done and 1 or min(1, (rd.have or 0) / max(1, rd.need or 1))
    orow.bar:SetSize(max(1, floor(28 * frac + 0.5)), 4)
    if rd.done then orow.bar:SetVertexColor(0.3, 1, 0.3, 1) else orow.bar:SetVertexColor(1, 0.8, 0.2, 1) end
    if frac > 0 then orow.bar:Show() else orow.bar:Hide() end
  end
  orow.fs:SetText(objText(rd))
  if rd.done then color(orow.fs, 0.5, 0.5, 0.5)
  elseif rd.kind == "fail" then color(orow.fs, 1, 0.3, 0.25)
  elseif rd.kind == "turn" then color(orow.fs, 0.6, 1, 0.6)
  else color(orow.fs, 0.88, 0.88, 0.88) end
end

local function layoutRow(i, y, h, indent)
  local r = newRow(i)
  r:ClearAllPoints()
  r:SetPoint("TOPLEFT", body, "TOPLEFT", PADX + indent, -y)
  r:SetPoint("TOPRIGHT", body, "TOPRIGHT", -PADX, -y)
  r:SetHeight(h)
  r.fs:ClearAllPoints()
  r.fs:SetPoint("LEFT", r, "LEFT", 0, 0)
  r.barBg:Hide() r.bar:Hide() r.badge:Hide()
  r.rt:SetText("")
  r:Show()
  return r
end

--- dessine le suivi (hors combat : mise en page ; en combat : textes seulement, la mise en page attend la fin du combat)
function W.Render()
  if not (W._active and opt("shown")) then return end
  if not ensureFrame() then return end
  -- caché en combat par SetAlpha(0) puis réaffiché avant la fin du combat : rendu visible (un cadre transparent
  -- prendrait les clics sans se voir)
  if (num(meth(frame, "GetAlpha")) or 1) < 1 then meth(frame, "SetAlpha", 1) end
  local me = playerWorld()
  local model = W.Model(me)
  if inCombat() then
    -- en combat : la progression s'écrit sur place (textes, barres : nos cadres, rien de protégé) ; une quête en plus
    -- ou en moins attend la fin du combat (les boutons d'objet sécurisés ne bougent pas en combat)
    if not frame:IsShown() then W._pendingLayout = true return end
    local sgS, sg = W.Signature(model)
    if sgS == W._sigS then
      W.model = model
      if sg ~= W._sig then W._sig = sg W.Refill(model, me) else W.UpdateTexts(me) end
    else
      W._pendingLayout = true
      W.UpdateTexts(me)
    end
    return
  end
  W._pendingLayout = false
  W.model = model
  -- même contenu : seulement les distances ; même structure (quêtes, lignes, arrêts, ordre) : la progression est
  -- réécrite sur place (texte, barre) sans rien reposer ; sinon la mise en page complète
  local sgS, sg = W.Signature(model)
  if frame:IsShown() and not W._force then
    if sg == W._sig then W.UpdateTexts(me) return end
    if sgS == W._sigS then W._sig = sg W.Refill(model, me) return end
  end
  W._sig, W._sigS, W._force = sg, sgS, nil
  W.renders = W.renders + 1
  frame.sortBtn.t:SetText(sortLabel())
  local th = Th()
  local pl = num(try(g("UnitLevel"), "player")) or 1
  local y, n = 4, 0
  for i = 1, #live do live[i] = nil end
  for i = 1, #wantItems do wantItems[i] = nil end
  local function qcolor(lv)
    if th and th.QuestDiffColor then return th:QuestDiffColor(lv, pl) end
    return 1, 0.82, 0
  end
  if model.n == 0 then
    n = n + 1
    local r = layoutRow(n, y, ROW_Q, 0)
    r.fs:SetText(model.total > 0 and "Tout est masqué : clic droit, « Tout réafficher »"
      or ((model.paused or 0) > 0 and "Rien à suivre : tes quêtes sont en pause" or "Aucune quête en cours : prends-en une !"))
    color(r.fs, 0.7, 0.7, 0.7)
    r.kind, r.qe, r.rowd = "empty", nil, nil
    y = y + ROW_Q
  end
  -- hauteur disponible : jamais sous le bas de l'écran (marge 50) ; le reste = « … et N autres »
  local up = g("UIParent")
  local top = num(meth(frame, "GetTop"))
  local sc = (num(meth(frame, "GetEffectiveScale")) or 1) / (num(meth(up, "GetEffectiveScale")) or 1)
  local maxY = (top and sc > 0) and max(120, (top * sc - 50) / sc - HEAD - 6) or 9999
  local left = 0
  for _, gr in ipairs(model.groups) do
    local firstH = ROW_G + ROW_Q + #(gr.quests[1] and gr.quests[1].rows or {}) * ROW_O
    if left > 0 or y + firstH > maxY then
      left = left + #gr.quests
    else
    n = n + 1
    local hr = layoutRow(n, y, ROW_G, 0)
    hr.fs:SetText(cut(gr.zone, 34))
    color(hr.fs, 1, 0.82, 0.2)
    hr.kind, hr.qe, hr.rowd = "zone", nil, nil
    y = y + ROW_G
    for _, qe in ipairs(gr.quests) do
      local q = qe.q
      if left > 0 or y + ROW_Q + #qe.rows * ROW_O > maxY then left = left + 1 else
      n = n + 1
      local qr = layoutRow(n, y, ROW_Q, 2)
      local lead = 0
      if qe.stop then
        qr.badge:SetText(tostring(qe.stop))
        color(qr.badge, 0.35, 0.8, 1)
        qr.badge:Show()
        lead = 16
      end
      qr.fs:ClearAllPoints()
      qr.fs:SetPoint("LEFT", qr, "LEFT", lead, 0)
      local lv = (tonumber(q.level) or 0) > 0 and ("[" .. q.level .. "] ") or ""
      qr.fs:SetText(lv .. cut(q.title, 30))
      if q.complete then color(qr.fs, 0.3, 1, 0.3) else color(qr.fs, qcolor(q.level)) end
      qr.kind, qr.qe, qr.rowd = "quest", qe, nil
      local it = q.item
      if it and it.id and (not q.complete or it.showComplete) then
        wantItems[#wantItems + 1] = { id = it.id, link = it.link, icon = it.icon, row = qr }
      end
      y = y + ROW_Q
      for _, rd in ipairs(qe.rows) do
        n = n + 1
        local orow = layoutRow(n, y, ROW_O, 14)
        local bx = 0
        if rd.kind == "obj" then
          bx = 34
          orow.barBg:ClearAllPoints()
          orow.barBg:SetPoint("LEFT", orow, "LEFT", 0, 0)
          orow.barBg:SetSize(28, 4)
          orow.barBg:SetVertexColor(0.2, 0.2, 0.2, 0.9)
          orow.barBg:Show()
          orow.bar:ClearAllPoints()
          orow.bar:SetPoint("LEFT", orow, "LEFT", 0, 0)
        end
        orow.fs:ClearAllPoints()
        orow.fs:SetPoint("LEFT", orow, "LEFT", bx, 0)
        paintObj(orow, rd)
        orow.kind, orow.qe, orow.rowd = "obj", qe, rd
        orow.key = nil
        if rd.pins and not rd.done then live[#live + 1] = orow end
        y = y + ROW_O
      end
      end
    end
    end
  end
  if left > 0 then
    n = n + 1
    local r = layoutRow(n, y, ROW_Q, 0)
    r.fs:SetText(fmt("… et %d autre%s quête%s : page « Quêtes »", left, left > 1 and "s" or "", left > 1 and "s" or ""))
    color(r.fs, 0.7, 0.7, 0.7)
    r.kind, r.qe, r.rowd = "more", nil, nil
    y = y + ROW_Q
  end
  W.hiddenByRoom = left
  -- (BRAIN) quêtes en pause du pilote auto : une ligne repliée, clic = sa page (Reprendre)
  if (model.paused or 0) > 0 then
    n = n + 1
    local r = layoutRow(n, y, ROW_Q, 0)
    r.fs:SetText(fmt("En pause (%d) : pilote auto", model.paused))
    color(r.fs, 0.6, 0.6, 0.6)
    r.kind, r.qe, r.rowd = "paused", nil, nil
    y = y + ROW_Q
  end
  -- ligne Questie : son suivi est encore là (un clic le coupe dans SES options, recharge)
  local AD = T.Addons
  local qs = AD and type(AD.QuestieState) == "function" and AD.QuestieState() or nil
  if qs and qs.loaded and qs.tracker and opt("replace") then
    n = n + 1
    local r = layoutRow(n, y + 2, ROW_Q, 0)
    r.fs:SetText("Questie affiche aussi son suivi : clic pour le couper")
    color(r.fs, 0.6, 0.8, 1)
    r.kind, r.qe, r.rowd = "questie", nil, nil
    y = y + ROW_Q + 2
  end
  for i = n + 1, #rows do local r = rows[i] if r:IsShown() then r:Hide() end r.qe, r.rowd, r.kind = nil, nil, nil end
  W.rowsShown = n
  body:SetHeight(max(10, y + 4))
  local collapsed = W.char and W.char.collapsed
  if not collapsed then frame:SetHeight(HEAD + y + 6) end
  local k = (tonumber(W.db and W.db.scale) or 100) / 100
  try(frame.SetScale, frame, max(0.5, min(2, k)))
  W.UpdateTexts(me, true)
  if not frame:IsShown() then frame:Show() end
  W.PlaceItems()
end

--- empreinte du contenu affiché (sans les distances) : la mise en page n'est refaite que si elle change
local sigParts, progParts = {}, {}
function W.Signature(model)
  local n, m = 0, 0
  local function put(v) n = n + 1 sigParts[n] = tostring(v) end
  local function prog(v) m = m + 1 progParts[m] = tostring(v) end
  put(W.db and W.db.sort) put(W.db and W.db.maxQuests) put(W.db and W.db.scale) put(model.total) put(model.paused or 0)
  put(frame and floor(num(meth(frame, "GetTop")) or 0))
  local AD = T.Addons
  local qs = AD and type(AD.QuestieState) == "function" and AD.QuestieState() or nil
  put(qs and qs.loaded and qs.tracker and opt("replace") and 1 or 0)
  for _, gr in ipairs(model.groups) do
    put(gr.zone)
    for _, qe in ipairs(gr.quests) do
      local q = qe.q
      put(q.id) put(qe.stop) put(q.complete and 1 or 0) put(q.item and q.item.id)
      for _, rd in ipairs(qe.rows) do
        put(rd.kind)
        prog(rd.text) prog(rd.have) prog(rd.need) prog(rd.done and 1 or 0) prog(rd.pins and #rd.pins or 0)
      end
    end
  end
  for i = n + 1, #sigParts do sigParts[i] = nil end
  for i = m + 1, #progParts do progParts[i] = nil end
  local sgS = tconcat(sigParts, "\031")
  return sgS, sgS .. "\030" .. tconcat(progParts, "\031")
end

--- même structure qu'au dernier rendu : la progression est réécrite sur place, dans le même ordre que la mise en page
function W.Refill(model, me)
  W.refills = (W.refills or 0) + 1
  local n = (model.n == 0) and 1 or 0
  for i = 1, #live do live[i] = nil end
  local count = 0
  for _, gr in ipairs(model.groups) do
    n = n + 1                                  -- en-tête de zone
    for _, qe in ipairs(gr.quests) do
      n = n + 1
      local qr = rows[n]
      if not (qr and qr.kind == "quest") then W._force = true return W.Render() end
      qr.qe = qe
      for _, rd in ipairs(qe.rows) do
        n = n + 1
        local orow = rows[n]
        if not (orow and orow.kind == "obj") then W._force = true return W.Render() end
        paintObj(orow, rd)
        orow.qe, orow.rowd, orow.key = qe, rd, nil
        if rd.pins and not rd.done then live[#live + 1] = orow end
        count = count + 1
        if n >= (W.rowsShown or 0) then break end
      end
      if n >= (W.rowsShown or 0) then break end
    end
    if n >= (W.rowsShown or 0) then break end
  end
  W.UpdateTexts(me, true)
  return count
end

--- distances vivantes : seulement les textes dont la valeur arrondie a changé
function W.UpdateTexts(me, force)
  me = me or playerWorld()
  local writes = 0
  for i = 1, #live do
    local r = live[i]
    local rd = r.rowd
    if rd and rd.pins then
      local p = nearest(me, rd.pins)
      local txt, key
      if me and p then txt, key = W.DistText(me, p) end
      if force or key ~= r.key then
        r.key = key
        r.rt:SetText(txt or "")
        writes = writes + 1
      end
    end
  end
  W.textWrites = W.textWrites + writes
  W.ticks = W.ticks + 1
  return writes
end

-- ----------------------------------------------------- infobulle, clics, menu
function W.RowTip(r)
  local tt = g("GameTooltip")
  if not (tt and r) then return end
  try(tt.SetOwner, tt, r, "ANCHOR_LEFT")
  if r.kind == "questie" then
    try(tt.AddLine, tt, "Suivi de Questie", 1, 0.82, 0.2)
    try(tt.AddLine, tt, "Coupe le suivi, les icônes et le bouton de minimap de Questie dans SES options ; sa base de quêtes reste lue par TreeAI. L'interface se recharge.", 0.9, 0.9, 0.9, true)
    try(tt.Show, tt)
    return
  end
  if r.kind == "paused" then
    try(tt.AddLine, tt, "En pause (pilote auto)", 1, 0.82, 0.2)
    local B = T.Brain
    local list = B and type(B.ParkedList) == "function" and try(B.ParkedList) or {}
    for i, e in ipairs(list) do
      if i > 8 then break end
      try(tt.AddDoubleLine, tt, cut(tostring(e.n or ("#" .. tostring(e.q))), 30), cut(tostring(e.t or e.why or ""), 30),
        0.9, 0.9, 0.9, 0.7, 0.7, 0.7)
    end
    try(tt.AddLine, tt, "Clic : la page « Pilote auto » (Reprendre)", 0.6, 0.8, 1)
    try(tt.Show, tt)
    return
  end
  local qe = r.qe
  if not qe then try(tt.Hide, tt) return end
  local q = qe.q
  try(tt.AddLine, tt, ((tonumber(q.level) or 0) > 0 and ("[" .. q.level .. "] ") or "") .. tostring(q.title), 1, 1, 1, true)
  if qe.stop then try(tt.AddLine, tt, fmt("Arrêt %d du trajet", qe.stop), 0.35, 0.8, 1) end
  local rd = r.rowd
  if rd and rd.pins then
    local me = playerWorld()
    local p = nearest(me, rd.pins)
    local d = me and p and W.EdgeDist(me, p)
    if d then
      try(tt.AddLine, tt, fmt("%s jusqu'à la zone, ≈ %d min à pied", meters(d), max(1, floor(etaSec(d, p.map) / 60 + 0.5))), 0.6, 0.8, 1, true)
    end
    if #rd.pins > 1 then try(tt.AddLine, tt, fmt("%d coins possibles : le plus proche est compté", #rd.pins), 0.7, 0.7, 0.7, true) end
  end
  try(tt.AddLine, tt, "Clic : la boussole vise · clic droit : menu", 0.4, 0.8, 1, true)
  try(tt.Show, tt)
end

function W.Guide(qe, rd)
  local C = T.Compass
  if not (qe and type(C) == "table") then return nil end
  if type(C.SetQuestTarget) == "function" then
    local t = try(C.SetQuestTarget, qe.q.id)
    if t then return t end
  end
  local p = rd and rd.pins and nearest(playerWorld(), rd.pins)
  if p and type(C.SetTarget) == "function" then
    return try(C.SetTarget, C, { mapID = p.map, x = p.x, y = p.y, label = cut(rd.text or qe.q.title, 40), source = "watch" })
  end
  return nil
end

function W.RowClick(r, btn)
  if r.kind == "questie" then
    local AD = T.Addons
    if AD and type(AD.SilenceQuestie) == "function" then T:UserAction(AD.SilenceQuestie, true) end
    return
  end
  if btn == "RightButton" then W.OpenMenu(r) return end
  if r.kind == "more" then
    local Wn = T.Window
    if Wn and Wn.Open then T:UserAction(Wn.Open, "quests") end
    return
  end
  if r.kind == "paused" then
    local B = T.Brain
    if B and type(B.OpenPage) == "function" then T:UserAction(B.OpenPage) end
    return
  end
  if r.qe then T:UserAction(W.Guide, r.qe, r.rowd) end
end

-- menu (cadre à nous) : guider, ignorer dans le trajet, partager, retirer du suivi, abandonner (page Journal)
local menu
function W.MenuEntries(qe)
  local out = {}
  local hidden = W.char and W.char.hidden or {}
  if qe then
    local q = qe.q
    out[#out + 1] = { text = cut(q.title, 34), head = true }
    out[#out + 1] = { text = "Guider ici (boussole)", fn = function() W.Guide(qe, nil) end }
    local Z = T.ZonePlan
    if Z and type(Z.Ignore) == "function" and qe.stop then
      out[#out + 1] = { text = "Ignorer dans le trajet", fn = function() pcall(Z.Ignore, Z, q.id, q.title) end }
    end
    local push = g("QuestLogPushQuest")
    local inGroup = (num(try(g("GetNumGroupMembers"))) or 0) > 1
    if push and inGroup and q.index and not q.complete then
      out[#out + 1] = { text = "Partager au groupe", fn = function() if not inCombat() then try(push, q.index) end end }
    end
    out[#out + 1] = { text = "Retirer du suivi", fn = function() if W.char then W.char.hidden[q.id] = true end W.Now() end }
    out[#out + 1] = { text = "Abandonner… (page Journal)", fn = function()
      local Wn = T.Window
      if Wn and Wn.Open then Wn.Open("journal") end
    end }
  end
  if next(hidden) then
    out[#out + 1] = { text = "Tout réafficher dans le suivi", fn = function() if W.char then W.char.hidden = {} end W.Now() end }
  end
  out[#out + 1] = { text = (tonumber(W.db and W.db.sort) or 1) == 2 and "Ordre du trajet" or "Par distance", fn = W.ToggleSort }
  return out
end
function W.OpenMenu(r)
  local entries = W.MenuEntries(r and r.qe)
  local CF, up = g("CreateFrame"), g("UIParent")
  if not (CF and up) then return nil end
  if not menu then
    menu = CF("Frame", "TreeAIWatchMenu", up)
    menu:SetFrameStrata("DIALOG")
    try(menu.SetClampedToScreen, menu, true)
    menu:EnableMouse(true)
    local th = Th()
    if th and th.Skin then try(th.Skin, th, menu, "window") end
    menu.rows = {}
    local list = g("UISpecialFrames")
    if type(list) == "table" then list[#list + 1] = "TreeAIWatchMenu" end
    if menu.RegisterEvent and pcall(menu.RegisterEvent, menu, "GLOBAL_MOUSE_DOWN") then
      menu:SetScript("OnEvent", function(self)
        if self:IsShown() and not (self.IsMouseOver and self:IsMouseOver()) then self:Hide() end
      end)
    end
    menu:Hide()
  end
  for _, b in ipairs(menu.rows) do b:Hide() b.e = nil end
  local y = -6
  for i, e in ipairs(entries) do
    local b = menu.rows[i]
    if not b then
      b = CF("Button", nil, menu)
      b:SetHeight(18)
      try(b.RegisterForClicks, b, "LeftButtonUp")
      b.t = b:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
      b.t:SetPoint("LEFT", b, "LEFT", 8, 0)
      try(b.t.SetJustifyH, b.t, "LEFT")
      local hl = b:CreateTexture(nil, "HIGHLIGHT")
      hl:SetAllPoints(b)
      if hl.SetColorTexture then hl:SetColorTexture(1, 1, 1, 0.08) end
      b:SetScript("OnClick", function(self)
        local en = self.e
        menu:Hide()
        if en and en.fn then T:UserAction(en.fn) end
      end)
      menu.rows[i] = b
    end
    b.e = e
    b:ClearAllPoints()
    b:SetPoint("TOPLEFT", menu, "TOPLEFT", 4, y)
    b:SetPoint("TOPRIGHT", menu, "TOPRIGHT", -4, y)
    b.t:SetText(e.text)
    if e.head then b.t:SetTextColor(1, 0.82, 0.2) else b.t:SetTextColor(1, 1, 1) end
    b:EnableMouse(e.fn ~= nil)
    b:Show()
    y = y - 18
  end
  menu:SetSize(220, -y + 6)
  menu:ClearAllPoints()
  local cx, cy = try(g("GetCursorPosition"))
  local sc = num(meth(up, "GetEffectiveScale")) or 1
  if tonumber(cx) and tonumber(cy) and sc > 0 and (cx > 0 or cy > 0) then menu:SetPoint("TOPLEFT", up, "BOTTOMLEFT", cx / sc + 6, cy / sc - 4)
  elseif r then menu:SetPoint("TOPRIGHT", r, "TOPLEFT", -4, 0)
  else menu:SetPoint("CENTER", up, "CENTER", 0, 0) end
  menu:Show()
  W.menu = menu
  return entries
end

--- réponse à un clic : tout de suite (pas d'attente du regroupement des événements)
function W.Now()
  W._force = true
  W.Render()
end
function W.ToggleSort()
  if not W.db then return end
  W.db.sort = ((tonumber(W.db.sort) or 1) == 2) and 1 or 2
  W.Now()
end

-- ===================================================================== remplacer le suivi du jeu
local hooked = {}
local function editModeOpen()
  local em = g("EditModeManagerFrame")
  return type(em) == "table" and meth(em, "IsShown") and true or false
end
local function wantReplace() return W._active and opt("shown") and opt("replace") and not editModeOpen() end
W.WantReplace = wantReplace
local function hideTracker(tf)
  if not tf then return end
  if inCombat() then
    -- en combat : jamais Hide (ses boutons d'objet sont sécurisés) ; invisible, puis caché après le combat
    meth(tf, "SetAlpha", 0)
    if T.RunOutOfCombat then T:RunOutOfCombat("Watch:hideTracker", function() if wantReplace() then W.TakeOver() end end) end
    return
  end
  if meth(tf, "IsShown") then meth(tf, "Hide") end
  meth(tf, "SetAlpha", 0)
  W.replaced = true
end
--- cache le suivi du jeu (hors combat) + crochet OnShow qui le recache tant que l'option est là
function W.TakeOver()
  local tf = trackerFrame()
  if not tf then return false end
  if not hooked[tf] and type(tf.HookScript) == "function" then
    hooked[tf] = true
    pcall(tf.HookScript, tf, "OnShow", function(self) if wantReplace() then hideTracker(self) end end)
  end
  local em = g("EditModeManagerFrame")
  if type(em) == "table" and type(em.HookScript) == "function" and not hooked[em] then
    hooked[em] = true
    -- mode édition du jeu : ouvert, le suivi du jeu revient (le joueur peut le régler) ; fermé, il se recache
    pcall(em.HookScript, em, "OnHide", function() if wantReplace() then W.TakeOver() end end)
    pcall(em.HookScript, em, "OnShow", function() if W.replaced then W.Restore(true) end end)
  end
  if not wantReplace() then return false end
  hideTracker(tf)
  return true
end
--- rend le suivi du jeu tel quel (option coupée, Suivi TreeAI caché ou coupé, mode édition ouvert)
function W.Restore(keepFlag)
  local tf = trackerFrame()
  if not (tf and (W.replaced or (num(meth(tf, "GetAlpha")) or 1) < 0.05)) then return false end
  local function back()
    meth(tf, "SetAlpha", 1)
    if not meth(tf, "IsShown") then meth(tf, "Show") end
    if not keepFlag then W.replaced = false end
  end
  if inCombat() then
    meth(tf, "SetAlpha", 1)                       -- l'alpha tout de suite ; Show après le combat
    if T.RunOutOfCombat then T:RunOutOfCombat("Watch:restoreTracker", back) end
  else
    back()
  end
  return true
end

-- ===================================================================== cycle de vie
local soonAt
function W.Soon()
  if soonAt then return end
  soonAt = true
  local function run() soonAt = nil if W._active then W.Render() end end
  if T.After then T:After(0.5, run) else run() end
end
function W.Apply()
  if not W._active then return end
  if opt("shown") then
    W.Render()
    W.TakeOver()
  else
    if frame then
      if inCombat() then meth(frame, "SetAlpha", 0) T:RunOutOfCombat("Watch:hide", function() if frame and not (W._active and opt("shown")) then frame:Hide() meth(frame, "SetAlpha", 1) end end)
      else frame:Hide() end
    end
    W.PlaceItems()
    W.Restore()
  end
end

-- distances vivantes : 1 lecture / s en marchant, rien à l'arrêt (le cœur réveille au mouvement)
local driver, acc = nil, 0
local lastSort = -100
local function onUpdate(_, el)
  acc = acc + (el or 0)
  if acc < 1 then return end
  acc = 0
  if not (frame and frame:IsShown()) or #live == 0 then W.Sleep() return end
  local moving = T.Moving and T:Moving(2) or false
  if not moving then W.idleN = (W.idleN or 0) + 1 if W.idleN >= 2 then W.Sleep() end return end
  W.idleN = 0
  local me = playerWorld()
  W.UpdateTexts(me)
  -- par distance : l'ordre est revu toutes les 4 s en marchant (mise en page hors combat seulement)
  if (tonumber(W.db and W.db.sort) or 1) == 2 and not inCombat() and now() - lastSort >= 4 then
    lastSort = now()
    local m = W.Model(me)
    local a, b = {}, {}
    for _, gr in ipairs(m.groups) do for _, qe in ipairs(gr.quests) do a[#a + 1] = qe.q.id end end
    for _, gr in ipairs(W.model and W.model.groups or {}) do for _, qe in ipairs(gr.quests) do b[#b + 1] = qe.q.id end end
    if tconcat(a, ",") ~= tconcat(b, ",") then W.Render() end
  end
end
function W.Sleep()
  if driver and not W.asleep then driver:SetScript("OnUpdate", nil) W.asleep = true end
end
function W.Wake()
  W.idleN = 0
  if driver and W.asleep and W._active and frame and frame:IsShown() and #live > 0 then
    W.asleep, acc = false, 1
    driver:SetScript("OnUpdate", onUpdate)
  end
end

function W:OnEnable()
  W._active = true
  if not driver and g("CreateFrame") then
    driver = g("CreateFrame")("Frame")
    W.driver = driver
  end
  W.asleep = true
  self:On("QUESTS_UPDATED", function() W.Soon() end)
  self:On("EXPORT_UPDATED", function() W.Soon() end)
  self:On("PLAN_BUILT", function() W.Soon() end)
  self:On("BRAIN_PARKED", function() W.Soon() end)       -- (BRAIN) quêtes mises en pause / reprises
  for _, ev in ipairs({ "QUEST_ACCEPTED", "QUEST_REMOVED", "QUEST_TURNED_IN", "ZONE_CHANGED_NEW_AREA", "PLAYER_LEVEL_UP" }) do
    self:RegisterEvent(ev, function() W.Soon() end)
  end
  self:RegisterEvent("PLAYER_REGEN_ENABLED", function()
    if W._pendingLayout then W.Render() end
    if wantReplace() then W.TakeOver() end
  end)
  self:RegisterEvent("PLAYER_ENTERING_WORLD", function() W.Soon() if wantReplace() then W.TakeOver() end end)
  if T.OnMotion then T:OnMotion(W, W.Wake) end
  T:RunOutOfCombat("Watch:start", function() W.Apply() W.Wake() end)
end
function W:OnDisable()
  W._active = false
  if driver then driver:SetScript("OnUpdate", nil) end
  W.asleep = true
  if frame then
    if inCombat() then
      meth(frame, "SetAlpha", 0)
      T:RunOutOfCombat("Watch:hide", function() if frame and not W._active then frame:Hide() meth(frame, "SetAlpha", 1) end end)
    else frame:Hide() end
  end
  for i = 1, #wantItems do wantItems[i] = nil end
  W.PlaceItems()
  W.Restore()
  if T.OnMotion then T:OnMotion(W, nil) end
end
W.autoIdleSafe = true
function W:OnOptionChanged(key)
  if key == "replace" or key == "shown" then
    if opt("shown") and opt("replace") then W.TakeOver() else W.Restore() end
  end
  if key == "shown" then W.Apply() return end
  W._force = true
  W.Render()
  W.Wake()
end

function W.Status()
  if not W._active then return "Suivi TreeAI : coupé" end
  return fmt("Suivi TreeAI : %d quête(s), %d ligne(s) à distance vivante, ordre %s, suivi du jeu %s", W.model and W.model.n or 0,
    #live, sortLabel():lower(), W.replaced and "caché" or "visible")
end
if T.RegisterSlash then
  -- « /tai suivi » appartient au module Tracker (cadre, filtres, masquer) : le Suivi TreeAI a son propre mot
  T:RegisterSlash("monsuivi", function(rest)
    rest = tostring(rest or ""):lower()
    if rest == "" or rest == "on" or rest == "off" then
      if W.db then W.db.shown = (rest == "on") or (rest == "" and not opt("shown")) end
      W.Apply()
    end
    W:Print(W.Status())
  end, "monsuivi [on|off] : le Suivi TreeAI (remplace le suivi du jeu)")
end
