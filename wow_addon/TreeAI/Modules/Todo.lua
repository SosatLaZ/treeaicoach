-- TreeAI — « À faire » (agent TODO) : la page de suivi refaite de zéro. Une seule fenêtre propre (déplaçable,
-- redimensionnable, TreeAI:RegisterMovable si présent) + l'onglet « Quêtes » en version compacte.
-- Sections : Maintenant (LA meilleure action, grande, avec pourquoi + temps + boussole / Y aller en un clic) ·
-- Étapes du trajet (plan TreeAI actif) · Quêtes par zone (cartes repliables : titre couleur de niveau, barres par
-- objectif, temps, distance, badge « À rendre », avatars + progression des amis, récompense + % d'amélioration,
-- verdict Abandon garder / plus tard / abandonner avec la raison au survol) · Courses (marchand, réparation,
-- formation, foyer, munitions / nourriture — Predict) · Groupe (mini-tableau Missions) · barre de session
-- (XP/h, niveau suivant). Cases à cocher épingler / masquer, glisser-déposer (ou ▲▼) pour l'ordre (sauvé par
-- perso), filtres en un clic, recherche facultative ; tout se fait à la souris.
-- Données LUES seulement, via les API gardées des modules : Tracker (modèle de coût, items triés), Quests,
-- Planner:GetActiveSteps (via Tracker.PlanSteps), Abandon (evals), Predict (list, rates), Squad (classes),
-- Missions (board, next), Boost (XPStats). Aucun cadre / aucune fonction de Blizzard touché (pas de suivi ni de
-- journal de quêtes Blizzard, aucune sélection) ; cadres simples enfants d'UIParent, aucun modèle sécurisé.
-- Rafraîchissement : à chaque Push du Tracker (évènementiel) + :AutoRefresh() ; ≤ 2 rendus/s, mise en page
-- refaite seulement si le contenu change. Lua 5.1, aucune globale.
local tonumber = function(v) return _G.tonumber(v) end   -- (4.0.8) tonumber(f()) : un 2e retour de f n'est jamais une base
local _, ns = ...
ns = ns or {}

local T = _G.TreeAI
if not (T and T.RegisterModule) then return end

local M = T:RegisterModule("Todo", {
  title = "À faire",
  defaults = { enabled = true, shown = false, tab = true, lock = false, fontSize = 12, scale = 100, alpha = 88,
               width = 380, height = 520, maxTab = 8, steps = true, errands = true, group = true, session = true,
               verdict = true, reward = true, mates = true },
  charDefaults = { point = "CENTER", x = 0, y = 40, pins = {}, hidden = {}, order = {}, folded = {}, sections = {},
                   filter = "tous", search = "" },
  options = {
    { key = "shown", type = "toggle", label = "Fenêtre « À faire » affichée (/tai todo)", section = "Fenêtre" },
    { key = "lock", type = "toggle", label = "Verrouiller (ni déplacer ni redimensionner)", section = "Fenêtre" },
    { key = "fontSize", type = "range", label = "Taille du texte", min = 10, max = 16, step = 1, section = "Fenêtre" },
    { key = "scale", type = "range", label = "Échelle (%)", min = 60, max = 150, step = 5, section = "Fenêtre" },
    { key = "alpha", type = "range", label = "Opacité du fond (%)", min = 30, max = 100, step = 5, section = "Fenêtre" },
    { key = "tab", type = "toggle", label = "Onglet « Quêtes » : version compacte d'« À faire »", section = "Onglet Quêtes" },
    { key = "maxTab", type = "range", label = "Quêtes listées dans l'onglet", min = 3, max = 15, step = 1, section = "Onglet Quêtes" },
    { key = "steps", type = "toggle", label = "Étapes du trajet (plan TreeAI actif)", section = "Sections" },
    { key = "errands", type = "toggle", label = "Courses (marchand, réparation, formation, foyer, munitions)", section = "Sections" },
    { key = "group", type = "toggle", label = "Groupe (mini-tableau des missions)", section = "Sections" },
    { key = "session", type = "toggle", label = "Barre de session (niveau suivant)", section = "Sections" },
    { key = "verdict", type = "toggle", label = "Verdict garder / plus tard / abandonner", section = "Cartes de quête" },
    { key = "reward", type = "toggle", label = "Récompense + % d'amélioration", section = "Cartes de quête" },
    { key = "mates", type = "toggle", label = "Avatars et progression des amis", section = "Cartes de quête" },
  },
})
ns.Todo = M
T.Todo = M

local floor, min, max = math.floor, math.min, math.max

local function try(fn, ...)
  if type(fn) ~= "function" then return nil end
  local ok, a, b, c, d = pcall(fn, ...)
  if ok then return a, b, c, d end
  return nil
end
local function now() return try(_G.GetTime) or 0 end
local cut = T.Cut   -- (4.0) Core : n caractères UTF-8 (« … » compris), jamais une lettre accentuée coupée
local function low(s) return tostring(s or ""):lower() end
local function opt(k, def) local v = M.db and M.db[k] if v == nil then return def end return v end
local function on(k) return not (M.db and M.db[k] == false) end
local function mod(name) return T.GetModule and T:GetModule(name) or nil end
local function secret(v) local f = rawget(_G, "issecretvalue") return type(f) == "function" and f(v) and true or false end
local function shortName(n) return (tostring(n or "?"):gsub("%-.*$", "")) end
local function inCombat() return T.InCombat and T:InCombat() or false end
local function shift() return _G.IsShiftKeyDown and try(_G.IsShiftKeyDown) and true or false end
local function fmtNum(n)
  n = tonumber(n) or 0
  if n >= 10000 then return (("%.1fk"):format(n / 1000):gsub("%.", ",")) end
  return tostring(floor(n + 0.5))
end
local function Time(sec)
  local Tr = mod("Tracker")
  if Tr and Tr.Time then return Tr.Time(sec) end
  sec = tonumber(sec) or 0
  return sec < 60 and (floor(sec) .. " s") or (floor(sec / 60 + 0.5) .. " min")
end
M.Time = Time

-- ------------------------------------------------------------------ apparence (textures Blizzard, couleurs)
local ICON = {
  now = "Interface\\Icons\\Ability_Hunter_Pathfinding",
  quest = "Interface\\GossipFrame\\AvailableQuestIcon",
  ready = "Interface\\GossipFrame\\ActiveQuestIcon",
  step = "Interface\\Icons\\INV_Misc_Map_01",
  pick = "Interface\\GossipFrame\\AvailableQuestIcon", turn = "Interface\\GossipFrame\\ActiveQuestIcon",
  obj = "Interface\\Icons\\Ability_Hunter_SniperShot", fly = "Interface\\Icons\\Ability_Mount_Wyvern_01",
  hs = "Interface\\Icons\\INV_Misc_Rune_01", train = "Interface\\Icons\\INV_Misc_Book_09",
  vendor = "Interface\\Icons\\INV_Misc_Bag_08",
  ammo = "Interface\\Icons\\INV_Ammo_Arrow_02", food = "Interface\\Icons\\INV_Misc_Food_15",
  water = "Interface\\Icons\\INV_Drink_07", bags = "Interface\\Icons\\INV_Misc_Bag_08",
  repair = "Interface\\Icons\\Trade_BlackSmithing", hearth = "Interface\\Icons\\INV_Misc_Rune_01",
  errand = "Interface\\Icons\\INV_Misc_Coin_01", group = "Interface\\Icons\\INV_Banner_02",
  session = "Interface\\Icons\\INV_Misc_PocketWatch_01", quests = "Interface\\Icons\\INV_Misc_Book_08",
  reward = "Interface\\Icons\\INV_Misc_Bag_10",
  keep = "Interface\\RaidFrame\\ReadyCheck-Ready", later = "Interface\\RaidFrame\\ReadyCheck-Waiting",
  abandon = "Interface\\RaidFrame\\ReadyCheck-NotReady",
  box = "Interface\\Buttons\\UI-CheckBox-Up", check = "Interface\\Buttons\\UI-CheckBox-Check",
  hide = "Interface\\Buttons\\UI-GroupLoot-Pass-Up",
  up = "Interface\\Buttons\\Arrow-Up-Up", down = "Interface\\Buttons\\Arrow-Down-Up",
  plus = "Interface\\Buttons\\UI-PlusButton-Up", minus = "Interface\\Buttons\\UI-MinusButton-Up",
  close = "Interface\\Buttons\\UI-StopButton", grip = "Interface\\ChatFrame\\UI-ChatIM-SizeGrabber-Up",
  bar = "Interface\\TargetingFrame\\UI-StatusBar", compass = "Interface\\Minimap\\MiniMap-QuestArrow",
  go = "Interface\\Icons\\Ability_Rogue_Sprint", mate = "Interface\\Icons\\INV_Misc_QuestionMark",
  classes = "Interface\\Glues\\CharacterCreate\\UI-CharacterCreate-Classes",
}
M.ICON = ICON
local LEVEL_RGB = { gris = { 0.62, 0.62, 0.62 }, vert = { 0.25, 0.75, 0.25 }, jaune = { 1, 1, 0 },
                    orange = { 1, 0.5, 0.25 }, rouge = { 1, 0.13, 0.13 } }
local PRIO_RGB = { [3] = { 1, 0.35, 0.3 }, [2] = { 1, 0.82, 0 }, [1] = { 0.86, 0.86, 0.86 } }
local VERDICT = { keep = { "Garder", "|cff40d040" }, later = { "Plus tard", "|cffffd100" }, abandon = { "À abandonner", "|cffff5040" } }
M.LEVEL_RGB, M.PRIO_RGB, M.VERDICT = LEVEL_RGB, PRIO_RGB, VERDICT
local function hex(rgb) return ("|cff%02x%02x%02x"):format(floor(rgb[1] * 255), floor(rgb[2] * 255), floor(rgb[3] * 255)) end
local function tex(path, size) return ("|T%s:%d|t"):format(path, size or 14) end
M.Tex = tex
M.FILTERS = { "tous", "zone", "pretes", "groupe", "faciles", "epingles" }
M.FILTER_TXT = { tous = "Tout", zone = "Zone", pretes = "Prêtes", groupe = "Groupe", faciles = "Faciles", epingles = "Épinglées" }

-- ------------------------------------------------------------------ données (lecture seule, gardée)
-- nom court → classe (amis TreeAI via Squad, sinon membres du groupe)
local function classOf()
  local out = {}
  local Tr = mod("Tracker")
  for _, m in ipairs(Tr and Tr.SquadMates and try(Tr.SquadMates) or {}) do
    local c = type(m.st) == "table" and m.st.c or m.class
    if c then out[shortName(m.name)] = c end
  end
  for i = 1, min(4, tonumber((try(_G.GetNumSubgroupMembers))) or 0) do
    local u = "party" .. i
    local nm = try(_G.UnitName, u)
    if nm and not secret(nm) and not out[shortName(nm)] then
      local _, cf = try(_G.UnitClass, u)
      if cf and not secret(cf) then out[shortName(nm)] = cf end
    end
  end
  return out
end

local function verdicts()
  local A = mod("Abandon")
  local out = {}
  if not (A and type(A.evals) == "table") then return out end
  for _, e in ipairs(A.evals) do
    local id = type(e.q) == "table" and e.q.id
    if id then out[id] = e end
  end
  return out
