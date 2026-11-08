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
  return out
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
