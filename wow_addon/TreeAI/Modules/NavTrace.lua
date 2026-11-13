-- TreeAI — Journal de la boussole (agent ZONEFIX, 4.0.1) : ce que la boussole et le trajet ont décidé, pour comprendre
-- une vraie session (« la boussole c'est de la merde » : laquelle, quand, pourquoi ?).
--  * Un anneau des 300 derniers évènements (30 Ko au plus) dans TreeAIDB.diag.nav, écrit seulement quand quelque chose
--    CHANGE (jamais par image) et hors combat (en combat : gardé en mémoire, écrit à la fin du combat) : cible (avant →
--    après, pourquoi : arrêt du trajet, corps, à la main, recalcul…), boussole cachée / remontrée (raison), trajet refait
--    (déclencheur, arrêts, 1er arrêt, zone, mode), arrêt pris en passant, temps prévu / réel de chaque trajet
--    chronométré (Temps de trajet), cadence vraiment obtenue en marchant (flèche, texte).
--  * Lu par « /tai erreurs » (rapport) et par l'appli (TreeAI-erreurs.txt, section « Trajet / boussole ») ; visible dans
--    la fenêtre : Outils → « Journal de la boussole ».
-- Lecture seule, aucune fonction protégée, aucun cadre Blizzard. Lua 5.1, aucune globale.
local tonumber = function(v) return _G.tonumber(v) end   -- (4.0.8) tonumber(f()) : un 2e retour de f n'est jamais une base
local T = _G.TreeAI
if type(T) ~= "table" then return end

local NT = {}
T.NavTrace = NT
NT.MAX, NT.MAX_BYTES, NT.PENDING, NT.SAME_S = 300, 30000, 40, 10

local function pass(ok, ...) if ok then return ... end return nil end
local function try(fn, ...) if type(fn) ~= "function" then return nil end return pass(pcall(fn, ...)) end
local function G(n) return rawget(_G, n) end
local function inCombat() return T.InCombat and T:InCombat() or false end
local function clock() return tonumber(try(G("GetTime"))) or 0 end
local function stamp() return try(G("date"), "%d/%m %H:%M:%S") or "" end
local function cut(s, n) s = tostring(s or "") if T.Cut then return T.Cut(s, n) end return s:sub(1, n) end

-- ------------------------------------------------------------------ l'anneau (TreeAIDB.diag.nav)
local counted = false
local function store()
  local db = T.db or G("TreeAIDB")
  if type(db) ~= "table" then return nil end
  db.diag = type(db.diag) == "table" and db.diag or {}
  local n = db.diag.nav
  if type(n) ~= "table" or type(n.lines) ~= "table" then n = { v = 1, lines = {}, bytes = 0 }; db.diag.nav = n end
  if not counted then                     -- une fois par session : la taille relue (fichier d'une autre version)
    counted = true
    local b = 0
    for i = #n.lines, 1, -1 do
      if type(n.lines[i]) ~= "string" then table.remove(n.lines, i) else b = b + #n.lines[i] + 1 end
    end
    n.bytes = b
  end
  return n
end
NT.Store = store

local pending = {}                        -- en combat : en mémoire seulement, écrit à la fin du combat
local last, lastAt = nil, -1e9
local function push(line)
  local n = store()
  if not n then return end
  local L = n.lines
  L[#L + 1] = line
  n.bytes = (tonumber(n.bytes) or 0) + #line + 1
  while #L > 1 and (#L > NT.MAX or n.bytes > NT.MAX_BYTES) do
    n.bytes = n.bytes - #L[1] - 1
    table.remove(L, 1)
  end
end
function NT.Flush()
  if inCombat() or #pending == 0 then return 0 end
  local k = #pending
  for i = 1, k do push(pending[i]); pending[i] = nil end
  return k
end
--- un évènement (sur un changement d'état seulement) : kind = « cible », « boussole », « trajet », « temps »,
--- « marche » ; texte court (≤ 160 caractères). La même ligne dans les 10 s : gardée une fois.
function NT.Log(kind, text)
  if NT.off then return false end
  text = tostring(text or "")
  local key = tostring(kind) .. "|" .. text
  local t = clock()
  if key == last and t - lastAt < NT.SAME_S then return false end
  last, lastAt = key, t
  local line = stamp() .. " " .. tostring(kind) .. " · " .. cut(text, 160)
  if inCombat() then
    if #pending < NT.PENDING then pending[#pending + 1] = line .. " (combat)" end
    return true
  end
  NT.Flush()
  push(line)
  return true
end
function NT.Pending() return #pending end
--- les n dernières lignes, les plus récentes d'abord (celles du combat en cours comprises)
function NT.Lines(n)
  local s = store()
  local L = s and s.lines or {}
  local out = {}
  n = n or (#L + #pending)
  for i = #pending, 1, -1 do if #out < n then out[#out + 1] = pending[i] end end
  for i = #L, 1, -1 do if #out >= n then break end out[#out + 1] = L[i] end
  return out
end
--- rapport « /tai erreurs » (Bugs) : un titre + les n dernières lignes (tout est aussi dans la section de l'appli)
function NT.ExportLines(n)
  local s = store()
  local k = s and #s.lines or 0
  local out = { ("== Trajet / boussole : %d évènement%s (les %d derniers ; tout dans « Trajet / boussole »)"):format(k,
    k > 1 and "s" or "", math.min(k, n or 20)) }
  for _, l in ipairs(NT.Lines(n or 20)) do out[#out + 1] = l end
  -- (5.1 ROUTE6) d'où viennent les temps de trajet (Shortest Path / chemins appris / facteur de zone), les tables de Questie
  -- qui répondent, et les « mauvais conseils » notés par le joueur (étape, position, alternatives, raisons)
  local NTm = T.NavTime
  for _, l in ipairs(NTm and NTm.CostLines and try(NTm.CostLines) or {}) do out[#out + 1] = cut(l, 280) end
  for _, l in ipairs(NT.BadLines and NT.BadLines(6) or {}) do out[#out + 1] = l end
  return out
end
-- ------------------------------------------------------------------ (5.1 ROUTE6, audit D5) « Mauvais conseil »
-- « des fois tu conseilles mal » : un clic sur l'arrêt en cours (clic droit → « Mauvais conseil », ou /tai mauvais) note CE
-- QUE TreeAI disait et pourquoi — l'arrêt, ta position, les arrêts voisins qu'il n'a pas choisis, les raisons du calcul
-- (ordre gardé par l'hystérésis ou neuf, solveur exact / heuristique, prises sur place, source des temps de trajet) — dans
-- TreeAIDB.diag.bad (20 derniers) ; le rapport d'erreurs les emporte. Aucun nom de joueur, rien dans le chat.
NT.BAD_MAX = 20
local function badStore()
  local db = T.db or G("TreeAIDB")
  if type(db) ~= "table" then return nil end
  db.diag = type(db.diag) == "table" and db.diag or {}
  if type(db.diag.bad) ~= "table" then db.diag.bad = {} end
  return db.diag.bad
end
--- note le conseil en cours (s = l'arrêt visé, nil = l'arrêt 1) → l'entrée notée, ou nil sans trajet
function NT.BadAdvice(s, note)
  local B0 = badStore()
  local P, Z, R, QB, Geo, NTm = T.Planner, T.ZonePlan, T.Route, T.QuestBrain, T.Geo, T.NavTime
  if not (B0 and P and P.steps) then return nil end
  s = (type(s) == "table" and s) or P.steps[P.cursor or 0]
  if not s then return nil end
  local m, x, y
  if Geo and Geo.Player then m, x, y = Geo.Player() end
  local function yd(p)
    local d = (m and x and p and p.x and Geo.Dist) and Geo.Dist(m, x, y, p.map, p.x, p.y) or nil
    return d and math.floor(d + 0.5) or nil
  end
  local e = { t = stamp(), step = cut(P:StepLabel(s), 60), k = s.k, q = tonumber(s.q), note = note and cut(note, 120) or nil,
    pos = (m and x) and ("%d:%.1f:%.1f"):format(m, x, y) or "?", at = s.x and ("%s:%.1f:%.1f"):format(tostring(s.map), s.x, s.y) or "sans lieu",
    dist = yd(s), alts = {}, why = {} }
  -- les autres arrêts du trajet, les plus proches d'abord : ce qu'il aurait pu dire à la place
  local alts = {}
  local n = 0
  for i = P.cursor or 1, #P.steps do
    local a = P.steps[i]
    if not a._done and not a._other then
      n = n + 1
      if a ~= s and a.x then alts[#alts + 1] = { d = yd(a) or 1e9, txt = ("n°%d %s"):format(n, cut(P:StepLabel(a), 40)) } end
      if a == s then e.rank = n end
    end
  end
  table.sort(alts, function(a, b) return a.d < b.d end)
  for i = 1, math.min(4, #alts) do e.alts[i] = ("%s à %s yd"):format(alts[i].txt, alts[i].d < 1e9 and alts[i].d or "?") end
  -- codes de raison
  local w = e.why
  if Z then
    w[#w + 1] = "zone=" .. cut(tostring(Z.label or "?"), 30) .. "/" .. tostring(Z.char and Z.char.mode or "auto")
    w[#w + 1] = "ordre=" .. tostring(Z.kept or "?") .. "/" .. tostring(Z.solveHow or Z.solver or "?")
    if Z.firstWhy then w[#w + 1] = "arrêt1=" .. Z.firstWhy end
    if Z.hubPicks then w[#w + 1] = "prisesIci=" .. tostring(Z.hubPicks) end
    if Z.onWayPicks then w[#w + 1] = "enChemin=" .. #Z.onWayPicks end
    w[#w + 1] = "réordonné=" .. tostring(Z.reorders or 0)
    if s.onWay then w[#w + 1] = "arrêt en chemin" end
    if s.out then w[#w + 1] = "hors zone" end
  end
  if QB and QB.Mode then w[#w + 1] = "mode=" .. tostring(QB.Mode()) end
  if P._why then w[#w + 1] = "refait=" .. table.concat(P._why, "+") end
  if NTm and NTm.Detour and m then
    local v, _, src = NTm.Detour(m)
    w[#w + 1] = ("détour=%.2f(%s)"):format(v, tostring(src))
  end
  if R and R.Stops then
    local okS, st = pcall(R.Stops, R, 64)
    if okS and type(st) == "table" then
      for _, en in ipairs(st) do
        if en.step == s then e.eta = math.floor((en.at or 0) - (en.sec or 0) + 0.5) e.src = en.src or "modèle" break end
      end
    end
  end
  B0[#B0 + 1] = e
  while #B0 > NT.BAD_MAX do table.remove(B0, 1) end
  NT.Log("mauvais conseil", ("%s (n°%s, à %s yd) noté"):format(e.step, tostring(e.rank or "?"), tostring(e.dist or "?")))
  if T.ClickNote then pcall(T.ClickNote, T, "Noté : ce conseil part dans le rapport (/tai erreurs). Merci.") end
  return e
end
--- lignes du rapport : les n derniers conseils notés (les plus récents d'abord)
function NT.BadLines(n)
  local B0 = badStore() or {}
  local out = { ("== Mauvais conseils notés : %d"):format(#B0) }
  for i = #B0, math.max(1, #B0 - (n or 6) + 1), -1 do
    local e = B0[i]
    if type(e) == "table" then
      out[#out + 1] = cut(("[%s] %s (%s q%s, arrêt n°%s) · toi %s → %s, %s yd, prévu %s s (%s)%s"):format(tostring(e.t), tostring(e.step),
        tostring(e.k), tostring(e.q or "-"), tostring(e.rank or "?"), tostring(e.pos), tostring(e.at), tostring(e.dist or "?"),
        tostring(e.eta or "?"), tostring(e.src or "?"), e.note and (" · note : " .. e.note) or ""), 280)
      out[#out + 1] = cut("   raisons : " .. table.concat(type(e.why) == "table" and e.why or {}, " ; "), 280)
      if type(e.alts) == "table" and #e.alts > 0 then out[#out + 1] = cut("   autres arrêts proches : " .. table.concat(e.alts, " | "), 280) end
    end
  end
  return out
end
if T.RegisterSlash then
  T:RegisterSlash("mauvais", function(rest)
    rest = tostring(rest or ""):gsub("^%s+", ""):gsub("%s+$", "")
    local e = NT.BadAdvice(nil, rest ~= "" and rest or nil)
    if not e and T.ClickNote then pcall(T.ClickNote, T, "Pas de trajet en cours : rien à noter.") end
  end, "mauvais [note] : noter que le conseil en cours est mauvais (part dans /tai erreurs)")
end

function NT.Clear()
  local s = store()
  if s then s.lines, s.bytes = {}, 0 end
  for i = #pending, 1, -1 do pending[i] = nil end
  last = nil
end

-- textes courts communs
function NT.Sec(sec)
  sec = tonumber(sec)
  if not sec then return "?" end
  sec = math.floor(sec + 0.5)
  if sec < 60 then return sec .. " s" end
  if sec < 3600 then return ("%d min %02d"):format(math.floor(sec / 60), sec % 60) end
  return ("%d h %02d"):format(math.floor(sec / 3600), math.floor(sec % 3600 / 60))
end
function NT.Target(t)
  if type(t) ~= "table" then return "aucune" end
  return cut(t.label or t.title or t.key or "?", 48)
end

-- ------------------------------------------------------------------ fin de combat, page de la fenêtre, pastille
if T.On then
  T:On("COMBAT_END", "NavTrace", function() NT.Flush() end)
end
local ICON = "Interface\\Icons\\"
local hubEntry = { group = "outils", id = "navlog", icon = ICON .. "INV_Misc_Spyglass_02", title = "Journal de la boussole",
  desc = "Ce que la boussole et le trajet ont décidé (cible, cachée, trajet refait, temps prévu / réel)",
  tip = "Clic : ouvrir le journal (il part aussi dans /tai erreurs)",
  fn = function() local W = T.Window if W and W.Open then W.Open("navlog") end end }
NT.HubEntry = hubEntry
function NT.Register()
  T.HUB_EXTRA = type(T.HUB_EXTRA) == "table" and T.HUB_EXTRA or {}
  local have = false
  for _, e in ipairs(T.HUB_EXTRA) do if e == hubEntry then have = true end end
  if not have then T.HUB_EXTRA[#T.HUB_EXTRA + 1] = hubEntry end
  local W = T.Window
  if not (W and W.AddPage) or NT._page then return end
  NT._page = true
  local page = { id = "navlog", hidden = true, label = "Journal de la boussole", icon = hubEntry.icon,
    title = "Journal de la boussole", desc = "Les décisions de la boussole et du trajet, les plus récentes d'abord",
    render = function(Ly)
      Ly.text("Ce que la boussole et le trajet ont décidé, et pourquoi. Gardé pour « /tai erreurs » (TreeAI-erreurs.txt).",
        "S", "muted")
      local L = NT.Lines(100)
      if #L == 0 then
        Ly.empty("Rien pour l'instant : la boussole note ici chaque changement (cible, trajet refait, temps de trajet).")
        return
      end
      for _, l in ipairs(L) do Ly.text(l, "S") end
    end }
  W.AddPage(page)
  -- page cachée (ouverte depuis Outils) : rangée avant « Options », qui reste la dernière de la navigation
  local P = W.PAGES
  if type(P) == "table" then
    for i = #P, 1, -1 do if P[i] == page then table.remove(P, i) break end end
    local at = #P + 1
    for i, q in ipairs(P) do if type(q) == "table" and q.id == "options" then at = i break end end
    table.insert(P, at, page)
  end
end
if T.On then T:On("READY", "NavTrace:page", function() NT.Register() end) end
NT.Register()