end

local function rewardIcon(name)
  local API = T.API
  if not name then return ICON.reward end
  if not (API and API.ItemInfo) then return ICON.reward end
  local ok, _, _, _, _, _, _, _, _, _, icon = pcall(API.ItemInfo, name)
  return ok and icon or ICON.reward
end

-- objectif → { name, have, need, done, hint }
local function objRows(it)
  local Tr = mod("Tracker")
  local out = {}
  for _, o in ipairs(it.q.obj or {}) do
    if not secret(o.name) then
      local e = it.objs and it.objs[o.name]
      out[#out + 1] = { name = tostring(o.name or "?"), have = tonumber(o.have) or 0, need = max(1, tonumber(o.need) or 1),
        done = o.finished and true or false, hint = (e and Tr and Tr.ObjHint) and Tr.ObjHint(e) or nil, sec = e and e.sec }
    end
  end
  return out
end

-- Cartes de quête depuis le modèle de coût du Tracker (tri, temps, distance, groupe).
function M.Cards()
  local Tr = mod("Tracker")
  local items = Tr and Tr.items or {}
  local V = on("verdict") and verdicts() or {}
  local classes = on("mates") and classOf() or {}
  local cards = {}
  for _, it in ipairs(items) do
    local q = it.q
    if type(q) == "table" and q.id and not secret(q.title) then
      local c = { key = "q:" .. q.id, id = q.id, title = tostring(q.title or ("#" .. q.id)), level = q.level, color = q.color,
        zone = (q.zone and q.zone ~= "") and tostring(q.zone) or "Autres", complete = q.complete and true or false,
        rank = it.rank or #cards + 1, xp = q.xp, xpm = it.xpm, dist = it.dist, npc = it.npc, it = it, hard = q.hard,
        eta = q.complete and (it.travelSec or it.sec) or it.sec, objs = objRows(it), why = it.why or {} }
      if q.complete and c.zone == "Autres" and q.turnZone then c.zone = tostring(q.turnZone) end
      c.mates = {}
      if on("mates") then
        for _, g in ipairs(it.mates or {}) do
          c.mates[#c.mates + 1] = { name = g.name, have = g.have, need = g.need, done = g.done, noAddon = g.noAddon,
            class = classes[g.name] }
        end
      end
      local e = V[q.id]
      if e then
        c.verdict = { v = e.verdict, short = e.short, text = e.text, score = e.score, plain = e.plain }
        local rw = type(e.q.reward) == "table" and e.q.reward or nil
        if on("reward") and rw and ((rw.pct or 0) >= 1 or rw.item) then
          c.reward = { item = rw.item, pct = rw.pct and floor(rw.pct + 0.5) or nil, icon = rewardIcon(rw.item) }
        end
      end
      cards[#cards + 1] = c
    end
  end
  return cards
end

-- Filtre + recherche (pure) : ctx = { zone, pins } ; le filtre « faciles / groupe / zone » reprend Tracker.Pass.
function M.Match(c, filter, search, ctx)
  ctx = ctx or {}
  local Tr = mod("Tracker")
  filter = filter or "tous"
  if filter == "epingles" then
    if not (ctx.pins and ctx.pins[c.key]) then return false end
  elseif filter ~= "tous" then
    if Tr and Tr.Pass and c.it then
      if not Tr.Pass(c.it, filter, { zone = ctx.zone }) then return false end
    elseif filter == "pretes" and not c.complete then return false
    elseif filter == "zone" and c.zone ~= ctx.zone then return false end
  end
  local s = low(search):gsub("^%s+", ""):gsub("%s+$", "")
  if s ~= "" then
    local hay = { low(c.title), low(c.zone), low(c.npc) }
    for _, o in ipairs(c.objs or {}) do hay[#hay + 1] = low(o.name) end
    if not table.concat(hay, "\n"):find(s, 1, true) then return false end
  end
  return true
end

-- Ordre (pure) : épinglées d'abord, puis l'ordre choisi par glisser-déposer, puis le rang du modèle de coût.
function M.Order(list, char)
  char = char or {}
  local pins, order = char.pins or {}, char.order or {}
  local pos = {}
  for i, k in ipairs(order) do pos[k] = i end
  local n = #order
  local out = {}
  for i, c in ipairs(list) do out[i] = c end
  table.sort(out, function(a, b)
    local pa, pb = pins[a.key] and 0 or 1, pins[b.key] and 0 or 1
    if pa ~= pb then return pa < pb end
    local oa, ob = pos[a.key] or (n + (a.rank or 999)), pos[b.key] or (n + (b.rank or 999))
    if oa ~= ob then return oa < ob end
    return tostring(a.key) < tostring(b.key)
  end)
  return out
end

-- Déplace key avant (ou après) target dans la liste de clés affichées → nouvel ordre sauvé (pure).
function M.MoveKey(keys, key, target, after)
  local out = {}
  for _, k in ipairs(keys or {}) do if k ~= key then out[#out + 1] = k end end
  if not key then return out end
  local at
  for i, k in ipairs(out) do if k == target then at = after and i + 1 or i end end
  table.insert(out, at or (#out + 1), key)
  return out
end

-- Regroupe par zone (zone courante d'abord, puis zone de la meilleure carte) en gardant l'ordre.
function M.Zones(cards, zone)
  local byZone, list = {}, {}
  for i, c in ipairs(cards) do
    local z = byZone[c.zone]
    if not z then z = { zone = c.zone, cards = {}, first = i, ready = 0 } byZone[c.zone] = z list[#list + 1] = z end
    z.cards[#z.cards + 1] = c
    if c.complete then z.ready = z.ready + 1 end
  end
  table.sort(list, function(a, b)
    local ca, cb = a.zone == zone, b.zone == zone
    if ca ~= cb then return ca end
    return a.first < b.first
  end)
  return list
end

-- Courses : les conseils que leurs propriétaires affichent VRAIMENT (Inventaire, Chasseur, Guichets via T.Predict.list).
local ERRAND_ICON = { ammo = ICON.ammo, food = ICON.food, water = ICON.water, bags = ICON.bags, train = ICON.train,
                      repair = ICON.repair, hearth = ICON.hearth }
local ERRAND_ORDER = { repair = 1, ammo = 2, bags = 3, food = 4, water = 5, train = 6, hearth = 7 }
-- la course urgente dite simplement (verbe d'abord, sans %) ; le texte détaillé du propriétaire reste au survol
local ERRAND_TASK = { repair = "Va réparer ton équipement chez un forgeron", ammo = "Achète des flèches au marchand le plus proche",
  bags = "Vends ce qui encombre tes sacs au marchand", food = "Achète à manger au marchand le plus proche",
  water = "Achète à boire au marchand le plus proche", train = "Va voir ton maître de classe (nouveaux sorts)",
  hearth = "Pense à lier ta pierre de foyer à l'auberge" }
M.ERRAND_TASK = ERRAND_TASK
function M.Errands()
  local P = T.Predict
  local out = {}
  for _, f in ipairs(P and type(P.list) == "table" and P.list or {}) do
    local id = tostring(f.id or "")
    if ERRAND_ICON[id] and f.text and not secret(f.text) then
      out[#out + 1] = { key = "c:" .. id, id = id, text = tostring(f.text), prio = tonumber(f.prio) or 1, icon = ERRAND_ICON[id] }
    end
  end
  table.sort(out, function(a, b)
    if a.prio ~= b.prio then return a.prio > b.prio end
    return (ERRAND_ORDER[a.id] or 9) < (ERRAND_ORDER[b.id] or 9)
  end)
  return out
end

-- Étapes du plan TreeAI (Planner:GetActiveSteps via Tracker.PlanSteps).
function M.Steps(n)
  if not on("steps") then return nil end
  local Tr = mod("Tracker")
  if not (Tr and Tr.PlanSteps) then return nil end
  local steps, idx, total, title = try(Tr.PlanSteps, n or 5)
  if type(steps) ~= "table" then return nil end
  local out = { title = title, idx = idx, total = total }
  for i, s in ipairs(steps) do
    local x, y = tonumber(s.x), tonumber(s.y)
    if x and y and (x > 1 or y > 1) then x, y = x / 100, y / 100 end
    out[#out + 1] = { n = (idx or 1) + i - 1, label = Tr.StepText and Tr.StepText(s) or tostring(s.label or "?"),
      eta = tonumber(s.eta) and s.eta * 60 or nil, kind = s.k, icon = ICON[s.k or ""] or ICON.step, q = s.q,
      point = (s.map and x and y) and { mapID = s.map, x = x, y = y } or nil }
  end
  return out
end

-- Groupe : mini-tableau Missions (repli : quêtes communes de Squad).
function M.Board(n)
  if not on("group") then return nil end
  n = n or 4
  local Mi = mod("Missions")
  local out = {}
  if Mi and type(Mi.board) == "table" and #Mi.board > 0 then
    local E = Mi.Engine
    for _, e in ipairs(Mi.board) do
      if #out >= n then break end
      if e.multi or #(Mi.board) <= n then
        out[#out + 1] = { id = e.id, title = tostring(e.title or "?"), pct = tonumber(e.pct) or 0,
          holders = #(e.holders or {}), done = #(e.done or {}), missing = e.missing, mission = e,
          status = E and E.StatusLine and try(E.StatusLine, e) or nil }
      end
    end
    local nx = Mi.next
    out.next = type(nx) == "table" and nx.text or nil
    out.src = "missions"
  else
    local S = mod("Squad")
    if S and S.IsActive and try(S.IsActive, S) and S.Common then
      for _, e in ipairs(try(S.Common, S) or {}) do
        if #out >= n then break end
        local h, d = #(e.holders or {}), #(e.done or {})
        out[#out + 1] = { id = e.id, title = tostring(e.title or "?"), holders = h, done = d,
          pct = h > 0 and floor(d * 100 / h) or 0, missing = e.missing }
      end
      out.src = "squad"
    end
  end
  if #out == 0 and not out.next then return nil end
  return out
end

-- Session : niveau, XP, repos, XP/h MESURÉE et temps avant le niveau suivant (Stats, via Boost ou T.Predict).
function M.Session()
  local lv = try(_G.UnitLevel, "player") or 1
  local xp, xpMax = tonumber((try(_G.UnitXP, "player"))) or 0, tonumber((try(_G.UnitXPMax, "player"))) or 0
  local s = { level = lv, xp = xp, xpMax = xpMax, pct = xpMax > 0 and xp / xpMax or 0,
              rested = tonumber((try(_G.GetXPExhaustion))) or 0 }
  local B = mod("Boost")
  local st = B and B.XPStats and try(B.XPStats, B)
  if type(st) == "table" then s.xph, s.eta = tonumber(st.perHour), tonumber(st.eta) end
  if not s.xph then
    local P = T.Predict
    local r = P and P.Rates and try(P.Rates) or {}
    s.xph = tonumber(r.xpPerHour)
    if s.xph and s.xph > 0 and xpMax > 0 then s.eta = (xpMax - xp) / s.xph * 3600 end
  end
  return s
end

function M.SessionText(s)
  s = s or M.Session()
  local t = ("Niv %d → %d : %d %%"):format(s.level, s.level + 1, floor(s.pct * 100))
  if s.rested and s.rested > 0 and s.xpMax > 0 then t = t .. " (bonus de repos)" end
  if s.eta and s.eta > 0 then
    local Pl = T.Plain
    t = t .. " · niveau " .. (s.level + 1) .. " dans " .. ((Pl and Pl.Minutes(s.eta)) or ("≈ " .. Time(s.eta)))
  end
  return t
end

-- Pourquoi ? (UNE phrase simple, agent SIMPLE ; les calculs restent ceux du Tracker)
local function openMates(c)
  local open = 0
  for _, g in ipairs(c.mates or {}) do if not g.done then open = open + 1 end end
  return open
end
local function plainInfo(c)
  local Tr = mod("Tracker")
  if Tr and Tr.PlainInfo and c.it then
    local ok, info = pcall(Tr.PlainInfo, c.it)
    if ok and info then return info end
  end
  return { complete = c.complete, verdict = c.verdict and c.verdict.v, reason = c.verdict and c.verdict.plain,
           hard = c.hard, sec = c.eta, mates = openMates(c) }
end
M.PlainInfo = plainInfo
local function whyText(c, ctx)
  local Pl = T.Plain
  local info = plainInfo(c)
  if Pl then return Pl.Why(info, ctx) end
  return c.complete and "Prête à rendre." or "La meilleure quête pour toi."
end
M.WhyText = whyText
local function tagText(c)
  local Pl = T.Plain
  return Pl and Pl.TagText(plainInfo(c)) or (c.complete and "|cff33ff33Prête à rendre|r" or "")
end
M.TagText = tagText

local function distText(d)
  local C = T.Compass
  if not d then return nil end
  if C and C.Geo and C.Geo.Dist then return C.Geo.Dist(d, C.db and C.db.units) end
  return floor(d + 0.5) .. " m"
end
M.DistText = distText

-- première chose concrète à faire dans une carte (« Va tuer 6 Loups au nord (≈ 3 min) »)
local function cardTask(c)
  local Tr = mod("Tracker")
  if Tr and Tr.TaskText and c.it then
    local ok, t = pcall(Tr.TaskText, c.it)
    if ok and t then return t end
  end
  local Pl = T.Plain
  if c.complete then return "Rends « " .. cut(c.title, 34) .. " »" .. (c.npc and (" à " .. cut(c.npc, 22)) or "") end
  for _, o in ipairs(c.objs or {}) do
    if not o.done then
      return (Pl and Pl.ObjAction(o)) or (("%s %d/%d"):format(cut(o.name, 28), o.have, o.need))
    end
  end
  return "Fais « " .. cut(c.title, 40) .. " »"
end
M.CardTask = cardTask

-- La meilleure action : courses urgentes > carte épinglée > étape du plan > meilleure carte.
function M.NowAction(model)
  local sug = T._suggestions
  for _, e in ipairs(model.errands or {}) do
    -- (REVIEW) la réparation appartient à Inventory (« Répare au prochain marchand ») : pas de 2e ligne
    local owned = e.id == "repair" and type(sug) == "table" and sug["Inventory:inv_repair"] ~= nil
    if e.prio >= 3 and not model.hiddenSet[e.key] and not owned then
      return { kind = "errand", key = e.key, title = ERRAND_TASK[e.id] or e.text, why = "À régler avant de continuer : " .. e.text,
        icon = e.icon, prio = 3 }
    end
  end
  local first = model.cards[1]
  local pinned = first and model.pins[first.key] and first or nil
  local steps = model.steps
  if not pinned and steps and steps[1] then
    local s = steps[1]
    local Pl = T.Plain
    local t = s.eta and Pl and Pl.Minutes(s.eta)
    return { kind = "step", title = tostring(s.label) .. (t and not tostring(s.label):find("≈", 1, true) and (" (" .. t .. ")") or ""),
      why = "C'est la prochaine étape de ton trajet.", eta = s.eta, icon = s.icon, point = s.point, questID = s.q, prio = 2 }
  end
  local c = pinned or first
  if c then
    return { kind = "quest", key = c.key, title = cardTask(c), why = whyText(c, { pinned = pinned and true or nil }), eta = c.eta,
      icon = c.complete and ICON.ready or ICON.quest, questID = c.id, card = c, prio = 2 }
  end
  return nil
end

-- Modèle complet de la page.
function M.Collect()
  local ch = M.char or {}
  ch.pins, ch.hidden, ch.order, ch.folded, ch.sections = ch.pins or {}, ch.hidden or {}, ch.order or {}, ch.folded or {}, ch.sections or {}
  local Tr = mod("Tracker")
  local zone = (Tr and Tr.ctx and Tr.ctx.zone) or try(_G.GetRealZoneText) or ""
  local all = M.Order(M.Cards(), ch)
  local filter, search = ch.filter or "tous", ch.search or ""
  local cards, hidden = {}, {}
  local mctx = { zone = zone, pins = ch.pins }
  for _, c in ipairs(all) do
    if ch.hidden[c.key] then hidden[#hidden + 1] = c
    elseif M.Match(c, filter, search, mctx) then cards[#cards + 1] = c end
  end
  local errands, hiddenErr = {}, {}
  if on("errands") then
    for _, e in ipairs(M.Errands()) do
      if ch.hidden[e.key] then hiddenErr[#hiddenErr + 1] = e else errands[#errands + 1] = e end
    end
  end
  local model = { cards = cards, all = all, zones = M.Zones(cards, zone), zone = zone, errands = errands,
    hidden = hidden, hiddenErrands = hiddenErr, hiddenSet = ch.hidden, pins = ch.pins, filter = filter, search = search,
    steps = M.Steps(5), board = M.Board(4), session = on("session") and M.Session() or nil, total = #all }
  model.now = M.NowAction(model)
  local keys = {}
  for _, c in ipairs(all) do keys[#keys + 1] = c.key end
  M.allKeys = keys
  M.model = model
  return model
end

-- ------------------------------------------------------------------ actions (clic du joueur seulement)
function M.Go(a, route)
  local C = T.Compass
  if not (C and a) then return nil end
  local t
  if a.point and C.SetWaypoint then
    C.SetWaypoint(a.point.x, a.point.y, cut(a.title or a.label or "Étape", 40), a.point.mapID)
    t = { mapID = a.point.mapID, x = a.point.x, y = a.point.y, label = a.title or a.label }
  elseif a.questID and C.SetQuestTarget then
    t = C.SetQuestTarget(a.questID)
  end
  if route and C.GoTo then C.GoTo(t) return "goto" end
  return t and "compass" or nil
end

local function save() M.RequestRender(true) local Tr = mod("Tracker") if Tr and Tr.PushSoon then Tr.PushSoon() end end
function M.TogglePin(key)
  M.char.pins[key] = (not M.char.pins[key]) or nil
  save()
  return M.char.pins[key] or false
end
function M.ToggleHidden(key)
  M.char.hidden[key] = (not M.char.hidden[key]) or nil
  if M.char.hidden[key] then M.char.pins[key] = nil end
  save()
  return M.char.hidden[key] or false
end
function M.ShowAll() M.char.hidden = {} save() end
-- tri du modèle de coût (Tracker) : bouton de l'en-tête
function M.CycleSort()
  local Tr = mod("Tracker")
  if not (Tr and Tr.CycleSort) then return nil end
  local r = try(Tr.CycleSort, 1)
  M.RequestRender(true)
  return r
end
function M.SortLabel()
  local Tr = mod("Tracker")
  local k = Tr and Tr.db and Tr.db.sort or "cout"
  return "Tri : " .. ((Tr and Tr.SORT_TXT and Tr.SORT_TXT[k]) or k)
end
-- itinéraire complet (Tracker.SendRoute : Shortest Path Forever, sinon boussole) — sur clic
function M.Route()
  local Tr = mod("Tracker")
  return Tr and Tr.SendRoute and try(Tr.SendRoute) or nil
end
function M.ResetOrder() M.char.order = {} save() end
function M.SetFilter(f) M.char.filter = f save() end
function M.SetSearch(s) M.char.search = tostring(s or "") M.RequestRender(true) end
function M.ToggleFold(key, default)
  local cur = M.char.folded[key]
  if cur == nil then cur = default and true or false end
  M.char.folded[key] = not cur
  M.RequestRender(true)
  return M.char.folded[key]
end
function M.ToggleSection(id) M.char.sections[id] = not M.char.sections[id] or nil M.RequestRender(true) end
function M.DropOn(key, target, after)
  if not key or key == target then return M.char.order end
  M.char.order = M.MoveKey(M.allKeys or {}, key, target, after)
  save()
  return M.char.order
end
-- ▲▼ : échange avec la carte voisine de la même zone
function M.Nudge(key, dir)
  local list = (M.model and M.model.all) or {}
  local idx, zone
  for i, c in ipairs(list) do if c.key == key then idx, zone = i, c.zone end end
  if not idx then return nil end
  local j = idx + dir
  while list[j] and list[j].zone ~= zone do j = j + dir end
  if not list[j] then return nil end
  return M.DropOn(key, list[j].key, dir > 0)
end

-- ------------------------------------------------------------------ lignes de l'onglet « Quêtes » (compact)
function M.CardLine(c)
  local col = c.complete and "|cff33ff33" or hex(LEVEL_RGB[c.color] or PRIO_RGB[1])
  return ("%s%s|r · %s"):format(col, cut(c.title, 32), tagText(c))
end

function M.CardTooltip(c)
  local Pl = T.Plain
  local zn = (type(c.zone) == "string" and c.zone ~= "" and c.zone ~= "?") and (" — " .. c.zone) or ""   -- jamais « — ? »
  local tip = { hex(LEVEL_RGB[c.color] or PRIO_RGB[1]) .. c.title .. "|r" .. zn }
  if Pl then
    local word, why = Pl.Tag(plainInfo(c))
    tip[#tip + 1] = "|cffffd100" .. word .. "|r : " .. why
  end
  tip[#tip + 1] = "|cff99ccffÀ faire|r : " .. cardTask(c)
  for _, o in ipairs(c.objs) do
    tip[#tip + 1] = ("%s%s %d/%d|r"):format(o.done and "|cff55ff55" or "|cffdddddd", cut(o.name, 34), o.have, o.need)
  end
  if #(c.mates or {}) > 0 then
    local m = {}
    for _, g in ipairs(c.mates) do m[#m + 1] = g.name .. (g.done and " (fini)" or "") end
    tip[#tip + 1] = "|cff88ccffAvec|r " .. table.concat(m, ", ")
  end
  if c.reward and c.reward.item then
    tip[#tip + 1] = "Récompense : " .. tostring(c.reward.item) .. ((c.reward.pct or 0) >= 1 and " (mieux que ton équipement)" or "")
  end
  local nums = {}
  if Pl and Pl.Minutes(c.eta) then nums[#nums + 1] = Pl.Minutes(c.eta) end
  if tonumber(c.xp) then nums[#nums + 1] = fmtNum(c.xp) .. " XP" end
  if #nums > 0 then tip[#tip + 1] = "|cffaaaaaa" .. table.concat(nums, " · ") .. "|r" end
  tip[#tip + 1] = "|cff888888Clic : boussole · Maj-clic : Y aller|r"
  if T.AutoRunHint then tip[#tip + 1] = "|cff888888" .. T.AutoRunHint() .. "|r" end
  return tip
end

-- (QUESTQA) le trajet (Planner + Itinéraire) a-t-il des arrêts affichés dans ce même onglet ?
local function planInTab()
  local P = T.Planner
  if not (P and P.IsEnabled and P:IsEnabled() and P.GetActiveSteps) then return false end
  local ok, st = pcall(P.GetActiveSteps, P, 1)
  return ok and st ~= nil
end
M.PlanInTab = planInTab
function M.TabLines(model)
  model = model or M.Collect()
  local lines = {}
  local a = model.now
  local planTab = planInTab()
  -- une seule liste d'étapes dans l'onglet : celle du trajet (« Arrêt 1/8 : … ») ; « À faire maintenant » ne la
  -- répète pas (étape du plan) et les lignes « Ensuite : … » disparaissent quand le trajet est affiché
  if a and not (planTab and a.kind == "step") then
    lines[#lines + 1] = { text = "|cffffd100À faire maintenant|r : " .. a.title,
      -- the Planner already pins its step in « Maintenant »: don't pin the same thing twice (QA sim)
      prio = a.prio or 2, icon = a.icon, pin = not planTab, now = true,
      tooltip = { a.title, "|cff99ccffPourquoi ?|r " .. (a.why or ""), "|cff888888Clic : boussole · Maj-clic : Y aller|r", (T.AutoRunHint and ("|cff888888" .. T.AutoRunHint() .. "|r") or nil) },
      onClick = function() M.Go(a, shift()) end }
  end
  local st = not planTab and model.steps or nil
  if st then
    for i = (a and a.kind == "step") and 2 or 1, min(#st, 3) do
      local s = st[i]
      local Pl = T.Plain
      local t = s.eta and Pl and Pl.Minutes(s.eta)
      lines[#lines + 1] = { text = ("Ensuite : %s%s"):format(s.label, t and (" (" .. t .. ")") or ""),
        prio = 1, icon = s.icon, plan = true, onClick = function() M.Go(s, shift()) end }
    end
  end
  local maxQ = tonumber(opt("maxTab", 8)) or 8
  local n = 0
  local multiZone = #model.zones > 1
  for _, z in ipairs(model.zones) do
    if n >= maxQ then break end
    if multiZone then lines[#lines + 1] = { text = ("|cff99ccff%s|r (%d)"):format(cut(z.zone, 30), #z.cards), prio = 1, header = true } end
    for _, c in ipairs(z.cards) do
      if n >= maxQ then break end
      n = n + 1
      local cid = c.id
      lines[#lines + 1] = { text = (model.pins[c.key] and tex(ICON.check, 12) or "") .. M.CardLine(c), prio = c.complete and 2 or 1,
        icon = c.complete and ICON.ready or ICON.quest, questID = cid, tooltip = M.CardTooltip(c),
        onClick = function() M.Go({ questID = cid }, shift()) end }
    end
  end
  if n == 0 then
    lines[#lines + 1] = { text = model.total == 0 and "Aucune quête dans le journal." or
      ("Aucune quête pour « " .. (M.FILTER_TXT[model.filter] or "?") .. " »" .. (model.search ~= "" and (" / « " .. model.search .. " »") or "") .. "."), prio = 1 }
  elseif model.total > n then
    lines[#lines + 1] = { text = ("|cff888888+ %d autre%s dans la fenêtre « À faire »|r"):format(model.total - n, model.total - n > 1 and "s" or ""), prio = 1,
      onClick = function() M.Show(true) end }
  end
  if #model.errands > 0 then
    local t, pr = {}, 1
    for i, e in ipairs(model.errands) do if i <= 4 then t[#t + 1] = tex(e.icon, 12) .. " " .. cut(e.text:gsub(" :.*$", ""), 18) end pr = max(pr, e.prio) end
    local tip = {}
    for _, e in ipairs(model.errands) do tip[#tip + 1] = e.text end
    lines[#lines + 1] = { text = "|cffffd100Courses|r : " .. table.concat(t, " · "), prio = pr, tooltip = tip }
  end
  if model.session and (model.session.eta or 0) > 0 then
    lines[#lines + 1] = { text = "|cff888888" .. M.SessionText(model.session) .. "|r", prio = 1, icon = ICON.session }
  end
  -- (REVIEW) une action sans contexte = un bouton d'onglet, pas une ligne
  lines[#lines + 1] = { text = (M.frame and M.frame:IsShown() and "Fermer" or "Ouvrir") .. " la fenêtre « À faire » (épingler, trier, filtrer)",
    label = "À faire", control = true, prio = 1, icon = ICON.quests, onClick = function() M.Toggle() end }
  M.tabLines = lines
  return lines
end

-- ------------------------------------------------------------------ mise en page (pure : lignes + hauteurs)
local function folded(key, default)
  local v = M.char and M.char.folded and M.char.folded[key]
  if v == nil then return default and true or false end
  return v
end
local function sectionOpen(id) return not (M.char and M.char.sections and M.char.sections[id]) end

function M.Rows(model, fs)
  fs = fs or tonumber(opt("fontSize", 12)) or 12
  local rows = {}
  local function add(r) rows[#rows + 1] = r return r end
  local H = { section = fs + 12, now = fs * 3 + 30, step = fs + 8, zone = fs + 8, card = fs + 10, obj = fs + 6,
              mates = 18, errand = fs + 8, board = fs + 10, foot = fs + 8, empty = fs + 10, gap = 6 }
  M.H = H
  -- Maintenant
  add({ kind = "section", id = "now", title = "Maintenant", icon = ICON.now, h = H.section })
  if sectionOpen("now") then
    if model.now then add({ kind = "now", a = model.now, h = H.now })
    else add({ kind = "empty", text = "Rien d'urgent : choisis une quête ci-dessous.", h = H.empty }) end
  end
  -- Étapes du trajet
  if model.steps and #model.steps > 0 then
    add({ kind = "section", id = "steps", title = ("Étapes du trajet%s"):format(model.steps.title and (" — " .. cut(model.steps.title, 22)) or ""),
      count = ("%d/%d"):format(model.steps.idx or 1, model.steps.total or #model.steps), icon = ICON.step, h = H.section })
    if sectionOpen("steps") then
      for i, s in ipairs(model.steps) do add({ kind = "step", s = s, first = i == 1, h = H.step }) end
    end
  end
  -- Quêtes
  local ready = 0
  for _, c in ipairs(model.cards) do if c.complete then ready = ready + 1 end end
  add({ kind = "section", id = "quests", title = "Quêtes", icon = ICON.quests, h = H.section,
    count = ("%d%s"):format(#model.cards, ready > 0 and (" · %d à rendre"):format(ready) or "") })
  if sectionOpen("quests") then
    local multiZone = #model.zones > 1
    for _, z in ipairs(model.zones) do
      local zk = "z:" .. z.zone
      local zfold = multiZone and folded(zk, false)
      if multiZone then add({ kind = "zone", key = zk, zone = z, folded = zfold, current = z.zone == model.zone, h = H.zone }) end
      if not zfold then
        for _, c in ipairs(z.cards) do
          local cf = folded(c.key, not (c.rank <= 3 or model.pins[c.key]))
          add({ kind = "card", c = c, key = c.key, folded = cf, pinned = model.pins[c.key] and true or false, h = H.card })
          if not cf then
            if c.complete then
              add({ kind = "obj", c = c, done = true, text = "Rendre" .. (c.npc and (" à " .. c.npc) or "") ..
                (c.dist and (" · " .. distText(c.dist)) or ""), have = 1, need = 1, h = H.obj })
            else
              for _, o in ipairs(c.objs) do
                add({ kind = "obj", c = c, o = o, have = o.have, need = o.need, done = o.done, h = H.obj,
                  text = ("%s  %d/%d%s"):format(cut(o.name, 30), o.have, o.need, (not o.done and o.hint and o.hint ~= "") and ("  · " .. o.hint) or "") })
              end
            end
            if #c.mates > 0 then add({ kind = "mates", c = c, h = H.mates }) end
          end
        end
      end
    end
    if #model.cards == 0 then
      add({ kind = "empty", h = H.empty, text = model.total == 0 and "Aucune quête dans le journal." or
        ("Rien pour « %s »%s."):format(M.FILTER_TXT[model.filter] or "?", model.search ~= "" and (" + « " .. model.search .. " »") or "") })
    end
    local nh = #model.hidden + #model.hiddenErrands
    if nh > 0 then add({ kind = "foot", text = ("%d masqué%s — clic pour tout réafficher"):format(nh, nh > 1 and "s" or ""), h = H.foot }) end
  end
  -- Courses
  if #model.errands > 0 then
    local pr = 1
    for _, e in ipairs(model.errands) do pr = max(pr, e.prio) end
    add({ kind = "section", id = "errands", title = "Courses", icon = ICON.errand, count = tostring(#model.errands), prio = pr, h = H.section })
    if sectionOpen("errands") then
      for _, e in ipairs(model.errands) do add({ kind = "errand", e = e, key = e.key, h = H.errand }) end
    end
  end
  -- Groupe
  local b = model.board
  if b then
    add({ kind = "section", id = "group", title = "Groupe", icon = ICON.group, count = tostring(#b), h = H.section })
    if sectionOpen("group") then
      if b.next then add({ kind = "step", s = { label = b.next, icon = ICON.group }, group = true, h = H.step }) end
      for _, e in ipairs(b) do add({ kind = "board", e = e, h = H.board }) end
    end
  end
  -- positions
  local y = 0
  for _, r in ipairs(rows) do
    if r.kind == "section" and y > 0 then y = y + H.gap end
    r.y = y
    y = y + r.h
  end
  return rows, y
end

-- Cible du glisser-déposer : carte sous la position y (depuis le haut du contenu) → clé, après ?
function M.TargetAt(rows, y)
  local best
  for _, r in ipairs(rows or {}) do
    if r.kind == "card" then
      if y >= r.y and y < r.y + r.h then return r.key, y > r.y + r.h / 2 end
      if r.y <= y then best = r end
    end
  end
  if best then return best.key, true end
  for _, r in ipairs(rows or {}) do if r.kind == "card" then return r.key, false end end
  return nil
end

-- ------------------------------------------------------------------ widgets
local function fontPath()
  local f = _G.GameFontHighlight
  local p = f and f.GetFont and try(f.GetFont, f)
  return p or "Fonts\\FRIZQT__.TTF"
end
local function setFont(fs, size, flags) if fs then pcall(fs.SetFont, fs, fontPath(), size, flags or "") end end
local function color(t, rgb, a) if t and t.SetColorTexture then t:SetColorTexture(rgb[1], rgb[2], rgb[3], a or 1) end end
local function showTip(owner, tip)
  local tt = _G.GameTooltip
  if not (tt and tip) then return end
  pcall(tt.SetOwner, tt, owner, "ANCHOR_LEFT")
  if type(tip) == "table" then
    for i, l in ipairs(tip) do pcall(tt.AddLine, tt, tostring(l), i == 1 and 1 or 0.9, i == 1 and 0.82 or 0.9, i == 1 and 0 or 0.9, true) end
  else
    for l in tostring(tip):gmatch("[^\n]+") do pcall(tt.AddLine, tt, l, 1, 1, 1, true) end
  end
  pcall(tt.Show, tt)
end
local function hideTip() if _G.GameTooltip then pcall(_G.GameTooltip.Hide, _G.GameTooltip) end end

-- petit bouton icône (16 px) avec infobulle
local function iconButton(parent, path, size, tip, onClick)
  local b = _G.CreateFrame("Button", nil, parent)
  b:SetSize(size, size)
  b.t = b:CreateTexture(nil, "ARTWORK")
  b.t:SetAllPoints(b)
  b.t:SetTexture(path)
  local hl = b:CreateTexture(nil, "HIGHLIGHT")
  hl:SetAllPoints(b)
  if hl.SetColorTexture then hl:SetColorTexture(1, 1, 1, 0.18) end
  b:RegisterForClicks("LeftButtonUp")
  b:SetScript("OnClick", function(self, btn) if self.onClick then pcall(self.onClick, self, btn) end end)
  b:SetScript("OnEnter", function(self) if self.tip then showTip(self, self.tip) end end)
  b:SetScript("OnLeave", hideTip)
  b.tip, b.onClick = tip, onClick
  return b
end
-- bouton texte (puce de filtre, « Boussole », « Y aller »)
local function textButton(parent, label, w, h, onClick)
  local b = _G.CreateFrame("Button", nil, parent)
  b:SetSize(w, h)
  b.bg = b:CreateTexture(nil, "BACKGROUND")
  b.bg:SetAllPoints(b)
  color(b.bg, { 1, 1, 1 }, 0.08)
  local hl = b:CreateTexture(nil, "HIGHLIGHT")
  hl:SetAllPoints(b)
  if hl.SetColorTexture then hl:SetColorTexture(1, 0.82, 0, 0.2) end
  b.fs = b:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
  b.fs:SetPoint("CENTER", b, "CENTER", 0, 0)
  b.fs:SetText(label)
  b:RegisterForClicks("LeftButtonUp")
  b:SetScript("OnClick", function(self, btn) if self.onClick then pcall(self.onClick, self, btn) end end)
  b:SetScript("OnEnter", function(self) if self.tip then showTip(self, self.tip) end end)
  b:SetScript("OnLeave", hideTip)
  b.onClick = onClick
  return b
end
M.TextButton = textButton

local frame
local pool = {}

local function rowWidget(i)
  if pool[i] then return pool[i] end
  local content = frame.content
  local b = _G.CreateFrame("Button", nil, content)
  b:RegisterForClicks("LeftButtonUp", "RightButtonUp")
  b:RegisterForDrag("LeftButton")
  b.bg = b:CreateTexture(nil, "BACKGROUND")
  b.bg:SetAllPoints(b)
  b.accent = b:CreateTexture(nil, "BORDER")
  b.accent:SetWidth(3)
  b.accent:SetPoint("TOPLEFT", b, "TOPLEFT", 0, 0)
  b.accent:SetPoint("BOTTOMLEFT", b, "BOTTOMLEFT", 0, 0)
  local hl = b:CreateTexture(nil, "HIGHLIGHT")
  hl:SetAllPoints(b)
  if hl.SetColorTexture then hl:SetColorTexture(1, 1, 1, 0.06) end
  b.icon = b:CreateTexture(nil, "ARTWORK")
  b.text = b:CreateFontString(nil, "OVERLAY", "GameFontHighlight")
  b.sub = b:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
  b.meta = b:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
  b.right = b:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
  for _, f in ipairs({ b.text, b.sub, b.meta, b.right }) do
    if f.SetJustifyH then f:SetJustifyH("LEFT") end
    if f.SetWordWrap then f:SetWordWrap(false) end
  end
  if b.right.SetJustifyH then b.right:SetJustifyH("RIGHT") end
  b.bar = _G.CreateFrame("StatusBar", nil, b)
  if b.bar.SetStatusBarTexture then b.bar:SetStatusBarTexture(ICON.bar) end
  b.bar.bg = b.bar:CreateTexture(nil, "BACKGROUND")
  b.bar.bg:SetAllPoints(b.bar)
  color(b.bar.bg, { 0, 0, 0 }, 0.45)
  b.bar.fs = b.bar:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
  b.bar.fs:SetPoint("LEFT", b.bar, "LEFT", 4, 0)
  if b.bar.fs.SetJustifyH then b.bar.fs:SetJustifyH("LEFT") end
  if b.bar.fs.SetWordWrap then b.bar.fs:SetWordWrap(false) end
  b.bar:Hide()
  b.fold = iconButton(b, ICON.minus, 12, nil, nil)
  b.pin = iconButton(b, ICON.box, 16, "Épingler (en tête, et « Maintenant »)", nil)
  b.pin.mark = b.pin:CreateTexture(nil, "OVERLAY")
  b.pin.mark:SetAllPoints(b.pin)
  b.pin.mark:SetTexture(ICON.check)
  b.hide = iconButton(b, ICON.hide, 14, "Masquer (réafficher : bas de la liste)", nil)
  b.up = iconButton(b, ICON.up, 14, "Monter", nil)
  b.down = iconButton(b, ICON.down, 14, "Descendre", nil)
  b.verdict = iconButton(b, ICON.keep, 14, nil, nil)
  b.reward = iconButton(b, ICON.reward, 14, nil, nil)
  b.go = iconButton(b, ICON.compass, 16, "Boussole vers cette quête (Maj : Y aller)", nil)
  b.btn1 = textButton(b, "Boussole", 70, 18, nil)
  b.btn2 = textButton(b, "Y aller", 60, 18, nil)
  b.avatars = {}
  for k = 1, 4 do
    local a = b:CreateTexture(nil, "ARTWORK")
    a:SetSize(14, 14)
    b.avatars[k] = a
  end
  b:SetScript("OnClick", function(self, btn)
    local r = self.row
    if r and r.onClick then pcall(r.onClick, r, btn) end
  end)
  b:SetScript("OnEnter", function(self) local r = self.row if r and r.tip then showTip(self, r.tip) end end)
  b:SetScript("OnLeave", hideTip)
  b:SetScript("OnDragStart", function(self)
    local r = self.row
    if r and r.kind == "card" and not opt("lock", false) then M.BeginDrag(r.key) end
  end)
  b:SetScript("OnDragStop", function() M.EndDrag() end)
  pool[i] = b
  return b
end

local function hideExtras(b)
  for _, w in ipairs({ b.fold, b.pin, b.hide, b.up, b.down, b.verdict, b.reward, b.go, b.btn1, b.btn2, b.bar }) do w:Hide() end
  for _, a in ipairs(b.avatars) do a:Hide() end
  b.sub:SetText("") b.meta:SetText("") b.right:SetText("")
  b.icon:Hide()
  b.accent:Hide()
end

local function classIcon(t, class)
  local co = rawget(_G, "CLASS_ICON_TCOORDS")
  local c = class and type(co) == "table" and co[class]
  if c then t:SetTexture(ICON.classes) if t.SetTexCoord then t:SetTexCoord(c[1], c[2], c[3], c[4]) end
  else t:SetTexture(ICON.mate) if t.SetTexCoord then t:SetTexCoord(0, 1, 0, 1) end end
end
local function classColor(class)
  local cc = rawget(_G, "RAID_CLASS_COLORS")
  local c = class and type(cc) == "table" and cc[class]
  return c and hex({ c.r or 1, c.g or 1, c.b or 1 }) or "|cffdddddd"
end

-- Peint une ligne selon son type. w = largeur utile.
function M.Paint(b, r, w, fs)
  hideExtras(b)
  b.row = r
  r.onClick, r.tip = nil, nil
  local x = 6
  b.text:ClearAllPoints()
  b.right:ClearAllPoints()
  setFont(b.text, fs)
  setFont(b.right, fs - 1)
  color(b.bg, { 0, 0, 0 }, 0)
  if r.kind == "section" then
    color(b.bg, { 1, 1, 1 }, 0.07)
    b.accent:Show()
    color(b.accent, PRIO_RGB[r.prio or 2] or PRIO_RGB[2], 0.9)
    b.icon:SetSize(fs + 4, fs + 4) b.icon:SetPoint("LEFT", b, "LEFT", 8, 0) b.icon:SetTexture(r.icon) b.icon:Show()
    setFont(b.text, fs + 1, "OUTLINE")
    b.text:SetPoint("LEFT", b.icon, "RIGHT", 6, 0)
    b.text:SetText("|cffffd100" .. r.title .. "|r")
    b.right:SetPoint("RIGHT", b, "RIGHT", -24, 0)
    b.right:SetText(r.count and ("|cffaaaaaa" .. r.count .. "|r") or "")
    b.fold:SetPoint("RIGHT", b, "RIGHT", -6, 0)
    b.fold.t:SetTexture(sectionOpen(r.id) and ICON.minus or ICON.plus)
    b.fold.onClick = function() M.ToggleSection(r.id) end
    b.fold:Show()
    r.onClick = function() M.ToggleSection(r.id) end
  elseif r.kind == "now" then
    local a = r.a
    color(b.bg, PRIO_RGB[a.prio or 2], 0.10)
    b.accent:Show() color(b.accent, PRIO_RGB[a.prio or 2], 1)
    local isz = fs * 2 + 14
    b.icon:SetSize(isz, isz) b.icon:SetPoint("TOPLEFT", b, "TOPLEFT", 10, -6) b.icon:SetTexture(a.icon or ICON.now) b.icon:Show()
    setFont(b.text, fs + 3, "OUTLINE")
    b.text:SetPoint("TOPLEFT", b, "TOPLEFT", 16 + isz, -6)
    b.text:SetWidth(w - isz - 24)
    b.text:SetText(hex(PRIO_RGB[a.prio or 2]) .. a.title .. "|r")
    b.sub:ClearAllPoints() setFont(b.sub, fs - 1)
    b.sub:SetPoint("TOPLEFT", b.text, "BOTTOMLEFT", 0, -3)
    b.sub:SetWidth(w - isz - 24)
    b.sub:SetText("|cffbbbbbb" .. (a.why or "") .. "|r")
    b.meta:ClearAllPoints() setFont(b.meta, fs - 1)
    b.meta:SetPoint("BOTTOMLEFT", b, "BOTTOMLEFT", 16 + isz, 8)
    b.meta:SetText("")
    if a.kind ~= "errand" and (a.questID or a.point) then
      b.btn2:SetPoint("BOTTOMRIGHT", b, "BOTTOMRIGHT", -8, 5)
      b.btn1:SetPoint("RIGHT", b.btn2, "LEFT", -4, 0)
      b.btn1.fs:SetText(tex(ICON.compass, 12) .. " Boussole") b.btn2.fs:SetText(tex(ICON.go, 12) .. " Y aller")
      b.btn1.onClick = function() M.Go(a, false) end
      b.btn2.onClick = function() M.Go(a, true) end
      b.btn1.tip = "Flèche de la boussole TreeAI vers l'objectif"
      b.btn2.tip = "Itinéraire : Shortest Path → TomTom → repère de carte (sur ton clic)"
      b.btn1:Show() b.btn2:Show()
    end
    r.tip = { a.title, "|cff99ccffPourquoi ?|r " .. (a.why or "") }
    r.onClick = function() M.Go(a, shift()) end
  elseif r.kind == "step" then
    local s = r.s
    b.icon:SetSize(fs + 2, fs + 2) b.icon:SetPoint("LEFT", b, "LEFT", 12, 0) b.icon:SetTexture(s.icon or ICON.step) b.icon:Show()
    b.text:SetPoint("LEFT", b.icon, "RIGHT", 6, 0)
    b.text:SetWidth(w - fs - 80)
    local head = r.group and "" or (s.n and ((r.first and "|cffffd100" or "|cffaaaaaa") .. s.n .. ".|r ") or "")
    b.text:SetText(head .. (r.first and "|cffffffff" or "|cffcccccc") .. tostring(s.label) .. "|r")
    b.right:SetPoint("RIGHT", b, "RIGHT", -8, 0)
    b.right:SetText(s.eta and ("|cffaaaaaa" .. ((T.Plain and T.Plain.Minutes(s.eta)) or ("~" .. Time(s.eta))) .. "|r") or "")
    if s.point or s.q then r.onClick = function() M.Go({ point = s.point, questID = s.q, title = s.label }, shift()) end end
    r.tip = { tostring(s.label), r.group and "Prochaine mission du groupe (Missions)" or "Clic : boussole · Maj-clic : Y aller", (T.AutoRunHint and ("|cff888888" .. T.AutoRunHint() .. "|r") or nil) }
    if r.group then local Mi = mod("Missions") r.onClick = function() if Mi and Mi.next and Mi.SetGroupTarget then pcall(Mi.SetGroupTarget, Mi, Mi.next.mission) end end end
  elseif r.kind == "zone" then
    local z = r.zone
    b.fold:SetPoint("LEFT", b, "LEFT", 8, 0)
    b.fold.t:SetTexture(r.folded and ICON.plus or ICON.minus)
    b.fold.onClick = function() M.ToggleFold(r.key, false) end
    b.fold:Show()
    setFont(b.text, fs, "")
    b.text:SetPoint("LEFT", b, "LEFT", 26, 0)
    b.text:SetText((r.current and "|cff66ccff" or "|cff99aabb") .. z.zone .. "|r")
    b.right:SetPoint("RIGHT", b, "RIGHT", -8, 0)
    b.right:SetText(("|cffaaaaaa%d%s|r"):format(#z.cards, z.ready > 0 and (" · |cff33ff33%d à rendre|r"):format(z.ready) or ""))
    r.onClick = function() M.ToggleFold(r.key, false) end
  elseif r.kind == "card" then
    local c = r.c
    color(b.bg, { 1, 1, 1 }, r.pinned and 0.08 or 0.035)
    b.accent:Show() color(b.accent, c.complete and { 0.2, 1, 0.2 } or (LEVEL_RGB[c.color] or PRIO_RGB[1]), 1)
    b.fold:SetPoint("LEFT", b, "LEFT", 8, 0)
    b.fold.t:SetTexture(r.folded and ICON.plus or ICON.minus)
    b.fold.onClick = function() M.ToggleFold(c.key, r.folded) end
    b.fold.tip = "Replier / déplier"
    b.fold:Show()
    b.pin:SetPoint("LEFT", b.fold, "RIGHT", 3, 0)
    b.pin.mark:SetShown(r.pinned)
    b.pin.onClick = function() M.TogglePin(c.key) end
    b.pin:Show()
    -- droite → gauche : masquer, ▼, ▲, boussole, verdict, récompense, temps / distance
    b.hide:SetPoint("RIGHT", b, "RIGHT", -4, 0) b.hide.onClick = function() M.ToggleHidden(c.key) end b.hide:Show()
    b.down:SetPoint("RIGHT", b.hide, "LEFT", -1, 0) b.down.onClick = function() M.Nudge(c.key, 1) end b.down:Show()
    b.up:SetPoint("RIGHT", b.down, "LEFT", 0, 0) b.up.onClick = function() M.Nudge(c.key, -1) end b.up:Show()
    b.go:SetPoint("RIGHT", b.up, "LEFT", -3, 0) b.go.onClick = function() M.Go({ questID = c.id, title = c.title }, shift()) end b.go:Show()
    local anchor = b.go
    if c.verdict and VERDICT[c.verdict.v] then
      b.verdict.t:SetTexture(ICON[c.verdict.v] or ICON.keep)
      b.verdict.tip = { VERDICT[c.verdict.v][2] .. VERDICT[c.verdict.v][1] .. "|r", tostring(c.verdict.plain or "") }
      b.verdict.onClick = function() local A = mod("Abandon") if A and A.Report then pcall(A.Report, c.title) end end
      b.verdict:SetPoint("RIGHT", anchor, "LEFT", -3, 0) b.verdict:Show()
      anchor = b.verdict
    end
    local rtxt = ""
    if c.reward then
      b.reward.t:SetTexture(c.reward.icon or ICON.reward)
      b.reward.tip = { "Récompense : " .. tostring(c.reward.item or "objet"), (c.reward.pct or 0) >= 1 and "Mieux que ton équipement" or "Pas mieux que ton équipement" }
      b.reward:SetPoint("RIGHT", anchor, "LEFT", -3, 0) b.reward:Show()
      anchor = b.reward
    end
    b.right:SetPoint("RIGHT", anchor, "LEFT", -4, 0)
    b.right:SetText(rtxt .. tagText(c))
    local rightW = 150 + (c.verdict and 17 or 0) + (c.reward and 17 or 0)
    b.text:SetPoint("LEFT", b.pin, "RIGHT", 4, 0)
    b.text:SetWidth(max(60, w - rightW - 30))
    local col = hex(LEVEL_RGB[c.color] or PRIO_RGB[1])
    b.text:SetText(col .. c.title .. "|r")
    r.tip = M.CardTooltip(c)
    table.insert(r.tip, #r.tip, "|cff888888Glisser : changer l'ordre · flèches haut / bas : monter / descendre|r")
    r.onClick = function(_, btn)
      if btn == "RightButton" then M.ToggleFold(c.key, r.folded) else M.Go({ questID = c.id, title = c.title }, shift()) end
    end
  elseif r.kind == "obj" then
    local ind = 30
    b.bar:ClearAllPoints()
    b.bar:SetPoint("LEFT", b, "LEFT", ind, 0)
    b.bar:SetSize(w - ind - 10, r.h - 4)
    b.bar:SetMinMaxValues(0, max(1, r.need or 1))
    b.bar:SetValue(min(r.have or 0, r.need or 1))
    local pct = (r.have or 0) / max(1, r.need or 1)
    if b.bar.SetStatusBarColor then
      if r.done then b.bar:SetStatusBarColor(0.2, 0.8, 0.25) else b.bar:SetStatusBarColor(0.9, 0.65 + 0.2 * pct, 0.1) end
    end
    setFont(b.bar.fs, fs - 1)
    b.bar.fs:SetWidth(w - ind - 18)
    b.bar.fs:SetText((r.done and "|cffccffcc" or "|cffffffff") .. r.text .. "|r")
    b.bar:Show()
    r.tip = r.o and { r.o.name .. (" %d/%d"):format(r.have, r.need), r.o.hint or "" } or nil
    local qid = r.c.id
    r.onClick = function() M.Go({ questID = qid }, shift()) end
  elseif r.kind == "mates" then
    local ax = 30
    local parts = {}
    for k, g in ipairs(r.c.mates) do
      local a = b.avatars[k]
      if a then
        classIcon(a, g.class)
        a:ClearAllPoints() a:SetPoint("LEFT", b, "LEFT", ax, 0) a:Show()
        ax = ax + 16
      end
      local st = g.noAddon and "|cffaaaaaa(sans TreeAI)|r" or (g.done and "|cff33ff33fini|r") or (("%d/%d"):format(g.have or 0, g.need or 1))
      parts[#parts + 1] = classColor(g.class) .. g.name .. "|r " .. st
    end
    setFont(b.text, fs - 1)
    b.text:SetPoint("LEFT", b, "LEFT", ax + 4, 0)
    b.text:SetWidth(w - ax - 10)
    b.text:SetText(table.concat(parts, "  "))
    r.tip = { "Amis sur cette quête", "Squad (TreeAI) : progression ; sans TreeAI : présence seulement" }
  elseif r.kind == "errand" then
    local e = r.e
    b.accent:Show() color(b.accent, PRIO_RGB[e.prio] or PRIO_RGB[1], 1)
    b.icon:SetSize(fs + 4, fs + 4) b.icon:SetPoint("LEFT", b, "LEFT", 10, 0) b.icon:SetTexture(e.icon) b.icon:Show()
    b.hide:SetPoint("RIGHT", b, "RIGHT", -4, 0) b.hide.onClick = function() M.ToggleHidden(e.key) end b.hide:Show()
    b.text:SetPoint("LEFT", b.icon, "RIGHT", 6, 0)
    b.text:SetWidth(w - fs - 50)
    b.text:SetText(hex(PRIO_RGB[e.prio] or PRIO_RGB[1]) .. e.text .. "|r")
    r.tip = { e.text, "Le même conseil que dans « Maintenant » (sacs, munitions, maître de classe)" }
  elseif r.kind == "board" then
    local e = r.e
    setFont(b.text, fs - 1)
    b.text:SetPoint("LEFT", b, "LEFT", 12, 0)
    b.text:SetWidth(w * 0.5)
    b.text:SetText("|cffffffff" .. cut(e.title, 34) .. "|r")
    b.bar:ClearAllPoints()
    b.bar:SetPoint("RIGHT", b, "RIGHT", -8, 0)
    b.bar:SetSize(w * 0.42, r.h - 6)
    b.bar:SetMinMaxValues(0, 100) b.bar:SetValue(e.pct or 0)
    if b.bar.SetStatusBarColor then b.bar:SetStatusBarColor(0.2, 0.6, 1) end
    setFont(b.bar.fs, fs - 2)
    b.bar.fs:SetWidth(w * 0.42 - 8)
    b.bar.fs:SetText(("%d %% · %d/%d fini%s"):format(e.pct or 0, e.done or 0, e.holders or 0, (e.done or 0) > 1 and "s" or ""))
    b.bar:Show()
    r.tip = { e.title, e.status or "", (e.missing and #e.missing > 0) and ("Sans la quête : " .. table.concat(e.missing, ", ")) or "" }
    local Mi = mod("Missions")
    if e.mission and Mi and Mi.SetGroupTarget then r.onClick = function() pcall(Mi.SetGroupTarget, Mi, e.mission) end end
  elseif r.kind == "foot" then
    setFont(b.text, fs - 1)
    b.text:SetPoint("LEFT", b, "LEFT", 12, 0)
    b.text:SetText("|cff888888" .. r.text .. "|r")
    r.onClick = function() M.ShowAll() end
  else -- empty
    setFont(b.text, fs - 1)
    b.text:SetPoint("LEFT", b, "LEFT", 12, 0)
    b.text:SetWidth(w - 20)
    b.text:SetText("|cffaaaaaa" .. tostring(r.text or "") .. "|r")
  end
  return x
end

-- ------------------------------------------------------------------ fenêtre
local function savePos(f)
  local p, _, _, x, y = f:GetPoint(1)
  M.char.point, M.char.x, M.char.y = p or "RIGHT", x or -60, y or 40
end
local function saveSize(f)
  M.db.width = floor((f:GetWidth() or 380) + 0.5)
  M.db.height = floor((f:GetHeight() or 520) + 0.5)
end

local HEADER_H, CHIPS_H, SESSION_H = 26, 46, 26
function M.CreateFrame()
  if frame or type(_G.CreateFrame) ~= "function" then return frame end
  local f = _G.CreateFrame("Frame", "TreeAITodoFrame", _G.UIParent)
  frame = f
  M.frame = f
  f:SetSize(opt("width", 380), opt("height", 520))
  f:SetPoint(M.char.point or "RIGHT", _G.UIParent, M.char.point or "RIGHT", M.char.x or -60, M.char.y or 40)
  f:SetFrameStrata("MEDIUM")
  f:SetMovable(true)
  f:EnableMouse(true)
  f:SetClampedToScreen(true)
  if f.SetResizable then f:SetResizable(true) end
  if f.SetResizeBounds then pcall(f.SetResizeBounds, f, 340, 260, 700, 1000)
  elseif f.SetMinResize then pcall(f.SetMinResize, f, 340, 260) end
  f.bg = f:CreateTexture(nil, "BACKGROUND")
  f.bg:SetAllPoints(f)
  -- liseré fin (4 textures, pas de modèle Backdrop)
  f.edges = {}
  for k, pt in ipairs({ { "TOPLEFT", "TOPRIGHT", true }, { "BOTTOMLEFT", "BOTTOMRIGHT", true }, { "TOPLEFT", "BOTTOMLEFT" }, { "TOPRIGHT", "BOTTOMRIGHT" } }) do
    local e = f:CreateTexture(nil, "BORDER")
    e:SetPoint(pt[1], f, pt[1], 0, 0) e:SetPoint(pt[2], f, pt[2], 0, 0)
    if pt[3] then e:SetHeight(1) else e:SetWidth(1) end
    color(e, { 1, 0.82, 0 }, 0.35)
    f.edges[k] = e
  end
  -- en-tête : icône, titre, compteur, fermer (glisser pour déplacer)
  local head = _G.CreateFrame("Button", nil, f)
  head:SetPoint("TOPLEFT", f, "TOPLEFT", 0, 0) head:SetPoint("TOPRIGHT", f, "TOPRIGHT", 0, 0)
  head:SetHeight(HEADER_H)
  head.bg = head:CreateTexture(nil, "BACKGROUND") head.bg:SetAllPoints(head) color(head.bg, { 1, 0.82, 0 }, 0.10)
  head:RegisterForDrag("LeftButton")
  head:SetScript("OnDragStart", function() if not opt("lock", false) then f:StartMoving() end end)
  head:SetScript("OnDragStop", function() f:StopMovingOrSizing() savePos(f) end)
  head.icon = head:CreateTexture(nil, "ARTWORK") head.icon:SetSize(18, 18) head.icon:SetPoint("LEFT", head, "LEFT", 6, 0)
  head.icon:SetTexture(ICON.quests)
  head.title = head:CreateFontString(nil, "OVERLAY", "GameFontNormal")
  head.title:SetPoint("LEFT", head.icon, "RIGHT", 6, 0)
  head.title:SetText("À faire")
  head.count = head:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
  head.count:SetPoint("LEFT", head.title, "RIGHT", 8, 0)
  head.close = iconButton(head, ICON.close, 16, "Fermer (/tai todo)", function() M.Show(false) end)
  head.close:SetPoint("RIGHT", head, "RIGHT", -6, 0)
  head.route = textButton(head, "Itinéraire", 66, 16, function() M.Route() end)
  head.route:SetPoint("RIGHT", head.close, "LEFT", -6, 0)
  head.route.tip = "Tout le journal en un trajet (Shortest Path → boussole)"
  head.sort = textButton(head, "Tri", 82, 16, function() M.CycleSort() end)
  head.sort:SetPoint("RIGHT", head.route, "LEFT", -4, 0)
  head.sort.tip = "Changer l'ordre (les plus rapides, en chemin, les plus proches, niveau, zone)"
  head.reset = textButton(head, "Ordre auto", 72, 16, function() M.ResetOrder() end)
  head.reset:SetPoint("RIGHT", head.sort, "LEFT", -4, 0)
  head.reset.tip = "Oublier l'ordre choisi à la main (les plus rapides d'abord)"
  f.head = head
  -- puces de filtre + recherche
  local chips = _G.CreateFrame("Frame", nil, f)
  chips:SetPoint("TOPLEFT", head, "BOTTOMLEFT", 0, 0) chips:SetPoint("TOPRIGHT", head, "BOTTOMRIGHT", 0, 0)
  chips:SetHeight(CHIPS_H)
  f.chips = {}
  for i, id in ipairs(M.FILTERS) do
    local c = textButton(chips, M.FILTER_TXT[id], 56, 18, function() M.SetFilter(id) end)
    c.id = id
    c.tip = "Filtre : " .. M.FILTER_TXT[id]
    f.chips[i] = c
  end
  local eb = _G.CreateFrame("EditBox", nil, chips)
  eb:SetHeight(18)
  eb:SetPoint("BOTTOMLEFT", chips, "BOTTOMLEFT", 8, 3) eb:SetPoint("BOTTOMRIGHT", chips, "BOTTOMRIGHT", -8, 3)
  if eb.SetAutoFocus then eb:SetAutoFocus(false) end
  if eb.SetFontObject and _G.GameFontHighlightSmall then pcall(eb.SetFontObject, eb, _G.GameFontHighlightSmall) end
  if eb.SetTextInsets then eb:SetTextInsets(18, 4, 0, 0) end
  eb.bg = eb:CreateTexture(nil, "BACKGROUND") eb.bg:SetAllPoints(eb) color(eb.bg, { 0, 0, 0 }, 0.5)
  eb.icon = eb:CreateTexture(nil, "ARTWORK") eb.icon:SetSize(12, 12) eb.icon:SetPoint("LEFT", eb, "LEFT", 4, 0)
  eb.icon:SetTexture("Interface\\Common\\UI-Searchbox-Icon")
  eb.hint = eb:CreateFontString(nil, "OVERLAY", "GameFontDisableSmall")
  eb.hint:SetPoint("LEFT", eb, "LEFT", 20, 0) eb.hint:SetText("Rechercher (quête, zone, objectif)…")
  eb:SetScript("OnTextChanged", function(self)
    local t = self:GetText() or ""
    eb.hint:SetShown(t == "")
    if t ~= (M.char.search or "") then M.SetSearch(t) end
  end)
  eb:SetScript("OnEscapePressed", function(self) self:SetText("") if self.ClearFocus then self:ClearFocus() end end)
  eb:SetScript("OnEnterPressed", function(self) if self.ClearFocus then self:ClearFocus() end end)
  f.search = eb
  -- zone défilante
  local sf = _G.CreateFrame("ScrollFrame", nil, f)
  sf:SetPoint("TOPLEFT", chips, "BOTTOMLEFT", 4, -2)
  sf:SetPoint("BOTTOMRIGHT", f, "BOTTOMRIGHT", -8, SESSION_H + 2)
  local content = _G.CreateFrame("Frame", nil, sf)
  content:SetSize(opt("width", 380) - 16, 10)
  sf:SetScrollChild(content)
  sf:EnableMouseWheel(true)
  sf:SetScript("OnMouseWheel", function(self, delta) M.Scroll(-delta * 40) end)
  f.scroll, f.content = sf, content
  f.thumb = f:CreateTexture(nil, "OVERLAY")
  f.thumb:SetWidth(3)
  color(f.thumb, { 1, 0.82, 0 }, 0.5)
  f.dropLine = content:CreateTexture(nil, "OVERLAY")
  f.dropLine:SetHeight(2)
  color(f.dropLine, { 1, 0.82, 0 }, 0.95)
  f.dropLine:Hide()
  -- barre de session
  local sb = _G.CreateFrame("StatusBar", nil, f)
  sb:SetPoint("BOTTOMLEFT", f, "BOTTOMLEFT", 6, 5) sb:SetPoint("BOTTOMRIGHT", f, "BOTTOMRIGHT", -22, 5)
  sb:SetHeight(SESSION_H - 8)
  if sb.SetStatusBarTexture then sb:SetStatusBarTexture(ICON.bar) end
  if sb.SetStatusBarColor then sb:SetStatusBarColor(0.58, 0.2, 0.9) end
  sb:SetMinMaxValues(0, 1)
  sb.bg = sb:CreateTexture(nil, "BACKGROUND") sb.bg:SetAllPoints(sb) color(sb.bg, { 0, 0, 0 }, 0.55)
  sb.rest = sb:CreateTexture(nil, "BORDER") sb.rest:SetPoint("TOPLEFT", sb, "TOPLEFT", 0, 0) sb.rest:SetPoint("BOTTOMLEFT", sb, "BOTTOMLEFT", 0, 0)
  color(sb.rest, { 0.2, 0.45, 1 }, 0.45)
  sb.fs = sb:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
  sb.fs:SetPoint("CENTER", sb, "CENTER", 0, 0)
  sb:EnableMouse(true)
  sb:SetScript("OnEnter", function(self) showTip(self, { "Session", self.fs:GetText() or "", "Temps estimé d'après ton rythme de jeu" }) end)
  sb:SetScript("OnLeave", hideTip)
  f.session = sb
  -- poignée de redimensionnement
  local grip = _G.CreateFrame("Button", nil, f)
  grip:SetSize(16, 16)
  grip:SetPoint("BOTTOMRIGHT", f, "BOTTOMRIGHT", -2, 2)
  grip.t = grip:CreateTexture(nil, "ARTWORK") grip.t:SetAllPoints(grip) grip.t:SetTexture(ICON.grip)
  grip:SetScript("OnMouseDown", function() if not opt("lock", false) then f:StartSizing("BOTTOMRIGHT") end end)
  grip:SetScript("OnMouseUp", function() f:StopMovingOrSizing() saveSize(f) savePos(f) M.RequestRender(true) end)
  f.grip = grip
  f:SetScript("OnSizeChanged", function() M.RequestRender(true) end)
  f:SetScript("OnHide", function() M.EndDrag(true) end)
  -- Échap ferme, fondu d'ouverture (habillage et en-tête du module gardés)
  if T.Theme and T.Theme.Window then T.Theme:Window(f, { id = "todo", header = false, skin = false }) end
  f:Hide()
  -- mode édition du cœur (POLISH) : enregistrement gardé, jamais requis
  if T.RegisterMovable then pcall(T.RegisterMovable, T, f, "todo", "À faire", { resizable = true, onMoved = function() savePos(f) saveSize(f) end }) end
  return f
end

M.scroll = 0
function M.Scroll(dy)
  if not frame then return end
  local maxS = max(0, (M.contentH or 0) - (frame.scroll:GetHeight() or 0))
  M.scroll = max(0, min(maxS, (M.scroll or 0) + (dy or 0)))
  frame.scroll:SetVerticalScroll(M.scroll)
  M.UpdateThumb()
end
function M.UpdateThumb()
  if not frame then return end
  local vh, ch = frame.scroll:GetHeight() or 1, M.contentH or 1
  if ch <= vh + 1 then frame.thumb:Hide() return end
  local th = max(20, vh * vh / ch)
  local y = (M.scroll or 0) / (ch - vh) * (vh - th)
  frame.thumb:ClearAllPoints()
  frame.thumb:SetPoint("TOPRIGHT", frame.scroll, "TOPRIGHT", 6, -y)
  frame.thumb:SetHeight(th)
  frame.thumb:Show()
end

-- glisser-déposer des cartes : ligne d'insertion suivie à la souris (OnUpdate seulement pendant le glisser)
local function cursorY()
  if not (frame and frame.content) then return 0 end
  local _, cy = try(_G.GetCursorPosition)
  local sc = frame.content.GetEffectiveScale and frame.content:GetEffectiveScale() or 1
  local top = frame.content.GetTop and frame.content:GetTop() or 0
  return (top or 0) - (tonumber(cy) or 0) / max(0.01, sc or 1)
end
function M.BeginDrag(key)
  M.dragKey = key
  if not frame then return end
  frame.content:SetScript("OnUpdate", function()
    local target, after = M.TargetAt(M.rows, cursorY())
    frame.dropLine:ClearAllPoints()
    for _, r in ipairs(M.rows or {}) do
      if r.key == target and r.kind == "card" then
        frame.dropLine:SetPoint("TOPLEFT", frame.content, "TOPLEFT", 4, -(after and (r.y + r.h) or r.y))
        frame.dropLine:SetWidth((frame.content:GetWidth() or 300) - 8)
        frame.dropLine:Show()
      end
    end
  end)
end
function M.EndDrag(cancel)
  local key = M.dragKey
  M.dragKey = nil
  if frame then frame.content:SetScript("OnUpdate", nil) frame.dropLine:Hide() end
  if cancel or not key then return nil end
  local target, after = M.TargetAt(M.rows, cursorY())
  if target and target ~= key then return M.DropOn(key, target, after) end
end

local function styleChrome(f, model)
  local a = (tonumber(opt("alpha", 88)) or 88) / 100
  color(f.bg, { 0.04, 0.04, 0.06 }, a)
  f:SetScale((tonumber(opt("scale", 100)) or 100) / 100)
  local ready = 0
  for _, c in ipairs(model.all) do if c.complete then ready = ready + 1 end end
  f.head.count:SetText(("|cffaaaaaa%d quête%s%s|r"):format(model.total, model.total > 1 and "s" or "",
    ready > 0 and (" · |cff33ff33%d à rendre|r"):format(ready) or ""))
  f.head.reset:SetShown(#(M.char.order or {}) > 0)
  f.head.sort.fs:SetText("|cffbbbbbb" .. M.SortLabel() .. "|r")
  -- puces : 2 rangées si étroit
  local w = f:GetWidth() or 380
  local cw = floor((w - 16 - 5 * 4) / 6)
  for i, c in ipairs(f.chips) do
    c:ClearAllPoints()
    c:SetSize(cw, 18)
    c:SetPoint("TOPLEFT", f.chips[1]:GetParent(), "TOPLEFT", 8 + (i - 1) * (cw + 4), -3)
    local act = model.filter == c.id
    color(c.bg, act and { 1, 0.82, 0 } or { 1, 1, 1 }, act and 0.35 or 0.08)
    c.fs:SetText((act and "|cffffffff" or "|cffbbbbbb") .. M.FILTER_TXT[c.id] .. "|r")
  end
  if f.search and (f.search:GetText() or "") ~= (model.search or "") then f.search:SetText(model.search or "") end
  local s = model.session
  if s then
    f.session:SetValue(s.pct or 0)
    local rw = (s.xpMax > 0 and s.rested > 0) and min(1, (s.xp + s.rested) / s.xpMax) or 0
    f.session.rest:SetWidth(max(1, (f.session:GetWidth() or 300) * rw))
    f.session.rest:SetShown(rw > (s.pct or 0))
    f.session.fs:SetText(M.SessionText(s))
    f.session:Show()
  else
    f.session:Hide()
  end
end

-- Rendu complet (cheap : mise en page refaite seulement si le contenu a changé)
local function signature(rows, w)
  local t = { tostring(w) }
  for _, r in ipairs(rows) do
    local c = r.c
    t[#t + 1] = r.kind .. (r.key or r.id or "") .. (r.text or "") .. (c and (tostring(c.eta) .. tostring(c.dist) .. tostring(c.complete)) or "")
      .. (r.a and (r.a.title .. tostring(r.a.eta)) or "") .. (r.e and (tostring(r.e.text) .. tostring(r.e.pct)) or "")
      .. (r.s and tostring(r.s.label) or "") .. tostring(r.folded) .. tostring(r.pinned)
    if c and r.kind == "card" then
      for _, o in ipairs(c.objs) do t[#t + 1] = o.have .. "/" .. o.need end
      for _, g in ipairs(c.mates) do t[#t + 1] = g.name .. tostring(g.have) .. tostring(g.done) end
    end
  end
  return table.concat(t, "|")
end

function M.Render(force)
  if not (frame and frame:IsShown()) then return nil end
  local model = M.Collect()
  styleChrome(frame, model)
  local fs = tonumber(opt("fontSize", 12)) or 12
  local rows, h = M.Rows(model, fs)
  local w = (frame:GetWidth() or 380) - 16
  local sig = signature(rows, w) .. (M.char.filter or "")
  M.rows, M.contentH = rows, h
  if not force and sig == M.sig then return #rows end
  M.sig = sig
  frame.content:SetSize(w, max(1, h))
  for i, r in ipairs(rows) do
    local b = rowWidget(i)
    b:ClearAllPoints()
    b:SetPoint("TOPLEFT", frame.content, "TOPLEFT", 0, -r.y)
    b:SetSize(w, r.h)
    M.Paint(b, r, w, fs)
    b:Show()
  end
  for i = #rows + 1, #pool do pool[i]:Hide() pool[i].row = nil end
  M.Scroll(0)
  M.renders = (M.renders or 0) + 1
  return #rows
end

local pendingRender, lastRender = false, -10
function M.RequestRender(force)
  if force then M.sig = nil end
  if not (frame and frame:IsShown()) or pendingRender then return end
  pendingRender = true
  T:After(max(0.05, 0.5 - (now() - lastRender)), function()
    pendingRender = false
    lastRender = now()
    pcall(M.Render)
  end)
end

function M.Show(on)
  if on == nil then on = true end
  M.db.shown = on and true or false
  if on then
    M.CreateFrame()
    if not frame then return false end
    frame:Show()
    M.sig = nil
    local Tr = mod("Tracker")
    if Tr and not Tr.items and Tr.Update then pcall(Tr.Update, true) end
    pcall(M.Render, true)
  elseif frame then
    frame:Hide()
  end
  local Tr = mod("Tracker")
  if Tr and Tr.PushSoon then Tr.PushSoon() end
  return on
end
function M.Toggle() return M.Show(not (frame and frame:IsShown())) end

-- ------------------------------------------------------------------ contrat Tracker
-- Tracker.Push appelle ceci : renvoie les lignes de l'onglet (version compacte) si l'option « tab » est active.
function M.Active() return M.IsEnabled and M:IsEnabled() and true or false end
function M.FromTracker()
  if not M.Active() then return nil end
  if frame and frame:IsShown() then M.RequestRender() end
  if not on("tab") then return nil end
  local ok, lines = pcall(M.TabLines)
  if ok then return lines end
  M:Debug("TabLines: " .. tostring(lines))
  return nil
end

function M.WriteSummary()
  local s = T.Summary and T:Summary()
  if type(s) ~= "table" then return end
  local model = M.model
  local np, nh, no = 0, 0, #(M.char and M.char.order or {})
  for _ in pairs(M.char and M.char.pins or {}) do np = np + 1 end
  for _ in pairs(M.char and M.char.hidden or {}) do nh = nh + 1 end
  local a = model and model.now
  s.todo = { updated = try(_G.time), now = a and { kind = a.kind, text = cut(a.title, 90), why = a.why, eta = a.eta and floor(a.eta + 0.5) or nil } or nil,
    pins = np, hidden = nh, ordered = no, filter = M.char and M.char.filter or "tous",
    errands = model and #model.errands or 0, shown = frame and frame:IsShown() or false }
end

function M:OnEnable()
  self:On("SUMMARY", function() pcall(M.WriteSummary) end)
  self:On("SQUAD_UPDATE", function() M.RequestRender() end)
  for _, ev in ipairs({ "PLAYER_XP_UPDATE", "UPDATE_EXHAUSTION", "PLAYER_LEVEL_UP", "UPDATE_INVENTORY_DURABILITY" }) do
    self:RegisterEvent(ev, function() M.RequestRender() end)
  end
  if M.db.shown then T:After(1.5, function() if M:IsEnabled() and M.db.shown then M.Show(true) end end) end
end

function M:OnDisable()
  if frame then frame:Hide() end
  local Tr = mod("Tracker")
  if Tr and Tr.PushSoon then Tr.PushSoon() end
end

function M:OnOptionChanged(key, value)
  if key == "shown" then M.Show(value and true or false) return end
  M.RequestRender(true)
  local Tr = mod("Tracker")
  if Tr and Tr.PushSoon then Tr.PushSoon() end
end

-- Core:StartAutoRefresh : barre de session / courses / groupe quand la fenêtre est ouverte (rien sinon)
M.autoIdleSafe = true   -- (4.0.1) fenêtre fermée : rien ; au calme, son rendu suit les poussées du Suivi
function M:AutoRefresh()
  if frame and frame:IsShown() then M.RequestRender() end
end

local function slash(rest)
  local cmd = low(rest):match("^%s*(%S*)")
  if cmd == "" then M.Toggle()
  elseif cmd == "montrer" or cmd == "on" or cmd == "ouvrir" then M.Show(true)
  elseif cmd:find("^cach") or cmd == "off" or cmd == "fermer" then M.Show(false)
  elseif cmd == "ordre" then M.ResetOrder() M:Print("Ordre automatique rétabli (les plus rapides d'abord).")
  elseif cmd == "tout" then M.ShowAll() M:Print("Tout est réaffiché.")
  elseif cmd == "reset" then
    M.char.pins, M.char.hidden, M.char.order, M.char.folded, M.char.sections, M.char.search, M.char.filter = {}, {}, {}, {}, {}, "", "tous"
    M.char.point, M.char.x, M.char.y = "RIGHT", -60, 40
    if frame then frame:ClearAllPoints() frame:SetPoint("RIGHT", _G.UIParent, "RIGHT", -60, 40) end
    save()
    M:Print("« À faire » remis à zéro (épingles, masqués, ordre, position).")
  else
    M:Print("/tai todo [montrer|cacher|ordre|tout|reset]")
  end
end
T:RegisterSlash("todo", slash, "fenêtre « À faire » : /tai todo [montrer|cacher|ordre|tout|reset]")
T:RegisterSlash("afaire", slash)
