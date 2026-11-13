-- TreeAI — « Pilote auto » (agent BRAIN, 4.0.2) : TreeAI décide tout seul ce qui est SÛR, LOGIQUE et RÉVERSIBLE,
-- et dit pourquoi. Chaque décision est visible (une fois sur la carte « Prochaine étape », puis dans la page
-- « Pilote auto » de la fenêtre : quoi, quand, pourquoi, bouton « Annuler ») ; le choix explicite du joueur gagne
-- pour la session (une quête reprise, une zone ou un mode changé à la main ne sont plus touchés).
--  1. CONTEXTE (T.Brain.Context(), lu par les autres modules) : mort / fantôme, donjon, en vol, absent, en ville,
--     en route, à farmer, en quête, calme ; drapeaux : JcJ en zone dangereuse, groupe dispersé, niveau tout juste
--     pris, sacs pleins, munitions basses, réparation, repos, journal plein. Événementiel (aucune minuterie).
--  2. DÉCISIONS (rien de protégé, tout se défait d'un clic) :
--     * quêtes EN PAUSE : retirées du suivi du jeu et du trajet, rangées « En pause » avec la raison — faits du jeu
--       seulement (module « Nettoyer mon journal » : grise, échouée, chaîne bloquée, doublon, donjon déjà fait, trop
--       dure pour le groupe ACTUEL, autre continent / très loin) + « rapporte presque rien pour le détour » (XP réelle
--       du jeu, détour mesuré par Route / NavTime, XP/h mesurée par Stats ≥ 10 min ; sinon la règle attend).
--       Reprise toute seule quand la raison disparaît (le groupe grandit, un niveau, le trajet passe à côté, tu y
--       avances, elle est finie). JAMAIS abandonnée : l'abandon reste à la page « Journal » (2 clics, hors combat) ;
--     * zone de quêtes choisie quand tu n'en as pas choisi (ta zone si elle a encore assez de quêtes, sinon la
--       meilleure zone voisine du même continent d'après QuestieDB) — annoncé une fois ;
--     * mode du trajet selon la situation (groupe formé → « Groupe », quand ZonePlan offre les modes) ;
--     * courses sur le trajet (réparer, vendre, munitions, maître de classe) : un arrêt inséré au plus court quand
--       le trajet passe près d'un marchand CONNU (QuestieDB / PNJ déjà vus) ou quand c'est urgent ; sinon une note ;
--     * pierre de foyer quand plus rien n'est à faire ici et que des quêtes finies se rendent près de l'auberge ;
--     * « Suivre le groupe » (coupé par défaut) : la zone / le donjon / le point de rencontre que le groupe veut
--       (contrat du module Groupe : Group.Intent(), voir README).
-- Coût : événements + un rappel T:After regroupé ; rien quand rien ne change ; calculs hors combat. Aucune fonction
-- protégée (seulement C_QuestLog.AddQuestWatch / RemoveQuestWatch, réglages de nos modules, boussole) ; rien dans le
-- chat. Le cœur (B.Core) est pur : tests/wow/brain_spec.lua. Lua 5.1, aucune globale.
local tonumber = function(v) return _G.tonumber(v) end   -- (4.0.8) tonumber(f()) : un 2e retour de f n'est jamais une base
local T = _G.TreeAI
if not (T and T.RegisterModule) then return end

-- règles sûres (faits du jeu, réversibles) : rangé avec les fonctions du mode sûr
if type(T.SAFE_MODULES) == "table" then T.SAFE_MODULES.Brain = true end
if type(T.MODULE_INFO) == "table" then
  T.MODULE_INFO.Brain = T.MODULE_INFO.Brain or { lite = true }
  T.MODULE_INFO.Brain.desc = "Pilote auto : quêtes inutiles en pause, zone et mode du trajet, courses, pourquoi"
end

local B = T:RegisterModule("Brain", {
  title = "Pilote auto",
  description = "TreeAI décide seul ce qui est sûr et logique, et dit pourquoi (page « Pilote auto » : annuler d'un clic).",
  defaults = { enabled = true, park = true, lowValue = true, zone = true, style = true, errands = true, hearth = true,
    follow = false, notice = true },
  -- parked[q] = { k, t, at, n, lv, w, p, x, z, l } ; log = décisions (≤ 30) ; ov = choix du joueur de la session ;
  -- pick = zone choisie par le pilote ; style = mode posé par le pilote ; stops = courses insérées dans le trajet
  charDefaults = { parked = {}, log = {}, ov = false, seq = 0, seen = 0, pick = false, style = false, stops = {},
    train = false },
  options = {
    { section = "Ce que le pilote décide seul", key = "park", type = "toggle",
      label = "Mettre en pause les quêtes qui ne servent à rien (suivi + trajet, avec la raison ; jamais abandonnées)" },
    { key = "lowValue", type = "toggle", label = "… et celles qui rapportent presque rien pour le détour (XP/h mesurée)" },
    { key = "zone", type = "toggle", label = "Choisir la zone de quêtes quand tu n'en as pas choisi" },
    { key = "style", type = "toggle", label = "Mode du trajet selon la situation (groupe formé : « Groupe »)" },
    { key = "errands", type = "toggle", label = "Courses sur le trajet : réparer, vendre, munitions, maître de classe" },
    { key = "hearth", type = "toggle", label = "Pierre de foyer conseillée quand elle fait gagner du temps" },
    { key = "follow", type = "toggle", label = "Suivre le groupe (zone, donjon ou point de rencontre voulus par le groupe)" },
    { section = "Affichage", key = "notice", type = "toggle", label = "Annoncer chaque décision une fois sur la carte « Prochaine étape »" },
  },
})
if not B then return end
T.Brain = B
if type(T.MODULE_TAB) == "table" and not T.MODULE_TAB.Brain then T.MODULE_TAB.Brain = "quests" end

local floor, max, min, abs = math.floor, math.max, math.min, math.abs
local fmt, tconcat, tsort = string.format, table.concat, table.sort

-- ===================================================================== utilitaires (appels gardés)
local function pass(ok, ...) if ok then return ... end return nil end
local function try(fn, ...) if type(fn) ~= "function" then return nil end return pass(pcall(fn, ...)) end
local function G(name) return rawget(_G, name) end
local function C(ns, fn) local t = G(ns) return type(t) == "table" and t[fn] or nil end
local function sv(v) local f = G("issecretvalue") if v ~= nil and f and f(v) then return nil end return v end
local function num(v) return tonumber(sv(v)) end
local function now() return tonumber(try(G("GetTime"))) or 0 end
local function epoch() return tonumber(try(G("time"))) or floor(now()) end
local function inCombat() return T.InCombat and T:InCombat() or false end
local function opt(k) return B.db and B.db[k] ~= false and (k ~= "follow" or B.db.follow == true) end
local function cut(s, n) s = tostring(s or "") if T.Cut then return T.Cut(s, n) end return s:sub(1, n) end
local function myLevel() return num(try(G("UnitLevel"), "player")) or 1 end
local function groupSize() return max(1, num(try(G("GetNumGroupMembers"))) or 0) end
local function zp() local Z = T.ZonePlan return (type(Z) == "table" and Z.IsEnabled and Z:IsEnabled()) and Z or nil end
local function mod(n) local m = T.modules and T.modules[n] return type(m) == "table" and m or nil end
local function thousands(n)
  local s = tostring(floor((tonumber(n) or 0) + 0.5)):reverse():gsub("(%d%d%d)", "%1 "):reverse()
  return (s:gsub("^%s+", ""))
end
local function plural(n, one, many) return fmt("%d %s", n, n > 1 and many or one) end
local function mapName(m)
  local Z = T.ZonePlan
  if Z and Z.MapName then return Z.MapName(m) end
  local i = try(C("C_Map", "GetMapInfo"), m)
  return type(i) == "table" and i.name or ("zone " .. tostring(m))
end
local function zoneOf(m) local Z = T.ZonePlan return (Z and Z.ZoneOf and m) and Z.ZoneOf(m) or m end

-- capitales (uiMapID du client : Orgrimmar, Les Pitons-du-Tonnerre, Fossoyeuse, Hurlevent, Forgefer, Darnassus)
B.CITY = { [1454] = "H", [1456] = "H", [1458] = "H", [1453] = "A", [1455] = "A", [1457] = "A" }

-- ===================================================================== cœur pur (testé seul : tests/wow/brain_spec.lua)
local K = {}
B.Core = K
K.PARK_KEYS = { failed = true, blocked = true, dup = true, grey = true, dungeon = true, hard = true, far = true, low = true }
K.STRONG = { failed = true, blocked = true, dup = true, grey = true, dungeon = true }   -- « Abandonner ? » proposé (page Journal)
-- raisons que les règles du trajet (ZonePlan : zone, élite / donjon / groupe selon la taille du groupe, niveau) ne jugent
-- pas : seules celles-là retirent la quête du trajet ; « trop dure » et « loin » restent à ZonePlan (une quête qu'il
-- prend — groupe de 3, donjons cochés — n'est jamais « en pause » : elle est sur le trajet)
K.ROUTE_KEYS = { failed = true, blocked = true, dup = true, grey = true, dungeon = true, low = true }
K.WHY = { failed = "Échouée", blocked = "Chaîne bloquée", dup = "Doublon", grey = "Grise", dungeon = "Donjon déjà fait",
  hard = "Trop dure pour le groupe actuel", far = "Autre continent ou très loin", low = "Presque rien pour le détour" }
K.ADVICE = { failed = "Elle n'avance plus : abandonne-la puis reprends-la (page « Journal »).",
  blocked = "La suite est déjà prise ou faite : elle ne mène plus à rien.",
  dup = "Une seule des deux compte : garde l'autre.",
  grey = "Presque plus d'XP à ton niveau.",
  dungeon = "Tu as déjà fait ce donjon : rien à y gagner.",
  hard = "Elle reviendra toute seule quand le groupe sera assez grand (ou au niveau qu'il faut).",
  far = "Elle reviendra quand ton trajet passera à côté.",
  low = "Le détour coûte plus que ce qu'elle rapporte à ton rythme mesuré ; elle revient si le trajet passe à côté." }
K.SHORT = { failed = "échouée", blocked = "bloquée", dup = "doublon", grey = "grise", dungeon = "donjon fait",
  hard = "trop dure", far = "loin", low = "détour" }
K.LABEL = { dead = "Mort", ghost = "Fantôme", dungeon = "En donjon", taxi = "En vol", afk = "Absent", city = "En ville",
  travel = "En route", grind = "Tu farmes", quest = "En quête", idle = "Calme" }
-- zone : choisie seulement quand le trajet (ta zone + les voisines que ZonePlan ajoute) n'a plus RIEN (une remise compte),
-- et une autre zone doit avoir au moins 6 quêtes à ton niveau
K.MIN_HERE, K.MIN_PICK = 1, 6
K.LOW_RATIO, K.LOW_DETOUR = 0.25, 150 -- détour : < 25 % de l'XP/h mesurée, et au moins 2 min 30 de détour
K.NEAR_YD = 150                       -- « le trajet passe à côté » : un arrêt à moins de 150 yd (≈ 140 m)
K.FRESH_S, K.PROGRESS_S = 300, 900    -- jamais en pause : prise il y a < 5 min, ou avancée il y a < 15 min
K.CALM_S = 600                        -- reprise toute seule : pas remise en pause pour la même raison avant 10 min

--- contexte principal (un seul) d'après des signaux bruts
function K.Main(s)
  s = s or {}
  if s.dead or s.ghost then return s.ghost and "ghost" or "dead" end
  if s.instance then return "dungeon" end
  if s.taxi then return "taxi" end
  if s.afk then return "afk" end
  if s.city then return "city" end
  if s.travel then return "travel" end
  if s.grind then return "grind" end
  if s.quest then return "quest" end
  return "idle"
end
--- « tu farmes » : ≥ 3 combats en 5 min, aucune quête avancée en 5 min, pas d'arrêt du trajet à moins de 400 yd
function K.Grinding(fights, t, progressAt, stopYd)
  local n = 0
  for _, f in ipairs(fights or {}) do if t - f <= 300 then n = n + 1 end end
  if n < 3 then return false end
  if progressAt and t - progressAt <= 300 then return false end
  return not (stopYd and stopYd <= 400)
end
--- besoins (faits du jeu) → liste { key, text, urgent, kind = genre de PNJ, short } dans l'ordre d'importance
--- r = { free, durMin, broken, ammo (nil = pas d'arme à munitions), ammoMin (minutes au rythme mesuré), train, trainCost }
function K.Needs(r)
  r = r or {}
  local out = {}
  local function add(key, text, urgent, kind, short) out[#out + 1] = { key = key, text = text, urgent = urgent and true or false, kind = kind, short = short } end
  local dur, broken = tonumber(r.durMin), tonumber(r.broken) or 0
  if dur and (dur <= 10 or broken > 0) then
    add("repair", broken > 0 and fmt("Répare : %s cassée%s", plural(broken, "pièce", "pièces"), broken > 1 and "s" or "") or fmt("Répare : équipement à %d %%", dur), true, "repair", "répare")
  elseif dur and dur <= 25 then add("repair", fmt("Répare bientôt : équipement à %d %%", dur), false, "repair", "répare") end
  local free = tonumber(r.free)
  if free and free <= 0 then add("bags", "Sacs pleins : vends chez un marchand", true, "vendor", "vends")
  elseif free and free <= 3 then add("bags", fmt("Sacs : plus que %s", plural(free, "place", "places")), false, "vendor", "vends") end
  local ammo = tonumber(r.ammo)
  if ammo then
    local word = r.ammoWord or "munitions"
    local m = tonumber(r.ammoMin)
    if ammo < 100 then add("ammo", fmt("Munitions : plus que %d %s", ammo, word), true, "vendor", "achète des " .. word)
    elseif ammo < 400 or (m and m < 30) then
      add("ammo", fmt("Munitions : %d %s%s", ammo, word, m and fmt(" (≈ %d min de jeu)", floor(m)) or ""), false, "vendor", "achète des " .. word)
    end
  end
  if tonumber(r.train) then
    add("train", tonumber(r.trainCost) and r.trainCost > 0 and fmt("Maître de classe : sorts du niveau %d", r.train)
      or fmt("Maître de classe : nouveaux sorts (niveau %d)", r.train), false, "trainer", "maître de classe")
  end
  return out
end
--- signature des besoins (genres + urgence) : ce qui change le trajet, pas les chiffres
function K.NeedsSig(needs)
  local t = {}
  for _, n in ipairs(needs or {}) do t[#t + 1] = n.key .. (n.urgent and "!" or "") end
  return tconcat(t, ",")
end
--- « Répare · vends · achète des flèches (Forgeron) » : l'étiquette d'un arrêt « course »
function K.StopLabel(words, name)
  local s = tconcat(words or {}, " · ")
  s = s:sub(1, 1):upper() .. s:sub(2)
  if name and name ~= "" then s = s .. " (" .. name .. ")" end
  return s
end
--- « répare · vends · achète des flèches » (les mots courts, sans doublon)
function K.NeedsShort(needs)
  local t, seen = {}, {}
  for _, n in ipairs(needs or {}) do
    if n.short and not seen[n.short] then seen[n.short] = true t[#t + 1] = n.short end
  end
  return tconcat(t, " · ")
end
--- détour qui ne vaut pas le coup ? xp (vraie), detour (s, Route / NavTime), xph (mesurée) → vrai, texte
function K.LowValue(xp, detour, xph)
  xp, detour, xph = tonumber(xp), tonumber(detour), tonumber(xph)
  if not (xp and xp > 0 and detour and detour >= K.LOW_DETOUR and xph and xph > 0) then return false end
  local rate = xp * 3600 / (detour + 30)
  if rate >= K.LOW_RATIO * xph then return false end
  return true, fmt("%s XP pour ≈ %d min de détour (tu fais ≈ %s XP/h)", thousands(xp), floor(detour / 60 + 0.5), thousands(xph))
end
--- zone : nil = garder la zone en cours ; sinon la meilleure autre zone.
--- here = quêtes encore à faire ici ; cands = { {map, n, sec (trajet) } }
function K.ZonePick(here, cands)
  if (tonumber(here) or 0) >= K.MIN_HERE then return nil end
  local best, bs
  for _, c in ipairs(cands or {}) do
    local n = tonumber(c.n) or 0
    if n >= K.MIN_PICK and n > (tonumber(here) or 0) then
      local s = n / (1 + (tonumber(c.sec) or 600) / 300)   -- une zone à 5 min vaut moitié moins qu'une zone à côté
      if not bs or s > bs or (s == bs and c.map < best.map) then best, bs = c, s end
    end
  end
  return best
end
--- mode du trajet voulu : groupe ≥ 2 et mode « le plus rapide » → « groupe » ; groupe dissous → le mode d'avant
--- cur = id du mode en cours, set = { set, prev } posé par le pilote (ou nil), n = taille du groupe → id voulu ou nil
function K.StyleFor(n, cur, set)
  if set then
    if cur ~= set.set then return nil end                 -- le joueur a changé : rien
    if n < 2 then return set.prev end                     -- groupe dissous : on rend le mode d'avant
    return nil
  end
  if n >= 2 and cur == "fast" then return "group" end
  return nil
end
--- pierre de foyer : vaut-il le coup ? turns = quêtes finies qui se rendent près de l'auberge, gain (s), rien ici
function K.HearthWorth(turns, gain, nothingHere)
  return (tonumber(turns) or 0) >= 2 and (tonumber(gain) or 0) >= 300 and nothingHere and true or false
end

local openPage   -- (plus bas) la page « Pilote auto » de la fenêtre
-- ===================================================================== état
local S = {
  ctx = { main = "idle", label = K.LABEL.idle, flags = {}, at = -1 },
  dirtyCtx = true,
  fights = {},           -- débuts de combat (GetTime) des 5 dernières min
  progress = {},         -- [q] = GetTime() du dernier objectif avancé
  accepted = {},         -- [q] = GetTime() de la prise (session)
  progressAt = nil,      -- dernière quête avancée
  levelAt = nil, moveAt = nil,
  due = {}, timerAt = nil, token = 0,
  busyWatch = false, needs = {}, needsSig = "", lastIntent = nil,
  notice = nil, noticeAt = 0, announce = nil,
}
B._S = S

local function ov()
  local c = B.char
  if not c then return { keep = {}, errand = {} } end
  local t = epoch()
  -- une session = jusqu'à 2 h sans jeu : les choix du joueur (quête reprise, zone, mode) valent pour elle
  if type(c.ov) ~= "table" or t - (tonumber(c.seen) or 0) > 7200 then c.ov = { keep = {}, errand = {} } end
  c.ov.keep = type(c.ov.keep) == "table" and c.ov.keep or {}
  c.ov.errand = type(c.ov.errand) == "table" and c.ov.errand or {}
  c.seen = t
  return c.ov
end
B.Overrides = ov

-- ===================================================================== journal des décisions (≤ 30, sauvé)
B.LOG_MAX = 30
function B.Log(kind, text, why, undo)
  local c = B.char
  if not c then return nil end
  c.seq = (tonumber(c.seq) or 0) + 1
  local e = { id = c.seq, at = epoch(), k = kind, t = cut(text, 110), w = why and cut(why, 160) or nil, u = undo }
  c.log[#c.log + 1] = e
  while #c.log > B.LOG_MAX do table.remove(c.log, 1) end
  S.pageDirty = true
  return e
end
--- décisions, la plus récente d'abord
function B.Decisions(n)
  local out, log = {}, B.char and B.char.log or {}
  for i = #log, 1, -1 do
    if n and #out >= n then break end
    out[#out + 1] = log[i]
  end
  return out
end
function B.Find(id)
  for _, e in ipairs(B.char and B.char.log or {}) do if e.id == id then return e end end
  return nil
end

-- ===================================================================== contexte
local function questStopYd()
  local P = T.Planner
  local s = P and P.steps and P.cursor and P.steps[P.cursor]
  if not (s and s.x and s.map) then return nil, s end
  local Geo = T.Geo
  local m, x, y
  if Geo and Geo.Player then m, x, y = Geo.Player() end
  if not (m and x and Geo.Dist) then return nil, s end
  return Geo.Dist(m, x, y, s.map, s.x, s.y), s
end
local function bagsFree()
  local A = T.API
  if A and A.FreeSlots then
    local free, total = A.FreeSlots()
    return tonumber(free), tonumber(total)
  end
  return nil
end
local function durability()
  local Inv = mod("Inventory")
  if Inv and Inv.Durability then return Inv.Durability() end
  local f = G("GetInventoryItemDurability")
  if not f then return nil, 0 end
  local lo, broken = nil, 0
  for slot = 1, 18 do
    local cur, mx = try(f, slot)
    cur, mx = num(cur), num(mx)
    if cur and mx and mx > 0 then
      local p = floor(cur * 100 / mx)
      if not lo or p < lo then lo = p end
      if cur == 0 then broken = broken + 1 end
    end
  end
  return lo, broken
end
local function ammoInfo()
  if not (T.PlayerClass and T:PlayerClass() == "HUNTER") then return nil end
  local H = mod("Hunter")
  local a = H and H.AmmoInfo and try(H.AmmoInfo, H)
  if type(a) ~= "table" or not a.needs then return nil end
  local total = (tonumber(a.total) or 0)
  if (tonumber(a.equipped) or 0) > total then total = tonumber(a.equipped) end
  local rate = H.AmmoRate and try(H.AmmoRate)
  local word = a.kind == "arrow" and "flèches" or "balles"
  return total, (rate and rate > 0) and (total / rate * 60) or nil, word
end
local function logCap() return num(try(C("C_QuestLog", "GetMaxNumQuestsCanAccept"))) or 20 end
local function logCounts()
  if S.runLog then return S.runLog, S.runLogN, logCap() end
  local Xm = T.Export
  local log, n = nil, 0
  if Xm and Xm.LogSnapshot then log, n = Xm.LogSnapshot() end
  log, n = log or {}, tonumber(n) or 0
  if S.inRun then S.runLog, S.runLogN = log, n end         -- un relevé par passage (le journal ne change pas dedans)
  return log, n, logCap()
end
--- nombre de quêtes du journal : un appel du jeu (pas de relevé du journal)
local function logCount()
  local f = C("C_QuestLog", "GetNumQuestLogEntries") or G("GetNumQuestLogEntries")
  local _, nq = try(f)
  return num(nq) or 0
end
local function pvpDanger()
  if not sv(try(G("UnitIsPVP"), "player")) then return false end
  local f = C("C_PvP", "GetZonePVPInfo") or G("GetZonePVPInfo")
  local kind = f and sv(try(f))
  return (kind == "hostile" or kind == "contested" or kind == "combat") and true or false
end
local function partySpread()
  local n = groupSize()
  if n < 2 then return false, n end
  local CM = G("C_Map")
  local mine = CM and CM.GetBestMapForUnit and num(try(CM.GetBestMapForUnit, "player"))
  if not mine then return false, n end
  local mz, far = zoneOf(mine), 0
  for i = 1, 4 do
    local u = "party" .. i
    if sv(try(G("UnitExists"), u)) and sv(try(G("UnitIsConnected"), u)) ~= false then
      local m = num(try(CM.GetBestMapForUnit, u))
      if m and zoneOf(m) ~= mz then far = far + 1 end
    end
  end
  return far > 0, n
end

--- relit les besoins (sacs, durabilité, munitions, journal) : sur leurs événements seulement, ≤ 1 fois / 4 s
function B.ReadNeeds()
  S.needsAt = now()
  local free, total = bagsFree()
  local dur, broken = durability()
  local ammo, ammoMin, word = ammoInfo()
  local nlog, cap = logCount(), logCap()
  S.raw = { free = free, total = total, durMin = dur, broken = broken, ammo = ammo, ammoMin = ammoMin, ammoWord = word,
    log = nlog, logMax = cap }
  local f = S.ctx.flags
  if f then
    f.bags = free ~= nil and free <= 1
    f.ammo = ammo ~= nil and (ammo < 400 or (ammoMin and ammoMin < 30)) or false
    f.repair = dur ~= nil and (dur <= 25 or (broken or 0) > 0)
    f.logFull, f.log, f.logMax = nlog >= cap - 1, nlog, cap
  end
  S.ctx.raw = S.raw
  return S.raw
end
--- relit le contexte (appelé à chaque événement qui compte ; quelques appels de l'API, aucun calcul lourd)
function B.UpdateContext()
  S.dirtyCtx = false
  local ps = T.PlayerState and T:PlayerState() or {}   -- cache de Core (vidé par ses événements d’état)
  local t = now()
  local inst, ikind = try(G("IsInInstance"))
  inst = sv(inst)
  local map = num(try(C("C_Map", "GetBestMapForUnit"), "player"))
  local stopYd = questStopYd()
  local s = { dead = ps.dead, ghost = ps.ghost, taxi = ps.taxi, afk = ps.afk,
    instance = inst and (ikind == "party" or ikind == "raid" or ikind == "scenario") or false,
    city = map and (B.CITY[map] or B.CITY[zoneOf(map)]) and true or false }
  -- en route : on bouge depuis ≥ 20 s vers un arrêt à plus de 600 yd (ou un vol / bateau en tête du trajet)
  local R = T.Route
  if S.moveAt and t - S.moveAt >= 20 and T.Moving and T:Moving() then
    s.travel = (stopYd and stopYd > 600) or (R and R.firstKind and R.firstKind ~= "quest" and R.firstKind ~= "walk") or false
  end
  s.grind = K.Grinding(S.fights, t, S.progressAt, stopYd)
  local P = T.Planner
  s.quest = (P and P.steps and P.cursor and P.steps[P.cursor] and not (R and R.char and R.char.paused))
    or (S.progressAt and t - S.progressAt <= 300) or false
  local main = K.Main(s)
  local spread, gn = partySpread()
  local rested = num(try(G("GetXPExhaustion"))) or 0
  local raw = S.raw or {}
  raw.log, raw.logMax = logCount(), logCap()
  local f = {
    pvp = pvpDanger(), gather = spread, group = gn, levelUp = S.levelAt and t - S.levelAt <= 300 or false,
    bags = raw.free ~= nil and raw.free <= 1,
    ammo = raw.ammo ~= nil and (raw.ammo < 400 or (raw.ammoMin and raw.ammoMin < 30)) or false,
    repair = raw.durMin ~= nil and (raw.durMin <= 25 or (raw.broken or 0) > 0),
    rested = rested > 0, restedXP = rested, resting = sv(try(G("IsResting"))) and true or false,
    logFull = raw.log ~= nil and raw.log >= (raw.logMax or 20) - 1, log = raw.log, logMax = raw.logMax,
    combat = inCombat(), mounted = ps.mounted and true or false,
  }
  local label = K.LABEL[main] or main
  if main == "city" and map then label = "En ville : " .. mapName(map) end
  local ctx = S.ctx
  local changed = ctx.main ~= main
  ctx.main, ctx.label, ctx.flags, ctx.at, ctx.map, ctx.raw = main, label, f, t, map, raw
  if changed then
    S.ctxChangedAt = t
    if T.Fire then T:Fire("BRAIN_CONTEXT", main) end
  end
  return ctx, changed
end
--- le contexte du moment : { main, label, flags = { pvp, gather, group, levelUp, bags, ammo, repair, rested,
--- restedXP, resting, logFull, log, logMax, combat, mounted }, map, at } (même table, lecture seule)
function B.Context()
  if S.dirtyCtx then pcall(B.UpdateContext) end
  return S.ctx
end
function B.Is(kind) local c = B.Context() return c.main == kind or (c.flags and c.flags[kind]) and true or false end

-- ===================================================================== planification (un seul rappel regroupé)
local schedule
local function due(cat, delay)
  local t = now() + (delay or 0.3)
  if not S.due[cat] or t < S.due[cat] then S.due[cat] = t end   -- 1er événement = l'échéance (latence bornée)
  schedule()
end
B.Kick = due
schedule = function()
  local nxt
  for _, t in pairs(S.due) do if not nxt or t < nxt then nxt = t end end
  if not nxt then return end
  local t = now()
  if S.timerAt and S.timerAt <= nxt + 0.05 then return end          -- un rappel plus tôt attend déjà
  S.token = S.token + 1
  local tok = S.token
  S.timerAt = nxt
  T:After(max(0.05, nxt - t), function()
    if tok ~= S.token then return end
    S.timerAt = nil
    B.Run()
  end)
end

-- ===================================================================== suivi du jeu (sans ZoneWatch)
local function logIndex(q)
  local f = G("GetQuestLogIndexByID")
  local i = f and num(try(f, q))
  if i and i > 0 then return i end
  for k = 1, min(80, num(try(G("GetNumQuestLogEntries"))) or 0) do
    local _, _, _, header, _, _, _, id = try(G("GetQuestLogTitle"), k)
    if not header and tonumber(id) == q then return k end
  end
  return nil
end
function B.WatchAPI()
  local add, rem, typ = C("C_QuestLog", "AddQuestWatch"), C("C_QuestLog", "RemoveQuestWatch"), C("C_QuestLog", "GetQuestWatchType")
  if add and rem and typ then
    return { watched = function(q) return sv(try(typ, q)) ~= nil end, add = function(q) return try(add, q) end,
      remove = function(q) return try(rem, q) end }
  end
  local gAdd, gRem, gIs = G("AddQuestWatch"), G("RemoveQuestWatch"), G("IsQuestWatched")
  if gAdd and gRem and gIs then
    return { watched = function(q) local i = logIndex(q) return i and sv(try(gIs, i)) and true or false end,
      add = function(q) local i = logIndex(q) return i and try(gAdd, i) end,
      remove = function(q) local i = logIndex(q) return i and try(gRem, i) end }
  end
  return nil
end
--- (ZONEFIX) le suivi auto par zone tient le suivi du jeu d'après le trajet : une quête en pause en sort d'elle-même
local function zoneWatchOn()
  local Z = zp()
  return (Z and type(Z.AutoWatchOn) == "function" and try(Z.AutoWatchOn, Z)) and true or false
end
--- suivie à la main par le joueur (suivi auto par zone : watchManual[q] = true) ?
local function watchedByHand(q)
  local Z = zp()
  local wm = Z and Z.char and Z.char.watchManual
  return type(wm) == "table" and wm[q] == true and zoneWatchOn() or false
end
local function setWatch(q, on)
  -- (4.0.5) le suivi du jeu appartient au joueur : le pilote n'y ajoute ni n'y retire plus rien
  if not B.TOUCH_WATCH then return false end
  local api = B.WatchAPI()
  if not api or inCombat() then return false end
  S.busyWatch = true
  local ok = pcall(function()
    if on and not api.watched(q) then
      if T.ExpectQuestWatch then T:ExpectQuestWatch(q, true) end   -- (V5H) l'événement renvoyé est le nôtre
      api.add(q)
    elseif not on and api.watched(q) then
      if T.ExpectQuestWatch then T:ExpectQuestWatch(q, false) end
      api.remove(q)
    end
  end)
  S.busyWatch = false
  return ok
end

-- ===================================================================== quêtes en pause
local function parked() return B.char and B.char.parked or {} end
--- la pause d'une quête (ou nil) : { k = raison, t = fait du jeu, at, n = titre, lv }
function B.Parked(q)
  if not (B._active and B.db and B.db.park ~= false) then return nil end
  q = tonumber(q)
  return q and parked()[q] or nil
end
--- quêtes en pause, les raisons sûres d'abord puis par titre : { {q, k, t, at, n, lv, strong, why, advice} }
function B.ParkedList()
  local out = {}
  for q, p in pairs(parked()) do
    out[#out + 1] = { q = q, k = p.k, t = p.t, at = p.at, n = p.n, lv = p.lv, strong = K.STRONG[p.k] or false,
      why = K.WHY[p.k] or p.k, advice = K.ADVICE[p.k] }
  end
  tsort(out, function(a, b)
    if a.strong ~= b.strong then return a.strong end
    if a.k ~= b.k then return a.k < b.k end
    return tostring(a.n) < tostring(b.n)
  end)
  return out
end
local function bump()
  local Z = zp()
  if Z and Z.Bump then pcall(Z.Bump, Z) return end
  local P = T.Planner
  if P and P.Dirty then pcall(P.Dirty, P, true) end
end
local function questPoint(q)
  local CQ = G("C_QuestLog")
  if CQ and CQ.GetNextWaypoint then
    local m, x, y = try(CQ.GetNextWaypoint, q)
    m, x, y = num(m), num(x), num(y)
    if m and x and y then return { map = m, x = x * 100, y = y * 100 } end
  end
  return nil
end
local function park(q, c, quiet)
  local ps = parked()
  local p = { k = c.key, t = cut(c.text, 90), at = epoch(), n = cut(c.title or ("quête " .. q), 48), lv = tonumber(c.level),
    p = c.pt or questPoint(q), x = c.xph, z = c.zone, l = myLevel(), g = groupSize() }
  if not zoneWatchOn() then
    local api = B.WatchAPI()
    if api and api.watched(q) and setWatch(q, false) then p.w = true end
  end
  ps[q] = p
  if not quiet then
    B.Log("park", fmt("« %s » en pause : %s", cut(p.n, 40), p.t), K.ADVICE[p.k], { kind = "park", q = q })
  end
  return p
end
local function unpark(q, why, keep, quiet)
  local ps = parked()
  local p = ps[q]
  if not p then return nil end
  ps[q] = nil
  local inLog = logCounts()[q] ~= nil
  if p.w and inLog and not zoneWatchOn() then setWatch(q, true) end
  if keep then ov().keep[q] = true end
  if not quiet and inLog then
    B.Log("unpark", fmt("« %s » reprise : %s", cut(p.n, 40), why or "la raison a disparu"), nil, nil)
  end
  return p
end
B._Park, B._Unpark = park, unpark

-- candidats : les faits du module « Nettoyer mon journal » (quêtes hors trajet), + grises du trajet, + détours ruineux
local function abandonFacts()
  local Ab = T.Abandon
  if not Ab then return nil end
  -- le module « Nettoyer mon journal » se recalcule lui-même sur les mêmes événements (et 0,5 s après chaque trajet) :
  -- on lit son résultat ; calculé ici seulement s'il n'a jamais tourné (module coupé)
  if Ab.cleanup and Ab.Visible then return Ab.Visible(Ab.cleanup) end
  if Ab.CleanupNow then return (Ab.CleanupNow()) end
  return nil
end
local function routeQuests()
  local P = T.Planner
  local set, order = {}, {}
  local steps, cur = P and P.steps, P and P.cursor
  if not (steps and cur) then return set, order, steps end
  for i = cur, #steps do
    local s = steps[i]
    local q = tonumber(s.q)
    if q and not s._done and not s._other then
      if not set[q] then set[q] = {} order[#order + 1] = q end
      local l = set[q]
      l[#l + 1] = i
    end
  end
  return set, order, steps
end
local function legSec(a, b)
  local P = T.Planner
  if P and P.Leg then return tonumber((try(P.Leg, a, b))) end
  return nil
end
--- secondes que coûtent les arrêts de cette quête dans le trajet (insertion entre les arrêts voisins des autres)
local function detourOf(q, idx, steps)
  local total, pts = 0, nil
  for _, i in ipairs(idx) do
    local s = steps[i]
    if s.x then
      pts = pts or { map = s.map, x = s.x, y = s.y }
      local a, b
      for j = i - 1, 1, -1 do local o = steps[j] if not o._done and not o._other and o.x and tonumber(o.q) ~= q then a = o break end end
      for j = i + 1, #steps do local o = steps[j] if not o._done and not o._other and o.x and tonumber(o.q) ~= q then b = o break end end
      if a and b then
        local ab, as, sb = legSec(a, b), legSec(a, s), legSec(s, b)
        if not (ab and as and sb) then return nil end
        total = total + max(0, as + sb - ab)
      elseif a or b then
        local d = legSec(a or s, b or s)
        if not d then return nil end
        total = total + d
      end
    end
  end
  return total, pts
end
local function measuredXPH()
  local St = T.Stats
  if not St then return nil end
  local v = St.RecentXPPerHour and try(St.RecentXPPerHour, St)
  if not v and St.XPPerHour then v = try(St.XPPerHour, St, "questing") end
  return tonumber(v)
end
local function realXP(q)
  local f = G("GetQuestLogRewardXP")
  if not f then return nil end
  local x = num(try(f, q))
  return (x and x > 0) and x or nil
end
local function nearRoute(pt, steps, cur)
  local Geo = T.Geo
  if not (pt and pt.map and pt.x and Geo and Geo.Dist and steps) then return false end
  for i = cur or 1, #steps do
    local s = steps[i]
    if s.x and not s._done and not s._other then
      local d = Geo.Dist(pt.map, pt.x, pt.y, s.map, s.x, s.y)
      if d and d <= K.NEAR_YD then return true end
    end
  end
  return false
end

--- un passage « quêtes en pause » (hors combat) → nombre de changements
function B.ParkPass()
  local ps = parked()
  local log = logCounts()
  local keep = ov().keep
  local t = now()
  local changes, parkedNow, reasons, needBump, dungeonReady = 0, 0, {}, false, nil
  -- quêtes parties du journal (rendues, abandonnées) : oubliées sans un mot
  for q in pairs(ps) do if not log[q] then ps[q] = nil end end
  for q in pairs(keep) do if not log[q] then keep[q] = nil end end
  if not opt("park") then
    for q in pairs(ps) do unpark(q, "règle coupée") changes = changes + 1 end
    if changes > 0 then bump() end
    return changes
  end
  local cand = {}
  local Qm = mod("Quests")
  local timers = (Qm and Qm.Timers and Qm.lastList and try(Qm.Timers, Qm.lastList)) or {}
  local function protected(q, lq)
    if keep[q] or not lq then return true end
    if lq.done == 1 then return true end                                    -- finie : on la rend
    if type(timers) == "table" and timers[q] then return true end           -- chrono en cours : jamais
    if S.accepted[q] and t - S.accepted[q] < K.FRESH_S then return true end -- prise à l'instant : voulue
    if S.progress[q] and t - S.progress[q] < K.PROGRESS_S then return true end   -- tu y avances
    if watchedByHand(q) then return true end                                -- tu la suis à la main
    local Cm = T.Compass                                                    -- la boussole la vise sur ton clic
    local tg = Cm and Cm.pinned and Cm.state and Cm.state.target
    if type(tg) == "table" and tonumber(tg.questID) == q then return true end
    local cc = Cm and Cm.char and Cm.char.custom
    if type(cc) == "table" and tonumber(cc.questID) == q then return true end
    return false
  end
  local cl = abandonFacts()
  for _, gr in ipairs(cl and cl.groups or {}) do
    for _, it in ipairs(gr.items or {}) do
      local q = it.q and tonumber(it.q.id)
      local f = it.facts and it.facts[1]
      if q and f and K.PARK_KEYS[it.key] and not protected(q, log[q]) then
        cand[q] = { key = it.key, text = f.text, title = it.q.title, level = it.q.level }
      end
    end
  end
  -- grises du trajet (le module Journal ne juge jamais un arrêt du trajet) : la couleur du jeu, pas encore finies
  local rset, rorder, steps = routeQuests()
  local Q = mod("Quests")
  local byId = {}
  for _, q in ipairs(Q and Q.lastList or {}) do byId[q.id] = q end
  local Ab = T.Abandon
  local greys = {}
  local gkey = myLevel() .. ":" .. groupSize()
  S.greyJudged = S.greyJudged or {}
  for _, q in ipairs(rorder) do
    local e = byId[q]
    if e and e.grey and not e.complete and not cand[q] and not protected(q, log[q]) and S.greyJudged[q] ~= gkey then
      greys[#greys + 1] = e
      S.greyJudged[q] = gkey                               -- une seule fois par niveau et taille de groupe
    end
  end
  if #greys > 0 and Ab and Ab.Gather and Ab.Context and Ab.Core and Ab.Core.Facts then
    local okC, actx = pcall(Ab.Context, greys)
    local okG, _, fin = false, nil, nil
    if okC then okG, _, fin = pcall(Ab.Gather, greys, actx) end
    for _, f in ipairs(okG and fin or {}) do
      f.planned = false                                    -- jugée comme si elle n'était pas sur le trajet
      local facts = Ab.Core.Facts(f, actx)
      if facts[1] and facts[1].key == "grey" then cand[f.id] = { key = "grey", text = facts[1].text, title = f.title, level = f.level } end
    end
  end
  -- détours ruineux (XP réelle, détour du trajet, XP/h mesurée) : seulement si la règle est allumée et mesurée
  local xph = opt("lowValue") and measuredXPH() or nil
  local hz = zoneOf(num(try(C("C_Map", "GetBestMapForUnit"), "player")))
  if xph and steps then
    for _, q in ipairs(rorder) do
      if not cand[q] and not protected(q, log[q]) then
        local xp = realXP(q)
        local d, pt = detourOf(q, rset[q], steps)
        local low, text = K.LowValue(xp, d, xph)
        if low then cand[q] = { key = "low", text = text, title = (log[q] and log[q].t) or (byId[q] and byId[q].title), level = log[q] and log[q].lv, pt = pt, xph = xph, zone = hz } end
      end
    end
  end
  -- appliquer : reprises d'abord (raisons disparues), puis les nouvelles pauses
  local cur = T.Planner and T.Planner.cursor
  for q, p in pairs(ps) do
    local c = cand[q]
    local why
    if keep[q] then why = "tu l'as reprise"
    elseif watchedByHand(q) then why = "tu la suis à la main" keep[q] = true
    elseif log[q] and log[q].done == 1 then why = "finie : à rendre"
    elseif S.progress[q] and t - S.progress[q] < K.PROGRESS_S then why = "tu y avances"
    elseif p.k == "low" then
      -- détour : plus de candidat possible (elle n'est plus sur le trajet) → revient sur un fait nouveau seulement
      if p.l ~= myLevel() then why = "niveau " .. myLevel()
      elseif p.z and hz and p.z ~= hz then why = "nouvelle zone"
      elseif not xph or (p.x and abs(xph - p.x) > 0.3 * p.x) then why = "ton rythme a changé"
      elseif nearRoute(p.p, steps, cur) then why = "le trajet passe à côté" end
    elseif not c then
      local Tr = mod("Tracker")
      local mates = Tr and Tr.GroupOn and try(Tr.GroupOn, q, Tr.SquadMates and try(Tr.SquadMates) or {}) or nil
      if type(mates) == "table" and #mates > 0 then why = "ton groupe l'a aussi"
      elseif p.k == "hard" then
        why = (groupSize() > (tonumber(p.g) or 1) and fmt("vous êtes %d maintenant", groupSize()))
          or (myLevel() > (tonumber(p.l) or 0) and ("niveau " .. myLevel())) or "plus trop dure pour vous"
      else why = (p.k == "far" and "elle n'est plus loin") or "la raison a disparu" end
      if p.k == "hard" and groupSize() >= 4 and T.Dungeon and T.Dungeon.QuestDungeon then
        local dg = try(T.Dungeon.QuestDungeon, q)
        if type(dg) == "table" and dg.n then dungeonReady = dungeonReady or dg end
      end
    elseif p.k == "far" and nearRoute(p.p, steps, cur) then why = "le trajet passe à côté" end
    if why then
      local kept = keep[q] and true or false
      unpark(q, why, kept)
      if not kept then                                     -- pas de va-et-vient (trajet à 15 min pile, groupe qui bouge)
        S.unparkedAt = S.unparkedAt or {}
        S.unparkedAt[q] = { k = p.k, t = t }
      end
      changes, needBump = changes + 1, true
    else
      cand[q] = nil                                        -- déjà en pause pour cette raison
    end
  end
  local calm = S.unparkedAt or {}
  for q, c in pairs(cand) do
    local u = calm[q]
    local recent = u and u.k == c.key and t - u.t < K.CALM_S
    if not ps[q] and not recent and not (c.key == "far" and nearRoute(c.pt or questPoint(q), steps, cur)) then
      park(q, c)
      changes, parkedNow = changes + 1, parkedNow + 1
      if rset[q] then needBump = true end                 -- sur le trajet : il se refait sans elle
      reasons[#reasons + 1] = K.SHORT[c.key] or c.key
    end
  end
  if needBump then bump() end
  if changes > 0 and T.Fire then T:Fire("BRAIN_PARKED") end
  if dungeonReady then
    local d = dungeonReady
    B.Log("unpark", fmt("Groupe de %d : %s redevient possible", groupSize(), tostring(d.n)), "Ses quêtes reprennent ; la fenêtre « Prêt ? » vérifie le groupe.", nil)
    B.Announce("dungeon", fmt("Groupe de %d : %s possible", groupSize(), cut(d.n, 26)), "clic : prêt pour le donjon ?", function()
      local Dn = T.Dungeon
      if Dn and Dn.SetPlanned then try(Dn.SetPlanned, d.key or d.n) end
      if Dn and Dn.ToggleChecklist then try(Dn.ToggleChecklist, Dn, true) else openPage() end
    end)
  end
  if parkedNow > 0 then
    local seen, uniq = {}, {}
    for _, r in ipairs(reasons) do if not seen[r] then seen[r] = true uniq[#uniq + 1] = r end end
    B.Announce("park", parkedNow == 1 and "Pilote auto : 1 quête en pause" or fmt("Pilote auto : %d quêtes en pause", parkedNow),
      cut(tconcat(uniq, ", "), 60) .. " · clic : pourquoi")
  end
  return changes
end

--- « Reprendre » (clic du joueur) : la quête revient au suivi et au trajet, plus touchée de la session
function B.Resume(q)
  q = tonumber(q)
  if not (q and parked()[q]) then return false end
  unpark(q, "tu l'as reprise", true)
  bump()
  if T.Fire then T:Fire("BRAIN_PARKED") end
  S.pageDirty = true
  B.UpdateNotice()
  return true
end

-- ===================================================================== zone de quêtes (quand tu n'en as pas choisi)
local function countKeys(t) local n = 0 for _ in pairs(t or {}) do n = n + 1 end return n end
local function setZone(Z, map)
  if Z.PickZone then return try(Z.PickZone, Z, map, "brain") end      -- (ZONEFIX) une zone d'un coup
  local ch = Z.char
  ch.mode, ch.zones = "zones", { [map] = true }
  if T.Export and T.Export.Dirty then pcall(T.Export.Dirty, T.Export) end
  if Z.Bump then Z:Bump() end
  return true
end
local function zoneAuto(Z)
  if Z.SetMode then pcall(Z.SetMode, Z, "auto") return end
  Z.char.mode, Z.char.zones = "auto", {}
  if Z.Bump then Z:Bump() end
end
--- quêtes encore à faire sur le trajet (toutes zones de travail), pas en pause → nombre, zones du trajet
local function routeLeft()
  local P = T.Planner
  local seen, n, zones = {}, 0, {}
  local steps, cur = P and P.steps, P and P.cursor
  for i = cur or 1, steps and #steps or 0 do
    local s = steps[i]
    local q = tonumber(s.q)
    if q and not s._done and not s._other and not seen[q] then
      seen[q] = true
      n = n + 1
      if s.map then zones[zoneOf(s.map)] = true end
    end
  end
  return n, zones
end
--- zone de quêtes : seulement quand le trajet n'a plus rien (ta zone et les voisines prises en renfort sont vides) et
--- que tu n'as pas choisi de zone ; la meilleure zone du continent d'après QuestieDB (quêtes à ton niveau), proche d'abord
function B.ZonePass()
  local Z = zp()
  if not (Z and Z.char and opt("zone")) then return 0 end
  local ch, c, o = Z.char, B.char, ov()
  local pick = c.pick
  if type(pick) == "table" then
    -- la zone posée par le pilote a été changée à la main : le choix du joueur gagne (session)
    if ch.mode ~= "zones" or not (ch.zones and ch.zones[pick.map]) or countKeys(ch.zones) ~= 1 then
      c.pick, o.zone = false, true
      B.Log("zone", "Zone changée à la main : le pilote ne la choisit plus (session)", nil, nil)
      return 0
    end
  end
  if o.zone or (ch.mode ~= "auto" and type(pick) ~= "table") then return 0 end
  local P = T.Planner
  -- pas encore de trajet, ou un calcul en cours : on attend (PLAN_BUILT nous rappelle)
  if not (P and P.route) or (P.Busy and P.IsEnabled and P:IsEnabled() and P:Busy()) then return 0 end
  local left, rz = routeLeft()
  if left >= K.MIN_HERE then return 0 end
  local counts = Z.counts
  if not (counts and counts.n) and Z.CountZones then
    pcall(Z.CountZones, Z)                                -- comptage découpé (ZonePlan) : on repassera à sa fin
    counts = Z.counts
  end
  if not (counts and counts.n) then due("zone", 15) return 0 end
  local Geo = T.Geo
  local m, x, y
  if Geo and Geo.Player then m, x, y = Geo.Player() end
  if not m then return 0 end
  local hz = zoneOf(m)
  local A = Z.Allowed and try(Z.Allowed, Z)
  local inArea = type(A) == "table" and type(A.maps) == "table" and A.maps or {}
  local cont = Z.Cont and Z.Cont(hz)
  local here0 = (x and y) and { map = m, x = x, y = y } or nil
  local cands = {}
  for mm, n in pairs(counts.n) do
    mm = tonumber(mm)
    if mm and not inArea[mm] and not rz[mm] and mm ~= hz and not B.CITY[mm] and zoneOf(mm) == mm
        and (not cont or Z.Cont(mm) == cont) and (tonumber(n) or 0) >= K.MIN_PICK then
      cands[#cands + 1] = { map = mm, n = n, sec = here0 and legSec(here0, { map = mm, x = 50, y = 50 }) or nil }
    end
  end
  local best = K.ZonePick(left, cands)
  if not best then return 0 end
  local prev = { mode = ch.mode, zones = {} }
  for k2, v in pairs(ch.zones or {}) do prev.zones[k2] = v end
  setZone(Z, best.map)
  c.pick = { map = best.map, at = epoch(), prev = type(pick) == "table" and pick.prev or prev }
  local name = mapName(best.map)
  local why = fmt("Plus rien à faire sur ton trajet ; %s : %d quêtes à ton niveau%s.", name, best.n,
    best.sec and fmt(" (≈ %d min de trajet)", floor(best.sec / 60 + 0.5)) or "")
  B.Log("zone", "Zone choisie : " .. name, why, { kind = "zone" })
  B.Announce("zone", "Zone choisie : " .. cut(name, 30), fmt("%d quêtes à ton niveau · clic : pourquoi", best.n))
  return 1
end

-- ===================================================================== mode du trajet (ZONEFIX : Z.STYLES)
local function styleIndex(Z, id)
  for i, e in ipairs(Z.STYLES or {}) do if e.id == id then return i end end
  return nil
end
function B.StylePass()
  local Z = zp()
  if not (Z and type(Z.STYLES) == "table" and Z.SetStyle and Z.StyleIndex and opt("style")) then return 0 end
  local c, o = B.char, ov()
  local curI = try(Z.StyleIndex, Z)
  local cur = curI and Z.STYLES[curI] and Z.STYLES[curI].id
  local set = type(c.style) == "table" and c.style or nil
  if set and cur ~= set.set then c.style, o.style = false, true return 0 end   -- changé à la main : il gagne
  if o.style and not set then return 0 end
  if not set and Z.char and Z.char.styleBy == "player" then return 0 end     -- (ZONEFIX) choix explicite gardé
  local n = groupSize()
  local want = K.StyleFor(n, cur, set)
  if not want then return 0 end
  local wi = styleIndex(Z, want)
  if not wi then return 0 end
  try(Z.SetStyle, Z, wi, "brain")
  local name = Z.StyleName and try(Z.StyleName, Z, wi) or want
  if set then
    c.style = false
    B.Log("style", "Trajet : retour au mode « " .. tostring(name) .. " »", "Le groupe est dissous.", nil)
  else
    c.style = { set = want, prev = cur }
    B.Log("style", "Trajet en mode « " .. tostring(name) .. " »", fmt("Vous êtes %d : les quêtes que le groupe a aussi passent d'abord.", n), { kind = "style" })
    B.Announce("style", "Trajet en mode « " .. tostring(name) .. " »", fmt("vous êtes %d · clic : pourquoi", n))
  end
  return 1
end

-- ===================================================================== courses (réparer, vendre, munitions, maître)
local function trainNeed()
  local c = B.char
  local lvl = myLevel()
  local tr = c and tonumber(c.train)
  if not tr or tr > lvl then return nil end
  local Er = mod("Errands")
  local nt = Er and Er.char and Er.char.nextTrain
  local known = type(nt) == "table" and tonumber(nt.level) and nt.level <= lvl and tonumber(nt.cost)
  if not known and lvl % 2 == 1 then return nil end      -- sans prix lu chez le maître : niveaux pairs (sorts Classic)
  return lvl, known and nt.cost or nil
end
function B.Needs()
  local raw = S.raw or {}
  local tr, cost = trainNeed()
  return K.Needs({ free = raw.free, durMin = raw.durMin, broken = raw.broken, ammo = raw.ammo, ammoMin = raw.ammoMin,
    ammoWord = raw.ammoWord, train = tr, trainCost = cost })
end
-- PNJ connus d'une carte : QuestieDB / déjà vus (module Détection) — positions en % comme le trajet
local function services(kind, map)
  local D = T.Detect
  local out = {}
  if not D then return out end
  local Geo = T.Geo
  local m = Geo and Geo.Player and Geo.Player()
  if map == nil or map == m then
    for _, e in ipairs((D.Services and try(D.Services, kind)) or {}) do
      if e.mapID and e.x and e.y and (e.kind == kind or (kind == "vendor" and e.kind == "repair")) then
        out[#out + 1] = { map = e.mapID, x = e.x * 100, y = e.y * 100, name = e.name, dist = e.dist }
      end
    end
    return out
  end
  local L = D.db and type(D.db.learned) == "table" and D.db.learned[tostring(map)]
  for _, e in ipairs(type(L) == "table" and L or {}) do
    if type(e) == "table" and tonumber(e.x) and (e.k == kind or (kind == "vendor" and e.k == "repair")) then
      out[#out + 1] = { map = map, x = e.x * 100, y = e.y * 100, name = e.n }
    end
  end
  return out
end
B.Services = services
-- une ville (capitale) sur le trajet : la 1re entrée du trajet (arrêt ou arrivée d'un vol) dans une capitale
local function cityOnRoute()
  local R = T.Route
  if not (R and R.IsEnabled and R:IsEnabled() and R.Stops) then return nil end
  local okS, st = pcall(R.Stops, R, 64)
  if not okS or type(st) ~= "table" then return nil end
  for i, e in ipairs(st) do
    local p = e.place
    if p and p.map and B.CITY[p.map] then return { map = p.map, name = mapName(p.map), i = i, at = e.at } end
  end
  return nil
end
local function stops() local c = B.char return c and c.stops or {} end
function B.ErrandStops() return stops() end
local function addStop(key, kind, label, p, why)
  local list = stops()
  local t = epoch()
  for _, s in ipairs(list) do
    if s.key == key and (not s.done or (s.doneAt and t - s.doneAt < 600)) then return nil end   -- déjà là / atteint à l'instant
  end
  for i = #list, 1, -1 do if list[i].done then table.remove(list, i) end end
  local e = { key = key, k = kind == "trainer" and "train" or "vendor", label = cut(label, 60), map = p.map, x = p.x, y = p.y,
    needs = why, at = epoch() }
  list[#list + 1] = e
  while #list > 3 do table.remove(list, 1) end
  S.stopSig = (S.stopSig or 0) + 1
  return e
end
local function dropStop(key, silent)
  for _, s in ipairs(stops()) do
    if s.key == key and not s.done then
      s.done = true
      S.stopSig = (S.stopSig or 0) + 1
      if not silent then B.Log("errand", "Course retirée du trajet : " .. tostring(s.label), nil, nil) end
    end
  end
end
B._DropStop = dropStop
local function nearestOf(list, here)
  local Geo = T.Geo
  local best, bd
  for _, e in ipairs(list) do
    local d = e.dist or (here and Geo and Geo.Dist and Geo.Dist(here.map, here.x, here.y, e.map, e.x, e.y))
    if d and (not bd or d < bd) then best, bd = e, d end
  end
  return best, bd
end
function B.ErrandPass()
  if not opt("errands") then
    for _, s in ipairs(stops()) do if not s.done then dropStop(s.key, true) end end
    S.city, S.errandText = nil, nil
    return 0
  end
  local needs = B.Needs()
  local o = ov()
  local sig = K.NeedsSig(needs)
  local changed = sig ~= S.needsSig
  S.needs, S.needsSig = needs, sig
  -- besoin réglé (réparé, vendu, rechargé) : son arrêt quitte le trajet
  local want = {}
  for _, n in ipairs(needs) do want[n.kind] = true end
  for _, s in ipairs(stops()) do
    if not s.done and not want[s.key:match("^errand:(.+)$") or ""] then dropStop(s.key, true) end
  end
  if #needs == 0 then S.city, S.errandText = nil, nil return changed and 1 or 0 end
  local Geo = T.Geo
  local m, x, y
  if Geo and Geo.Player then m, x, y = Geo.Player() end
  local here = (m and x) and { map = m, x = x, y = y } or nil
  local city = cityOnRoute()
  S.city = city
  S.errandText = K.NeedsShort(needs)
  local added = 0
  -- une course par genre de PNJ (réparer couvre aussi « vendre » : le réparateur achète)
  local kinds = {}
  for _, n in ipairs(needs) do
    local k2 = n.kind
    if k2 == "vendor" and want.repair then k2 = "repair" end
    if not kinds[k2] then kinds[k2] = { urgent = false, words = {} } kinds[#kinds + 1] = k2 end
    kinds[k2].urgent = kinds[k2].urgent or n.urgent
    kinds[k2].words[#kinds[k2].words + 1] = n.short
  end
  for _, k2 in ipairs(kinds) do
    local info = kinds[k2]
    local key = "errand:" .. k2
    if not o.errand[k2] and k2 ~= "trainer" then
      local spot, d
      if info.urgent and here then
        spot, d = nearestOf(services(k2, m), here)
        if d and d > 900 then spot = nil end            -- urgent : un PNJ connu à ≤ 900 yd (≈ 4 min à pied)
      elseif city then
        spot = nearestOf(services(k2, city.map), here)  -- la capitale du trajet : un PNJ connu là-bas
      end
      if spot then
        local e = addStop(key, k2, K.StopLabel(info.words, spot.name and cut(spot.name, 24)), spot, info.words)
        if e then
          added = added + 1
          B.Log("errand", "Course ajoutée au trajet : " .. e.label,
            info.urgent and "Urgent : un marchand connu tout près." or ("Ton trajet passe par " .. tostring(city and city.name) .. "."),
            { kind = "errand", key = key, k2 = k2 })
        end
      end
    end
  end
  if added > 0 then
    local P = T.Planner
    if P and P.steps and P.Evaluate then pcall(P.Evaluate, P) P._sig = nil if P.Render then pcall(P.Render, P) end end
    B.Announce("errand", "Courses ajoutées au trajet", cut(S.errandText, 40) .. " · clic : pourquoi")
  end
  return (changed or added > 0) and 1 or 0
end

-- contrat du Planner (comme Finder.Inject / Finder.StopDone) : les courses insérées au plus court dans le trajet
function B.Inject(steps, cursor)
  if type(steps) ~= "table" or not (B._active and B.char) then return false end
  if T.FocusMode and T:FocusMode() then return false end   -- (4.0.4) session métier : pas d'arrêt du pilote
  local sig = S.stopSig or 0
  if steps.__bsig == sig then return false end
  steps.__bsig = sig
  local have = {}
  for _, s in ipairs(steps) do if s.brain then have[s.brain] = true end end
  local P = T.Planner
  local E = P and P.Engine
  local added = false
  for _, st in ipairs(stops()) do
    if not st.done and not have[st.key] and st.map and st.x then
      local s = { k = st.k or "vendor", label = st.label, map = st.map, x = st.x, y = st.y, brain = st.key }
      local i0 = max(1, min(tonumber(cursor) or 1, #steps + 1))
      local Geo = T.Geo
      local start
      if Geo and Geo.Player then local m, x, y = Geo.Player() if m and x then start = { map = m, x = x, y = y } end end
      local urgent = false
      for _, n in ipairs(S.needs or {}) do if n.urgent and ("errand:" .. n.kind) == st.key then urgent = true end end
      if urgent then table.insert(steps, i0, s)
      elseif E and E.Insert and P.Leg then
        if not pcall(E.Insert, steps, { s }, i0, 20, start or steps[i0 - 1], P.Leg) then table.insert(steps, i0, s) end
      else table.insert(steps, i0, s) end
      added = true
    end
  end
  return added
end
--- arrêt « course » fait ? (arrivé à ≤ 40 yd, besoin réglé, ou retiré)
function B.StopDone(s)
  for _, st in ipairs(stops()) do
    if st.key == s.brain then
      if st.done then return true end
      local Geo = T.Geo
      local m, x, y
      if Geo and Geo.Player then m, x, y = Geo.Player() end
      local d = (m and x and Geo.Dist) and Geo.Dist(m, x, y, st.map, st.x, st.y)
      if d and d < 40 then st.done, st.doneAt = true, epoch() S.stopSig = (S.stopSig or 0) + 1 return true end
      return false
    end
  end
  return true
end

-- ===================================================================== pierre de foyer
function B.HearthPass()
  S.hearth = nil
  if not opt("hearth") then return 0 end
  local R = T.Route
  if not (R and R.HearthReady and R.BindPlace and R.HearthReady()) then return 0 end
  if R.firstKind == "hs" then return 0 end                -- le trajet la prend déjà (Route)
  local bind = try(R.BindPlace)
  if not (type(bind) == "table" and bind.map and bind.x) then return 0 end
  local Geo = T.Geo
  local m, x, y
  if Geo and Geo.Player then m, x, y = Geo.Player() end
  if not (m and x and Geo.Dist) then return 0 end
  -- rien à faire ici : plus d'arrêt du trajet autre que ces remises
  local P = T.Planner
  local log = logCounts()
  local turns, xp, nothing = 0, 0, true
  local near = {}
  for q, lq in pairs(log) do
    if lq.done == 1 then
      local wp = questPoint(q)
      local d = wp and Geo.Dist(wp.map, wp.x, wp.y, bind.map, bind.x, bind.y)
      if d and d <= 400 then turns, xp, near[q] = turns + 1, xp + (realXP(q) or 0), true end
    end
  end
  if turns < 2 then return 0 end
  for i = (P and P.cursor) or 1, P and P.steps and #P.steps or 0 do
    local s = P.steps[i]
    if not s._done and not s._other and not (s.k == "turn" and near[tonumber(s.q) or -1]) and (s.q or s.brain) then nothing = false break end
  end
  local walk = legSec({ map = m, x = x, y = y }, bind)
  local D = T.Data and T.Data.Travel
  local hs = ((D and D.hearthCast) or 10) + ((D and D.hearthLoad) or 5)
  local gain = walk and (walk - hs) or nil
  if not K.HearthWorth(turns, gain, nothing) then return 0 end
  local name = cut(bind.name or "l'auberge", 26)
  S.hearth = { name = name, turns = turns, gain = gain, xp = xp }
  local key = fmt("%s:%d", name, turns)
  if S.hearthKey ~= key then
    S.hearthKey = key
    B.Log("hearth", fmt("Pierre de foyer conseillée : %s à rendre à %s", plural(turns, "quête", "quêtes"), name),
      fmt("Plus rien d'autre ici ; ≈ %d min gagnées sur le trajet à pied ou en vol.", floor(gain / 60 + 0.5)), nil)
  end
  return 1
end

-- ===================================================================== suivre le groupe (GROUPJOIN : Group.Intent())
function B.FollowPass()
  if not opt("follow") then return 0 end
  local Gm = mod("Group")
  local f = Gm and Gm.Intent
  if type(f) ~= "function" or not Gm._active then return 0 end
  local it = try(f, Gm)
  if type(it) ~= "table" or (tonumber(it.conf) or 0) < 0.6 then return 0 end
  local sig = fmt("%s:%s:%s", tostring(it.kind), tostring(it.map or it.dungeon or it.questID or ""), tostring(it.at or ""))
  if sig == S.lastIntent then return 0 end
  S.lastIntent = sig
  local who = it.by and (" (" .. cut(it.by, 16) .. ")") or ""
  local label = cut(it.label or "", 40)
  local Z = zp()
  if it.kind == "zone" and tonumber(it.map) and Z and Z.char then
    local ch = Z.char
    local prev = { mode = ch.mode, zones = {} }
    for k2, v in pairs(ch.zones or {}) do prev.zones[k2] = v end
    setZone(Z, tonumber(it.map))
    B.char.pick = { map = tonumber(it.map), at = epoch(), prev = prev, group = true }
    B.Log("follow", "Suivre le groupe : zone " .. mapName(it.map) .. who, label ~= "" and label or nil, { kind = "zone" })
    B.Announce("follow", "Le groupe va : " .. cut(mapName(it.map), 28), "zone de quêtes suivie · clic : pourquoi")
  elseif it.kind == "dungeon" and it.dungeon and T.Dungeon and T.Dungeon.SetPlanned then
    local d = try(T.Dungeon.SetPlanned, it.dungeon)
    if d then
      B.Log("follow", "Suivre le groupe : donjon " .. tostring(d.n or it.dungeon) .. who, label ~= "" and label or nil, nil)
      B.Announce("follow", "Le groupe va : " .. cut(d.n or tostring(it.dungeon), 28), "donjon prévu · clic : pourquoi")
    end
  elseif (it.kind == "meet" or it.kind == "quest") and T.Compass then
    local Cm = T.Compass
    local p = it.point
    local okT
    if it.kind == "meet" and type(p) == "table" and p.map and p.x and Cm.SetTarget then
      okT = try(Cm.SetTarget, Cm, { mapID = p.map, x = p.x / 100, y = p.y / 100, label = cut(it.label or "Point de rencontre", 40), source = "group" })
    elseif it.kind == "quest" and tonumber(it.questID) and Cm.SetQuestTarget then
      okT = try(Cm.SetQuestTarget, tonumber(it.questID))
    end
    if okT then
      B.Log("follow", "Suivre le groupe : la boussole vise " .. (label ~= "" and label or "le groupe") .. who, nil, nil)
      B.Announce("follow", "Boussole : " .. (label ~= "" and label or "le groupe"), "le groupe y va · clic : pourquoi")
    end
  end
  return 1
end

-- ===================================================================== avis sur la carte « Prochaine étape »
local NOTICE = "card"
function openPage()
  local Wn = T.Window
  if Wn and Wn._active and Wn.Open and Wn.pageIndex and Wn.pageIndex.brain then Wn.Open("brain") return end
  if T.ShowPanel then T:ShowPanel() end
end
B.OpenPage = openPage
function B.Announce(kind, title, sub, fn)
  if not opt("notice") then return end
  S.announce = { kind = kind, title = title, sub = sub, at = now(), fn = fn }
  due("notice", 91)                                      -- l'avis s'efface tout seul après 90 s
end
local function nextStepLabel()
  local R, P = T.Route, T.Planner
  if not (R and R.Stops and P and P.StepLabel) then return nil end
  local okS, st = pcall(R.Stops, R, 6)
  if not okS or type(st) ~= "table" then return nil end
  for _, e in ipairs(st) do if e.step then return P:StepLabel(e.step) end end
  return nil
end
--- clic du joueur : la boussole vise le PNJ connu le plus proche pour ce besoin (QuestieDB / déjà vu)
function B.GoService(kind)
  kind = kind or ((S.needs or {})[1] or {}).kind
  if not kind then return nil end
  local Geo = T.Geo
  local m, x, y
  if Geo and Geo.Player then m, x, y = Geo.Player() end
  local e = nearestOf(services(kind, m), (m and x) and { map = m, x = x, y = y } or nil)
  local Cm = T.Compass
  if e and Cm and Cm.SetTarget then
    return try(Cm.SetTarget, Cm, { mapID = e.map, x = e.x / 100, y = e.y / 100, label = cut(e.name or "PNJ", 40), source = "brain" })
  end
  if T.ClickNote then T:ClickNote("Aucun PNJ connu pour ça sur cette carte : le prochain marchand de ton trajet fera l'affaire.") end
  return nil
end
local function cityClick() B.GoService() end
--- la ligne d'avis du pilote (une seule à la fois) : urgent > décision (90 s) > en vol > en ville > pierre de foyer
function B.UpdateNotice()
  local Wn = T.Window
  local c = S.ctx
  local cand
  local urgent
  for _, n in ipairs(S.needs or {}) do if n.urgent then urgent = n break end end
  local a = S.announce
  if a and now() - a.at > 90 then S.announce, a = nil, nil end
  if urgent and opt("errands") and c.main ~= "dead" and c.main ~= "ghost" and c.main ~= "dungeon" then
    local st
    for _, s in ipairs(stops()) do if not s.done and s.key == "errand:" .. urgent.kind then st = s end end
    local kind = urgent.kind
    cand = { urgent.text, st and "arrêt ajouté au trajet · clic : la boussole y va" or "clic : la boussole vise le PNJ connu le plus proche", 2,
      nil, function() B.GoService(kind) end }
  elseif a then
    cand = { a.title, a.sub, 1, nil, a.fn }
  elseif c.main == "taxi" then
    local nx = nextStepLabel()
    if nx then cand = { "En vol : à l'arrivée, " .. cut(nx, 40), "le trajet reprend à l'atterrissage", 1, "always" } end
  elseif c.main == "city" and S.errandText and S.errandText ~= "" and opt("errands") then
    cand = { "Courses ici : " .. cut(S.errandText, 40), "clic : la boussole vise le PNJ le plus proche", 1, nil, cityClick }
  elseif S.hearth then
    cand = { fmt("Pierre de foyer : %s à rendre à %s", plural(S.hearth.turns, "quête", "quêtes"), S.hearth.name),
      fmt("≈ %d min gagnées · plus rien d'autre ici", floor((S.hearth.gain or 0) / 60 + 0.5)), 1 }
  end
  if not (cand and B._active) then
    if S.notice then S.notice = nil B:Clear(NOTICE) end
    if Wn and type(Wn.CARD_NOTICE) == "table" then Wn.CARD_NOTICE["Brain:" .. NOTICE] = nil end
    return
  end
  local key = tconcat({ tostring(cand[1]), tostring(cand[2]), tostring(cand[3]) }, "|")
  if S.notice == key then return end
  S.notice = key
  if Wn and type(Wn.CARD_NOTICE) == "table" then Wn.CARD_NOTICE["Brain:" .. NOTICE] = { cand[1], cand[2] } end
  B:Suggest(NOTICE, cand[1], cand[3], { ttl = 0, sound = false, cooldown = 0, kind = cand[4], tab = "now",
    onClick = cand[5] or openPage, tooltip = { "Pilote auto : " .. tostring(cand[2]),
      cand[5] and "Pourquoi, annuler : la page « Pilote auto » (pastille de la minimap)" or "Clic : la page « Pilote auto » (pourquoi, annuler)" } })
  -- la carte se redessine à l'image suivante (clé remise à zéro : le texte de l'avis a changé)
  if Wn and Wn._active and Wn.Dirty and Wn.RenderCardSoon then pcall(Wn.Dirty) pcall(Wn.RenderCardSoon) end
end

-- ===================================================================== annuler (clic du joueur)
--- défait une décision ; le choix du joueur gagne ensuite pour la session → vrai si défait
function B.Undo(id)
  local e = B.Find(tonumber(id))
  if not (e and type(e.u) == "table" and not e.undone) then return false end
  local u, o = e.u, ov()
  local Z = zp()
  if u.kind == "park" then
    if parked()[u.q] then unpark(u.q, "annulé", true, true) bump() if T.Fire then T:Fire("BRAIN_PARKED") end else o.keep[u.q] = true end
  elseif u.kind == "zone" then
    local c = B.char
    local pick = type(c.pick) == "table" and c.pick or nil
    if Z and Z.char then
      local prev = pick and pick.prev
      if prev and prev.mode == "zones" and next(prev.zones or {}) then
        Z.char.mode, Z.char.zones = "zones", prev.zones
        if Z.Bump then Z:Bump() end
      else zoneAuto(Z) end
    end
    c.pick, o.zone = false, true
  elseif u.kind == "style" then
    local c = B.char
    local st = type(c.style) == "table" and c.style or nil
    if Z and st and Z.SetStyle then local i = styleIndex(Z, st.prev) if i then try(Z.SetStyle, Z, i, "player") end end
    c.style, o.style = false, true
  elseif u.kind == "errand" then
    dropStop(u.key, true)
    o.errand[u.k2 or ""] = true
    local P = T.Planner
    if P and P.Evaluate then pcall(P.Evaluate, P) P._sig = nil if P.Render then pcall(P.Render, P) end end
  else
    return false
  end
  e.undone = true
  B.Log("undo", "Annulé : " .. tostring(e.t), "Ton choix gagne jusqu'à la fin de la session.", nil)
  S.announce = nil
  S.pageDirty = true
  B.UpdateNotice()
  return true
end

-- ===================================================================== le passage
--- une règle on / off (page « Pilote auto », options) ; couper une règle défait ce qu'elle tenait
function B.SetRule(key, on)
  if not (B.db and B.db[key] ~= nil) then return false end
  B.db[key] = on and true or false
  if B.OnOptionChanged then B:OnOptionChanged(key, B.db[key]) end
  return true
end
-- une erreur dans un passage ne casse pas les autres : comptée (tests), remise au fichier d'erreurs (/tai erreurs)
B.errors = 0
local function guard(fn, ...)
  local ok, err = pcall(fn, ...)
  if not ok then
    B.errors, B.lastError = (B.errors or 0) + 1, tostring(err)
    if T.Debug then T:Debug("Pilote auto : " .. tostring(err)) end
    if T._errorHook then pcall(T._errorHook, err, "Brain") end
  end
  return ok
end
B._Guard = guard
local function pageRefresh()
  if not S.pageDirty then return end
  S.pageDirty = false
  local Wn = T.Window
  if Wn and Wn.IsOpen and Wn.IsOpen() and Wn.CurrentPage and Wn.CurrentPage() == "brain" and Wn.Dirty then pcall(Wn.Dirty) end
end
function B.Run()
  if not (B._active and B.char) then return end
  if inCombat() then S.afterCombat = true return end
  S.inRun, S.runLog, S.runLogN = true, nil, nil
  guard(B.RunBody)
  S.inRun, S.runLog, S.runLogN = false, nil, nil
  schedule()
end
--- un passage : le contexte (léger) + UNE tâche lourde au plus par image ; le reste à l'image suivante (pas de pic)
B.HEAVY = { "needs", "park", "zone", "errand", "hearth", "follow" }
function B.RunBody()
  local t = now()
  local d = S.due
  local function ready(cat) local v = d[cat] if v and v <= t + 0.05 then d[cat] = nil return true end return false end
  local ctxR = ready("ctx")
  if ctxR or S.dirtyCtx then guard(B.UpdateContext) end
  ov()
  local c = S.ctx
  local busy = c.main == "dead" or c.main == "ghost" or c.main == "taxi" or c.main == "afk"
  local away = busy or c.main == "dungeon"
  -- (4.0.4) session métier (T.focus, ProfLevel) : le pilote ne touche ni au trajet, ni aux courses, ni à la zone
  local focus = T.FocusMode and T:FocusMode() or nil
  if focus then away = true end
  local done = false
  local noGuide = T.QuestGuideOn and not T:QuestGuideOn()
  for _, cat in ipairs(B.HEAVY) do
    local v = d[cat]
    -- (5.0 STABLE) guide de quêtes coupé : aucune règle de quête / de trajet (pause, zone, courses sur le trajet, foyer, groupe)
    if v and noGuide and cat ~= "needs" then d[cat] = nil v = nil end
    if v and v <= t + 0.05 then
      if done then d[cat] = t + 0.1                       -- une seule par image : la suivante juste après
      else
        d[cat] = nil
        if cat == "needs" or not S.raw then
          guard(B.ReadNeeds)
          -- un besoin est apparu / parti / devenu urgent : les courses se revoient (pas à chaque objet ramassé)
          local sig = K.NeedsSig(B.Needs())
          if sig ~= S.readSig then S.readSig = sig if not d.errand then d.errand = t + 0.1 end end
          if cat ~= "needs" then d[cat] = t + 0.1 end
          done = true
        elseif cat == "park" then
          -- décisions de quêtes : jamais mort / en vol / absent / en donjon (le journal attend l'arrivée)
          if away then d.park = t + 5 else guard(B.ParkPass) S.pageDirty = true done = true end
        elseif cat == "zone" then
          if away then d.zone = t + 10 else guard(B.ZonePass) guard(B.StylePass) done = true end
        elseif cat == "errand" then
          if focus or c.main == "dungeon" or c.main == "dead" or c.main == "ghost" then d.errand = t + 10
          else guard(B.ErrandPass) S.pageDirty = true done = true end
        elseif cat == "hearth" then
          if away then d.hearth = t + 10 else guard(B.HearthPass) done = true end
        elseif cat == "follow" then
          if focus then d.follow = t + 10 else guard(B.FollowPass) done = true end
        end
      end
    end
  end
  ready("notice")
  guard(B.UpdateNotice)
  pageRefresh()
end

-- ===================================================================== annonce du passage de niveau
local function onLevel(lv)
  lv = tonumber(lv) or myLevel()
  S.levelAt = now()
  if B.char then B.char.train = lv end
  B.Log("level", "Niveau " .. lv .. " : tout est revu (quêtes en pause, zone, maître de classe)", nil, nil)
end

-- ===================================================================== cycle de vie
local function ctxSoon() S.dirtyCtx = true due("ctx", 0.2) end
local function guideOff() return T.QuestGuideOn and not T:QuestGuideOn() or false end
local function questsSoon()
  if guideOff() then return end   -- (5.0 STABLE) guide de quêtes coupé : le pilote ne touche plus aux quêtes ni au trajet
  due("park", 2.5) due("zone", 4) due("hearth", 4)
end
function B:OnEnable()
  ov()
  local Z = T.ZonePlan
  -- (contrat ZONEFIX) une quête en pause n'est plus au trajet : Z:Accept la refuse (« pause ») ; ZonePlan peut le faire
  -- lui-même (Z.BRAIN_AWARE = true) — sinon le pilote enveloppe Z.Accept une fois (notre module, aucun cadre du jeu)
  if type(Z) == "table" and type(Z.Accept) == "function" and not Z.BRAIN_AWARE and not Z._brainAccept then
    local orig = Z.Accept
    Z._brainAccept = orig
    Z.Accept = function(self, q, rec, lq, ...)
      local ok, why = orig(self, q, rec, lq, ...)
      if not ok then return ok, why end                   -- ses propres règles d'abord (donjon, élite, zone… : dites telles quelles)
      if type(lq) == "table" and lq.done == 1 then return ok, why end   -- prête à rendre : toujours au trajet
      local Bn = T.Brain
      local p = Bn and Bn.Parked and Bn.Parked(q)
      if p and K.ROUTE_KEYS[p.k] then return false, "pause" end
      return ok, why
    end
  end
  for _, ev in ipairs({ "PLAYER_ENTERING_WORLD", "ZONE_CHANGED_NEW_AREA" }) do
    self:RegisterEvent(ev, function() ctxSoon() questsSoon() due("errand", 3) due("follow", 2) end)
  end
  for _, ev in ipairs({ "PLAYER_DEAD", "PLAYER_ALIVE", "PLAYER_UNGHOST", "PLAYER_CONTROL_GAINED", "PLAYER_UPDATE_RESTING",
    "UPDATE_EXHAUSTION" }) do
    self:RegisterEvent(ev, function() ctxSoon() due("notice", 1.2) end)
  end
  -- décollage : le jeu dit « en vol » (UnitOnTaxi) un instant après la perte de contrôle → relu aussi 2 s plus tard
  for _, ev in ipairs({ "PLAYER_CONTROL_LOST", "TAXIMAP_CLOSED" }) do
    self:RegisterEvent(ev, function()
      ctxSoon()
      T:After(2, function() ctxSoon() due("notice", 0.3) end)
    end)
  end
  self:RegisterEvent("PLAYER_FLAGS_CHANGED", function(_, _, unit) if unit == nil or unit == "player" then ctxSoon() end end)
  self:RegisterEvent("PLAYER_LEVEL_UP", function(_, _, lv) onLevel(sv(lv)) ctxSoon() questsSoon() due("errand", 3) end)
  self:RegisterEvent("GROUP_ROSTER_UPDATE", function() ctxSoon() due("park", 3) due("zone", 3) due("follow", 2) end)
  local function needsSoon() due("needs", 6) end
  self:RegisterEvent("BAG_UPDATE_DELAYED", needsSoon)
  self:RegisterUnitEvent("UNIT_INVENTORY_CHANGED", "player", needsSoon)
  self:RegisterEvent("UPDATE_INVENTORY_DURABILITY", needsSoon)
  self:RegisterEvent("TRAINER_SHOW", function()
    if not sv(try(G("IsTradeskillTrainer"))) and B.char then B.char.train = false due("errand", 1) end
  end)
  self:RegisterEvent("PLAYER_STARTED_MOVING", function() S.moveAt = now() due("ctx", 21) end)
  self:RegisterEvent("PLAYER_STOPPED_MOVING", function() S.moveAt = nil if S.ctx.main == "travel" then ctxSoon() end end)
  self:RegisterEvent("QUEST_ACCEPTED", function(_, _, a, b)
    local q = tonumber(sv(b)) or tonumber(sv(a))
    if q then S.accepted[q] = now() end
    ctxSoon()
    due("needs", 2)
  end)
  self:RegisterEvent("QUEST_WATCH_UPDATE", function(_, _, q)
    q = tonumber(sv(q))
    if q and not C("C_QuestLog", "GetInfo") then                -- client classique : index du journal
      local _, _, _, _, _, _, _, id = try(G("GetQuestLogTitle"), q)
      q = tonumber(sv(id))
    end
    S.progressAt = now()
    if q then
      S.progress[q] = now()
      if parked()[q] then due("park", 1) end               -- tu avances une quête en pause : elle revient
    end
  end)
  self:RegisterEvent("QUEST_WATCH_LIST_CHANGED", function(_, _, q, added)
    q = tonumber(sv(q))
    if S.busyWatch or not q or not sv(added) then return end
    local p = parked()[q]
    if p and zoneWatchOn() then due("park", 2.5) return end -- le suivi auto juge si c'est ta main ; on relit après lui
    if p and p.w then                                      -- suivie à nouveau, et pas par nous : ton choix gagne
      unpark(q, "tu la suis à la main", true)
      bump()
      S.pageDirty = true
    end
  end)
  self:RegisterEvent("QUEST_REMOVED", function() due("park", 2) due("needs", 2) end)
  self:On("PLAN_BUILT", function() ctxSoon() questsSoon() due("errand", 3) end)
  self:On("COMBAT_START", function()
    local t = now()
    local f = S.fights
    f[#f + 1] = t
    while #f > 0 and t - f[1] > 300 do table.remove(f, 1) end
    S.dirtyCtx = true
  end)
  self:On("COMBAT_END", function()
    S.dirtyCtx = true
    if S.afterCombat then S.afterCombat = false due("ctx", 1) else due("ctx", 2) end
  end)
  self:On("GROUP_INTENT", function() due("follow", 0.5) end)
  -- (4.0.4) fin d'une session métier : le pilote reprend où il en était
  self:On("FOCUS_CHANGED", function(_, _, kind) if not kind then due("park", 1) due("errand", 1.5) due("zone", 2) end end)
  self:On("SUMMARY", function() pcall(B.WriteSummary) end)
  if T.RegisterSlash then
    T:RegisterSlash("pilote", function(rest)
      rest = tostring(rest or ""):lower():gsub("^%s+", ""):gsub("%s+$", "")
      if rest == "off" then T:SetModuleEnabled("Brain", false)
      elseif rest == "on" then T:SetModuleEnabled("Brain", true)
      else openPage() end
    end, "pilote : la page « Pilote auto » (ce qu'il a décidé, pourquoi, annuler) · /tai pilote on|off")
  end
  ctxSoon()
  questsSoon()
  due("errand", 5)
end

--- couper le pilote : tout ce qu'il tenait est rendu (quêtes reprises, zone et mode d'avant, courses retirées)
function B:OnDisable()
  S.token = S.token + 1
  S.timerAt, S.due = nil, {}
  local c = self.char
  if not c then return end
  local n = 0
  for q in pairs(c.parked or {}) do unpark(q, "pilote coupé", false, true) n = n + 1 end
  local Z = T.ZonePlan
  if type(c.pick) == "table" and Z and Z.char and Z.char.mode == "zones" and Z.char.zones and Z.char.zones[c.pick.map] then
    local prev = c.pick.prev
    if prev and prev.mode == "zones" and next(prev.zones or {}) then Z.char.mode, Z.char.zones = "zones", prev.zones
      if Z.Bump then Z:Bump() end
    else zoneAuto(Z) end
  end
  if type(c.style) == "table" and Z and Z.SetStyle and Z.StyleIndex and type(Z.STYLES) == "table" then
    local i = styleIndex(Z, c.style.prev)
    local curI = try(Z.StyleIndex, Z)
    if i and Z.STYLES[curI] and Z.STYLES[curI].id == c.style.set then try(Z.SetStyle, Z, i, "brain") end
  end
  c.pick, c.style = false, false
  for _, s in ipairs(c.stops or {}) do s.done = true end
  S.stopSig = (S.stopSig or 0) + 1
  if n > 0 then bump() if T.Fire then T:Fire("BRAIN_PARKED") end end
  S.announce, S.notice, S.hearth = nil, nil, nil
  local Wn = T.Window
  if Wn and type(Wn.CARD_NOTICE) == "table" then Wn.CARD_NOTICE["Brain:" .. NOTICE] = nil end
  B.Log("off", "Pilote auto coupé : tout est remis comme avant", nil, nil)
end

function B:OnOptionChanged(key)
  if key == "park" or key == "lowValue" then due("park", 0.2)
  elseif key == "zone" or key == "style" then due("zone", 0.2)
  elseif key == "errands" then due("errand", 0.2)
  elseif key == "hearth" then due("hearth", 0.2)
  elseif key == "follow" then S.lastIntent = nil due("follow", 0.2)
  elseif key == "notice" then S.announce = nil due("notice", 0.1) end
  S.pageDirty = true
end

--- résumé pour TreeAI Coach : contexte, quêtes en pause (titre + raison), dernières décisions
function B.WriteSummary()
  local s = T.Summary and T:Summary()
  if type(s) ~= "table" then return end
  local list = {}
  for _, p in ipairs(B.ParkedList()) do
    if #list < 10 then list[#list + 1] = { title = p.n, reason = p.k, why = p.t } end
  end
  local dec = {}
  for _, e in ipairs(B.Decisions(5)) do dec[#dec + 1] = { at = e.at, kind = e.k, text = e.t, undone = e.undone or nil } end
  s.brain = { v = 1, ctx = S.ctx.main, parked = list, decisions = dec, updated = epoch() }
end
