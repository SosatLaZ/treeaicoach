-- TreeAI — « Groupe » : rejoindre ses amis au plus vite (ou en passant rendre des quêtes), point de rencontre,
-- ce que veut le groupe (intention déduite + « Suivre le groupe » / « Partager mon trajet »), vote léger (GROUPJOIN 4.0.1).
-- Partie du module « Groupe » (Modules/Group.lua) : même interrupteur, même onglet, même fil « GRP » v4 ; pas un module
-- à part. Lecture seule ; sur CLIC seulement : boussole TreeAI (Compass:SetTarget{ source = "join" }), zone de travail du
-- trajet (ZonePlan:SetMode), donjon prévu (Dungeon.SetPlanned). Rien en combat, rien dans le chat (le chat de groupe est
-- LU, jamais écrit), aucune fonction protégée, aucune globale.
--
-- Données (vraies seulement ; ce qui manque est dit) :
--   * position d'un membre : le jeu (UnitPosition, C_Map.GetBestMapForUnit / GetPlayerMapPosition ; hors instance ;
--     valeur secrète → rien) ; sinon celle qu'un ami TreeAI partage (champs « m » + « ps » du fil, ≤ 1 / 20 s) ; sinon le
--     nom de sa zone ; sinon « position inconnue » ;
--   * le plus rapide : Route.FastestWorld (marche à TA vitesse mesurée × détour appris = NavTime, vols connus, bateaux /
--     zeppelins, pierre de foyer prête si elle fait gagner ≥ 3 min) ;
--   * « Rentable » (J.Engine.Detour, ici ; ZONEFIX pourrait le reprendre dans Route) : même arrivée, en glissant dans les
--     tronçons à pied les rendus prêts (XP = GetQuestLogRewardXP lue par Quests / la boussole) et les objectifs du trajet
--     (Planner) qui coûtent ≤ 20 % de temps en plus — insertion la moins chère, gourmande sur l'XP par seconde ajoutée ;
--   * intention du groupe (J.Engine.Intent) : indices pondérés avec déclin exponentiel — chat de groupe (mots-clés FR/EN
--     + noms de zones / donjons / maîtres de vol, gardés en MÉMOIRE seulement : jamais le texte, jamais sauvé), positions
--     et déplacements (se rapprocher, se regrouper à une entrée / un maître de vol, être dans un donjon), étapes et zones
--     choisies des amis TreeAI, quêtes communes, objectif du groupe, vote, rendez-vous, trajet partagé, chef ×1,5 ;
--     hystérésis contre les sauts.
-- Coût : positions lues ≤ 1 / 15 s hors combat ; trajets recalculés seulement si quelqu'un a bougé (cases de 60 yd) ou si
-- le journal a changé, et seulement page Groupe ouverte (ou trajet vers un ami en cours).
local _, ns = ...
ns = type(ns) == "table" and ns or {}
local T = _G.TreeAI
if type(T) ~= "table" then return end
local Grp = ns.Group or (type(T.modules) == "table" and T.modules.Group) or nil
if type(Grp) ~= "table" then return end

local J = {}
Grp.Join = J
T.GroupJoin = J

local floor, max, min, sqrt, abs, mexp = math.floor, math.max, math.min, math.sqrt, math.abs, math.exp
local tconcat, tsort, tinsert, tremove = table.concat, table.sort, table.insert, table.remove
local fmt = string.format
local huge = math.huge
local cut = T.Cut or function(s, n) s = tostring(s or "") if #s > (n or 40) then return s:sub(1, n or 40) end return s end

local MAX_EXTRA, MIN_ALLOW, MAX_STOPS = 0.20, 30, 4        -- « Rentable » : ≤ 20 % de temps en plus (au moins 30 s)
local HS_GAIN = 180                                          -- pierre de foyer seulement si elle fait gagner ≥ 3 min
local NEAR_YD, ARRIVE_YD, HUB_YD = 120, 35, 45
local SAMPLE_GAP, SAMPLE_KEEP = 15, 9                        -- positions : 1 / 15 s, 2 min gardées
local RDV_TTL, PLAN_TTL, VOTE_TTL, VOTE_KEEP = 600, 600, 120, 600
local SHARE_GAP, VOTE_GAP, PROPOSE_GAP = 30, 60, 15
local AIM_GAP, AIM_REFRESH = 30, 480                         -- boussole revue ≤ 1 / 30 s ; rafraîchie ≤ 8 min (point « vieux »)

-- ------------------------------------------------------------------ utilitaires
local function pass(ok, ...) if ok then return ... end return nil end
local function call(fn, ...) if type(fn) ~= "function" then return nil end return pass(pcall(fn, ...)) end
local function pub(v)   -- valeur secrète (Forever, combat / verrou du chat) → nil
  local f = rawget(_G, "issecretvalue")
  if v ~= nil and type(f) == "function" then
    local ok, s = pcall(f, v)
    if not ok or s then return nil end
  end
  return v
end
local function num(v) return tonumber(pub(v)) end
local function now() return tonumber((call(GetTime))) or 0 end
local function inCombat() return (T.InCombat and T:InCombat()) and true or false end
local function shortName(full) if type(full) ~= "string" or full == "" then return nil end return (full:gsub("%-.*$", "")) end
local function q(s) return "« " .. tostring(s or "") .. " »" end
local function myName() return shortName(pub((call(UnitName, "player")))) or "?" end
local function andList(t, n) if Grp.AndList then return Grp.AndList(t, n) end return tconcat(t, ", ") end
local function clean(v, n)
  if Grp.Clean then return Grp.Clean(v, n) end
  if type(v) ~= "string" or v == "" then return nil end
  return v
end
local function numText(n)
  local F = T.Fmt
  if F and F.Num then return F.Num(n) end
  return tostring(floor(tonumber(n) or 0))
end
local function ucfirst(s) return (tostring(s or ""):gsub("^%l", string.upper)) end
local function lcfirst(s) return (tostring(s or ""):gsub("^%u", string.lower)) end
local function inInstance()
  local inst, kind = call(IsInInstance)
  inst, kind = pub(inst), pub(kind)
  return (inst and kind ~= "none") and true or false
end
local function mod(name) return type(T.modules) == "table" and T.modules[name] or nil end
local function active(m) return type(m) == "table" and m._active ~= false and (not m.IsEnabled or m:IsEnabled()) end
local function distText(yd)
  local Cp = T.Compass
  local G2 = type(Cp) == "table" and Cp.Geo
  if G2 and G2.Dist and yd then return G2.Dist(yd, Cp.db and Cp.db.units) end
  local m = (tonumber(yd) or 0) * 0.9144
  if m >= 1000 then return (fmt("%.1f km", m / 1000):gsub("%.", ",")) end
  return floor(m + 0.5) .. " m"
end

-- ------------------------------------------------------------------ moteur (pur, testable)
local E = {}
J.Engine = E

--- distance (yd) entre deux points monde { c, n, w } ; nil = autre continent / inconnu
function E.Dist(a, b)
  if not (a and b and a.n and b.n and a.w and b.w) or a.c ~= b.c then return nil end
  local dn, dw = a.n - b.n, a.w - b.w
  return sqrt(dn * dn + dw * dw)
end

--- « 6 min » (jamais 0) ; « moins d'1 min »
function E.Min(sec)
  sec = tonumber(sec) or 0
  if sec < 50 then return "moins d'1 min" end
  return max(1, floor(sec / 60 + 0.5)) .. " min"
end
--- temps ajouté : « +40 s » / « +3 min »
function E.Extra(sec)
  sec = max(0, floor((tonumber(sec) or 0) + 0.5))
  if sec < 60 then return "+" .. max(5, floor(sec / 5 + 0.5) * 5) .. " s" end
  return "+" .. floor(sec / 60 + 0.5) .. " min"
end

local MODE_FR = { walk = "à pied", fly = "vol", boat = "bateau", zeppelin = "zeppelin", hs = "pierre de foyer" }
E.MODE_FR = MODE_FR
--- mode principal d'un trajet (étapes Route.FastestWorld)
function E.MainMode(legs)
  local m = "walk"
  for _, l in ipairs(legs or {}) do
    if l.mode == "hs" then return "hs" end
    if l.mode == "boat" or l.mode == "zeppelin" then m = l.mode
    elseif l.mode == "fly" and m == "walk" then m = "fly" end
  end
  return m
end

--- « jusqu'à Bravo » / « jusqu'au maître de vol … » / « jusqu'aux … » (jamais « jusqu'à le »)
function E.Jusqua(name)
  name = tostring(name or "l'arrivée")
  if name:sub(1, 3) == "le " then return "jusqu'au " .. name:sub(4) end
  if name:sub(1, 4) == "les " then return "jusqu'aux " .. name:sub(5) end
  return "jusqu'à " .. name
end
--- étapes en clair (infobulle) ; destName = l'arrivée (« Bravo », « l'entrée de … ») ; stops = arrêts glissés (Detour)
function E.LegLines(legs, destName, stops)
  local out = {}
  local extra = {}
  for _, s in ipairs(stops or {}) do extra[s.k] = extra[s.k] or {}; tinsert(extra[s.k], s.cand) end
  for i, l in ipairs(legs or {}) do
    for _, c in ipairs(extra[i] or {}) do out[#out + 1] = "En passant : " .. tostring(c.label) end
    local nxt = legs[i + 1]
    local to = type(l.to) == "table" and l.to or {}
    local name = to.name and cut(to.name, 32) or nil
    if l.mode == "walk" then
      if not nxt then out[#out + 1] = fmt("À pied %s (%s)", E.Jusqua(destName), E.Min(l.sec))
      elseif nxt.mode == "fly" then out[#out + 1] = fmt("À pied jusqu'au maître de vol%s (%s)", name and (" : " .. name) or "", E.Min(l.sec))
      elseif nxt.mode == "boat" or nxt.mode == "zeppelin" then
        out[#out + 1] = fmt("À pied jusqu'au quai%s (%s)", name and (" : " .. name) or "", E.Min(l.sec))
      else out[#out + 1] = fmt("À pied (%s)", E.Min(l.sec)) end
    elseif l.mode == "fly" then out[#out + 1] = fmt("Vol%s (%s)", name and (" pour " .. name) or "", E.Min(l.sec))
    elseif l.mode == "boat" or l.mode == "zeppelin" then
      out[#out + 1] = fmt("%s%s (%s, attente comprise)", l.mode == "boat" and "Bateau" or "Zeppelin", name and (" pour " .. name) or "", E.Min(l.sec))
    elseif l.mode == "hs" then out[#out + 1] = fmt("Pierre de foyer%s (%s)", name and (" vers " .. name) or "", E.Min(l.sec)) end
  end
  return out
end

--- « Rentable » : même arrivée que le plus rapide, avec des arrêts de quête glissés dans les tronçons à pied s'ils coûtent
--- ≤ maxExtra du temps (au moins minAllow s). from = départ { c, n, w } ; legs = étapes du plus rapide { mode, sec, to } ;
--- base = secondes du plus rapide ; cands = { { place, svc = s, value, xp = n|nil, approx, kind = "turn"|"obj", label, q } } ;
--- walk(a, b) = secondes à pied entre deux points (même modèle que le trajet) ou nil (autre continent).
--- Un vol / un bateau ne s'arrête pas en route ; avant une pierre de foyer on peut s'arrêter (on la lance de là).
--- → { extra, total, stops = { { k = n° de l'étape avant laquelle s'arrêter, cand } } (ordre du trajet), turnins, objs,
---   xp, approx } ou nil (rien ne vaut le détour)
function E.Detour(from, legs, base, cands, walk, o)
  o = o or {}
  if not (from and legs and base and walk) or #(cands or {}) == 0 then return nil end
  local allow = max(o.minAllow or MIN_ALLOW, (o.maxExtra or MAX_EXTRA) * base)
  local segs, prev = {}, from
  for i, l in ipairs(legs) do
    if l.mode == "walk" and l.to then segs[#segs + 1] = { k = i, nodes = { { place = prev }, { place = l.to } }, closed = true }
    elseif l.mode == "hs" and i == 1 then segs[#segs + 1] = { k = i, nodes = { { place = prev } }, closed = false } end
    prev = l.to or prev
  end
  if #segs == 0 then return nil end
  local left = {}
  for i, c in ipairs(cands) do if c.place then left[i] = c end end
  local used = 0
  for _ = 1, o.maxStops or MAX_STOPS do
    local best, bs, bj, bd, br
    for ci, c in pairs(left) do
      for _, s in ipairs(segs) do
        local nodes = s.nodes
        for j = 1, #nodes - (s.closed and 1 or 0) do
          local p, nx = nodes[j].place, nodes[j + 1] and nodes[j + 1].place
          local wpc = walk(p, c.place)
          local d
          if wpc then
            d = wpc + (c.svc or 0)
            if nx then
              local wcn, wpn = walk(c.place, nx), walk(p, nx)
              d = (wcn and wpn) and (d + wcn - wpn) or nil
            end
          end
          if d and d >= 0 and used + d <= allow then
            local r = (c.value or 0) / max(d, 10)
            if not best or r > br or (r == br and ci < best) then best, bs, bj, bd, br = ci, s, j, d, r end
          end
        end
      end
    end
    if not best then break end
    tinsert(bs.nodes, bj + 1, { place = left[best].place, cand = left[best] })
    left[best] = nil
    used = used + bd
  end
  local res = { extra = used, total = base + used, stops = {}, turnins = 0, objs = 0, xp = 0, approx = false }
  for _, s in ipairs(segs) do
    for _, nd in ipairs(s.nodes) do
      local c = nd.cand
      if c then
        res.stops[#res.stops + 1] = { k = s.k, cand = c }
        if c.kind == "turn" then
          res.turnins = res.turnins + 1
          res.xp = res.xp + (tonumber(c.xp) or 0)
          if c.approx or not tonumber(c.xp) then res.approx = true end
        else
          res.objs = res.objs + 1
        end
      end
    end
  end
  if #res.stops == 0 then return nil end
  return res
end

--- point de rencontre : parmi des lieux { place, kind, label, useful }, celui qui raccourcit le plus le trajet du plus
--- loin (distance à vol d'oiseau : la seule vraie pour tous) ; un lieu « utile » (prochain arrêt commun) l'emporte s'il
--- ne coûte que ≤ 25 % de plus au plus loin. pos = { { name, place } } → lieu, distance du plus loin, son nom
function E.Meeting(pos, spots)
  if #(pos or {}) < 2 then return nil end
  local best, bestFar, bestName
  for _, s in ipairs(spots or {}) do
    local far, farName, ok = 0, nil, true
    for _, p in ipairs(pos) do
      local d = E.Dist(p.place, s.place)
      if not d then ok = false break end
      if d > far then far, farName = d, p.name end
    end
    s._far, s._farName = ok and far or nil, farName
    if ok and (not best or far < bestFar) then best, bestFar, bestName = s, far, farName end
  end
  if not best then return nil end
  for _, s in ipairs(spots) do
    if s.useful and s._far and s._far <= bestFar * 1.25 + 60 then return s, s._far, s._farName end
  end
  return best, bestFar, bestName
end

--- vote : ballots = { [nom] = n° } ; voters = { noms } → compte par choix, gagnant (n°), égalité, en attente
function E.Tally(vote, voters)
  local n, waiting = {}, {}
  for _, idx in pairs(vote and vote.ballots or {}) do n[idx] = (n[idx] or 0) + 1 end
  for _, v in ipairs(voters or {}) do if not (vote and vote.ballots[v]) then waiting[#waiting + 1] = v end end
  local best, top, tie = nil, 0, false
  for i = 1, #(vote and vote.choices or {}) do
    local c = n[i] or 0
    if c > top then best, top, tie = i, c, false elseif c == top and c > 0 then tie = true end
  end
  return n, best, tie, waiting
end

--- se rapproche-t-il ? samples = { { t, d } } du plus ancien au plus récent (d = distance au lieu) → vitesse yd/s, ETA s
function E.Approach(samples)
  local n = #(samples or {})
  if n < 3 then return nil end
  local a, b = samples[1], samples[n]
  local dt = b.t - a.t
  if dt < 20 then return nil end
  for i = 2, n do if samples[i].d > samples[i - 1].d + 15 then return nil end end   -- toujours plus près (bruit ≤ 15 yd)
  local v = (a.d - b.d) / dt
  if v < 2.5 or a.d - b.d < 100 then return nil end
  return v, b.d / v
end

-- ---------------------------------------------------------------- texte (chat de groupe) : normalisation et mots-clés
local ACCENT = { ["à"] = "a", ["â"] = "a", ["ä"] = "a", ["á"] = "a", ["ç"] = "c", ["é"] = "e", ["è"] = "e", ["ê"] = "e",
  ["ë"] = "e", ["î"] = "i", ["ï"] = "i", ["í"] = "i", ["ô"] = "o", ["ö"] = "o", ["ó"] = "o", ["ù"] = "u", ["û"] = "u",
  ["ü"] = "u", ["ú"] = "u", ["ÿ"] = "y", ["œ"] = "oe", ["æ"] = "ae", ["À"] = "a", ["Â"] = "a", ["Ä"] = "a", ["Ç"] = "c",
  ["É"] = "e", ["È"] = "e", ["Ê"] = "e", ["Ë"] = "e", ["Î"] = "i", ["Ï"] = "i", ["Ô"] = "o", ["Ö"] = "o", ["Ù"] = "u",
  ["Û"] = "u", ["Ü"] = "u", ["Œ"] = "oe" }
--- minuscules sans accents ni ponctuation, entouré d'espaces (« Kraal de Tranchebauge ! » → " kraal de tranchebauge ")
function E.Norm(s)
  if type(s) ~= "string" then return " " end
  s = s:gsub("|c%x%x%x%x%x%x%x%x", ""):gsub("|r", ""):gsub("|H.-|h(.-)|h", "%1")
  s = s:gsub("[\195\197][\128-\191]", ACCENT):lower():gsub("[^%w]+", " ")
  s = s:gsub("^ +", ""):gsub(" +$", "")
  return " " .. s .. " "
end
-- sans l'article en tête (« Les Tarides » → " tarides ")
local function normName(s)
  local n = E.Norm(s)
  n = n:gsub("^ les ", " "):gsub("^ la ", " "):gsub("^ le ", " "):gsub("^ l ", " "):gsub("^ the ", " ")
  return n
end
E.NormName = normName

local KW = {
  go = { " on va ", " allons ", " go ", " gogo ", " let s go ", " lets go ", " direction ", " on part ", " j arrive ",
    " jarrive ", " en route ", " je vais ", " on y va ", " je pars ", " on bouge ", " on file ", " vers ", " head to ",
    " going ", " heading " },
  rdv = { " rdv ", " rendez vous ", " retrouve ", " retrouvons ", " on se rejoint ", " rejoins ", " rejoignez ", " regroupe ",
    " regroupez ", " regroup ", " rassemble ", " meet ", " rejoint ", " devant ", " j y suis ", " sur place ", " je suis la ",
    " a l entree ", " i m at ", " im at " },
  wait = { " attendez ", " attends ", " attend ", " att ", " wait ", " afk ", " brb ", " bio ", " pause ", " 2 min ", " 2min ",
    " 1 min ", " 5 min ", " 2mn ", " deux min ", " une minute ", " one sec " },
  turn = { " je rends ", " rendre ", " rendu ", " rends ", " turn in ", " turnin ", " valider ", " je valide " },
  dungeon = { " donjon ", " dj ", " instance ", " inst ", " dungeon ", " dung " },
  farm = { " farm ", " farmer ", " farme ", " farmons ", " grind ", " on reste ", " xp ici " },
  ah = { " hv ", " hdv ", " ah ", " hotel des ventes ", " auction ", " encheres " },
  finish = { " fini ", " finir ", " finit ", " finish ", " on finit ", " termine ", " terminer ", " finissons " },
}
E.KW = KW
-- abréviations de donjons que les joueurs écrivent (en plus du nom, de la clé et des mots-clés de Data/Dungeons.lua)
local DG_ALIAS = { RFC = { "rfc" }, WC = { "wc" }, DM = { "vc", "deadmines" }, SFK = { "sfk" }, BFD = { "bfd" },
  RFK = { "rfk", "kraal" }, GNOMER = { "gnomer", "gnome" }, SM = { "sm", "monas", "monast" }, RFD = { "rfd" },
  ULDA = { "ulda" }, ZF = { "zf" }, MARA = { "mara" }, ST = { "temple" }, BRD = { "brd" }, BRS = { "brs", "lbrs", "ubrs" },
  STRAT = { "strat" }, SCHOLO = { "scholo" } }
E.DG_ALIAS = DG_ALIAS

--- message → indices { { kind = "dg"|"z"|"fm"|"wait"|"ah"|"farm"|"turn"|"dgany"|"finish", key, w, name } }.
--- index = { { pat = " nom normalisé ", kind, key, name, q (qualité 0..1) } } (le plus long d'abord)
function E.ParseChat(text, index)
  local s = E.Norm(text)
  if #s < 4 then return {} end
  local has = {}
  for k, list in pairs(KW) do
    for _, w in ipairs(list) do if s:find(w, 1, true) then has[k] = true break end end
  end
  local out, seen, covered = {}, {}, {}
  local verb = has.go or has.rdv or has.dungeon or has.finish
  for _, e in ipairs(index or {}) do
    local a, b = s:find(e.pat, 1, true)
    if a then
      local inside = false   -- « kraal de tranchebauge » déjà pris : « tranchebauge » seul n'ajoute rien
      for _, r in ipairs(covered) do if a >= r[1] and b <= r[2] then inside = true break end end
      local id = e.kind .. ":" .. tostring(e.key)
      if not inside and not seen[id] then
        seen[id] = true
        covered[#covered + 1] = { a, b }
        local w
        if e.kind == "dg" then w = ((verb or has.dungeon) and 1.5 or 0.6) * (e.q or 1)
        elseif e.kind == "fm" then w = ((has.rdv or has.go) and 1.2 or 0.5) * (e.q or 1)
        else w = (verb and 1.0 or 0.4) * (e.q or 1) end
        out[#out + 1] = { kind = (e.kind == "z" and has.finish) and "finish" or e.kind, key = e.key, w = w, name = e.name }
      end
    end
  end
  local anyDg = false
  for _, o in ipairs(out) do if o.kind == "dg" then anyDg = true end end
  if has.wait then out[#out + 1] = { kind = "wait", w = 1.0 } end
  if has.ah and #out == 0 then out[#out + 1] = { kind = "ah", w = 0.8 } end
  if has.farm then out[#out + 1] = { kind = "farm", w = 0.7 } end
  if has.turn then out[#out + 1] = { kind = "turn", w = 0.6 } end
  if has.dungeon and not anyDg then out[#out + 1] = { kind = "dgany", w = 0.6 } end
  return out
end

--- indices « explicites » (quelqu'un l'a dit / choisi / y est) contre l'état de fond (zones, étapes)
local EXPLICIT = { chat = true, vote = true, rdv = true, plan = true, goal = true, inside = true, at = true }
E.EXPLICIT = EXPLICIT
--- intention du groupe. ev = { { key, w, at (GetTime) | nil = état présent, tau (s), what, who } } ; t = maintenant ;
--- prev = clé gardée (hystérésis : une autre ne la remplace qu'avec ≥ 25 % de plus, sauf si elle est plus explicite) ;
--- deux personnes qui disent le même lieu dans le chat = +0,5 ; compat(a, b) = deux lectures qui ne s'opposent pas (le
--- Kraal et Les Tarides où est son entrée) : la confiance ne se mesure que contre une lecture qui s'oppose
--- → { key, score, conf, second, why, all } | nil
function E.Intent(ev, t, prev, compat)
  local score, why, expl, speakers = {}, {}, {}, {}
  for _, e in ipairs(ev or {}) do
    local w = tonumber(e.w) or 0
    if e.at and e.tau then w = w * mexp(-max(0, t - e.at) / e.tau) end
    if e.key and w > 0.02 then
      score[e.key] = (score[e.key] or 0) + w
      if EXPLICIT[e.what] then expl[e.key] = (expl[e.key] or 0) + w end
      if e.what == "chat" and e.who and w > 0.3 then
        speakers[e.key] = speakers[e.key] or {}
        speakers[e.key][e.who] = true
      end
      why[e.key] = why[e.key] or {}
      tinsert(why[e.key], { e = e, w = w })
    end
  end
  for k, set in pairs(speakers) do
    local n = 0
    for _ in pairs(set) do n = n + 1 end
    if n >= 2 then score[k], expl[k] = score[k] + 0.5, (expl[k] or 0) + 0.5 end
  end
  local best
  for k, v in pairs(score) do
    if not best or v > score[best] or (v == score[best] and k < best) then best = k end
  end
  if not best then return nil end
  if prev and prev ~= best and score[prev] and score[best] < score[prev] * 1.25 and (expl[best] or 0) <= (expl[prev] or 0) then
    best = prev
  end
  local second = 0
  for k, v in pairs(score) do
    if k ~= best and v > second and not (compat and compat(best, k)) then second = v end
  end
  local list = why[best]
  tsort(list, function(a, b) if a.w ~= b.w then return a.w > b.w end return tostring(a.e.what) < tostring(b.e.what) end)
  -- confiance = avance sur la 2e lecture possible (0,3 = « rien d'autre » : un seul indice faible n'est jamais sûr)
  return { key = best, score = score[best], conf = score[best] / (score[best] + second + 0.3), second = second, why = list, all = score }
end

-- ------------------------------------------------------------------ lieux : cartes, zones, donjons, maîtres de vol
local mapNames = {}
local function mapName(id)
  id = tonumber(id)
  if not id then return nil end
  local n = mapNames[id]
  if n == nil then
    local info = C_Map and call(C_Map.GetMapInfo, id)
    n = type(info) == "table" and clean(pub(info.name), 30) or false
    mapNames[id] = n
  end
  return n or nil
end
J.MapName = mapName
local function zoneOf(map)
  map = tonumber(map)
  if not map then return nil end
  local Z = T.ZonePlan
  if type(Z) == "table" and type(Z.ZoneOf) == "function" then return tonumber((call(Z.ZoneOf, map))) or map end
  return map
end
J.ZoneOf = zoneOf
local function faction() return pub((call(UnitFactionGroup, "player"))) end
local function factionOK(tag)
  local R = T.Route
  if type(R) == "table" and type(R.FactionOK) == "function" then return R.FactionOK(tag, faction()) end
  return true
end
local function travel() return T.Data and T.Data.Travel or nil end
local function dungeons() local D = T.Data and T.Data.Dungeons return D and D.list or {} end
local function dungeonByKey(key)
  for _, d in ipairs(dungeons()) do if d.key == key then return d end end
  return nil
end
J.Dungeon = dungeonByKey
local function dungeonByName(name)
  if type(name) ~= "string" or name == "" then return nil end
  local D = T.Dungeon
  if type(D) == "table" and type(D.FindExact) == "function" then return call(D.FindExact, name) end
  local n = normName(name)
  for _, d in ipairs(dungeons()) do if normName(d.n) == n then return d end end
  return nil
end
J.DungeonByName = dungeonByName

local function worldPct(map, x, y)   -- x, y en % de la carte → { c, n, w, map, x, y }
  local Geo = T.Geo
  map, x, y = tonumber(map), tonumber(x), tonumber(y)
  if not (Geo and Geo.World and map and x and y) then return nil end
  local n, w, c = Geo.World(map, x, y)
  if not (n and w) then return nil end
  return { c = c, n = n, w = w, map = map, x = x, y = y }
end
J.WorldPct = worldPct

-- entrées de donjon : APPRISES (dernière position dehors d'un ami TreeAI / la mienne juste avant d'entrer) d'abord ;
-- sinon positions de Classic (≈, à vérifier en jeu : seulement pour « se regroupe à l'entrée » et la boussole, marquées ≈)
local ENTRANCE = { WC = { 1413, 46.0, 36.4 }, RFK = { 1413, 42.3, 89.9 }, BFD = { 1440, 14.4, 14.0 }, SFK = { 1421, 44.8, 67.7 },
  DM = { 1436, 42.6, 71.7 }, RFC = { 1454, 51.8, 49.6 }, GNOMER = { 1426, 24.4, 39.8 }, SM = { 1420, 82.6, 33.2 },
  STOCKS = { 1453, 50.5, 66.3 }, ULDA = { 1418, 42.4, 11.8 }, ZF = { 1446, 38.7, 20.0 }, MARA = { 1443, 29.1, 62.5 } }
J.ENTRANCE = ENTRANCE
local function learned()
  local db = Grp.db
  if type(db) ~= "table" then return {} end
  if type(db.entrances) ~= "table" then db.entrances = {} end
  return db.entrances
end
local entCache = {}
function J.Entrance(key)
  if type(key) ~= "string" then return nil end
  local c = entCache[key]
  if c ~= nil then return c or nil end
  local p
  local e = learned()[key]
  if type(e) == "table" and tonumber(e[1]) then
    p = worldPct(e[1], e[2], e[3])
    if p then p.name, p.learned = "entrée", true end
  end
  if not p then
    local s = ENTRANCE[key]
    p = s and worldPct(s[1], s[2], s[3]) or nil
    if p then p.name, p.approx = "entrée", true end
  end
  entCache[key] = p or false
  return p
end
function J.LearnEntrance(key, map, x, y)   -- x, y en %
  map, x, y = tonumber(map), tonumber(x), tonumber(y)
  if not (key and map and x and y) or x < 0 or x > 100 or y < 0 or y > 100 then return false end
  learned()[key] = { map, floor(x * 10 + 0.5) / 10, floor(y * 10 + 0.5) / 10 }
  entCache[key] = nil
  return true
end

local function taxiPlace(id, r)
  if not (type(r) == "table" and tonumber(r[5]) and tonumber(r[5]) > 0) then return nil end
  return { c = r[2], n = r[3], w = r[4], map = r[5], x = r[6], y = r[7], name = r[1], id = id, kind = "fm" }
end
local GENERIC_FM = { ["transport"] = true, ["generique"] = true }
-- maîtres de vol de ma faction (ou neutres) sur un continent (données fixes : calculé une fois par continent)
local fmCache = {}
function J.FlightMasters(c)
  local ck = tostring(c) .. ":" .. tostring(faction())
  if fmCache[ck] then return fmCache[ck] end
  local D = travel()
  local out = {}
  for id, r in pairs(D and D.taxi or {}) do
    if (c == nil or r[2] == c) and factionOK(r[8]) then
      local key = normName(r[1]):gsub("^ +", ""):gsub(" +$", "")
      if not GENERIC_FM[key] and not key:find("chemin de quete", 1, true) and not key:find("programmeur", 1, true) then
        local p = taxiPlace(id, r)
        if p then out[#out + 1] = p end
      end
    end
  end
  tsort(out, function(a, b) return a.id < b.id end)
  fmCache[ck] = out
  return out
end

-- index des noms pour le chat (zones connues, donjons + abréviations, maîtres de vol), le plus long d'abord
local idxCache
function J.PlaceIndex(extraMaps)
  local sig = {}
  for _, m in ipairs(extraMaps or {}) do sig[#sig + 1] = tostring(m) end
  local key = tconcat(sig, ",") .. "|" .. tostring(faction())
  if idxCache and idxCache.key == key then return idxCache.list end
  local list, names = {}, {}
  local function addPat(pat, kind, k, name, qq)
    if type(pat) ~= "string" then return end
    local p = normName(pat)
    if #p < 4 then return end               -- 2 lettres + espaces : trop ambigu
    if names[p] and names[p] ~= kind .. ":" .. tostring(k) then return end   -- même nom pour deux lieux : le 1er gagne
    if names[p] then return end
    names[p] = kind .. ":" .. tostring(k)
    list[#list + 1] = { pat = p, kind = kind, key = k, name = name, q = qq or 1 }
  end
  for _, d in ipairs(dungeons()) do
    if d.faction == nil or d.faction == faction() then
      addPat(d.n, "dg", d.key, d.n, 1)
      addPat(d.key, "dg", d.key, d.n, 0.8)
      for _, k in ipairs(d.kw or {}) do addPat(k, "dg", d.key, d.n, 0.9) end
      for _, k in ipairs(DG_ALIAS[d.key] or {}) do addPat(k, "dg", d.key, d.n, 0.8) end
    end
  end
  local maps, seenMap = {}, {}
  local function addMap(m)
    m = zoneOf(m)
    if m and not seenMap[m] then seenMap[m] = true; maps[#maps + 1] = m end
  end
  for _, m in ipairs(extraMaps or {}) do addMap(m) end
  local D = travel()
  local tl = {}
  for _, r in pairs(D and D.taxi or {}) do if tonumber(r[5]) and r[5] > 0 then tl[#tl + 1] = r[5] end end
  tsort(tl)
  for _, m in ipairs(tl) do addMap(m) end
  for m = 1411, 1458 do addMap(m) end
  for _, m in ipairs(maps) do
    local n = mapName(m)
    if n then addPat(n, "z", m, n, 1) end
  end
  for _, p in ipairs(J.FlightMasters(nil)) do addPat(p.name, "fm", p.id, p.name, 0.9) end
  tsort(list, function(a, b) if #a.pat ~= #b.pat then return #a.pat > #b.pat end return a.pat < b.pat end)
  idxCache = { key = key, list = list }
  return list
end
--- carte d'une zone d'après son nom (index du chat) ou nil
function J.ZoneByName(name, extraMaps)
  local n = normName(name)
  for _, e in ipairs(J.PlaceIndex(extraMaps)) do if e.kind == "z" and e.pat == n then return e.key end end
  return nil
end
function J.ResetIndex() idxCache, mapNames, fmCache, entCache = nil, {}, {}, {} end

-- ------------------------------------------------------------------ positions
local JS = { cache = {}, samples = {}, chat = {}, plans = {}, placeEv = {}, near = {}, active = nil, rdv = nil, vote = nil,
  intent = nil, spots = {}, lastSample = -1e9, lastShare = -1e9, lastVote = -1e9, lastPropose = -1e9, seq = 0 }
J._state = JS

--- ma position : { c, n, w, map, x, y (%) } ou nil, raison
function J.Me()
  if inInstance() then return nil, "instance" end
  local Geo = T.Geo
  local map, x, y
  if Geo and Geo.Player then map, x, y = Geo.Player() end
  local p = worldPct(map, x, y)
  if p then return p end
  local a, b, _, inst = call(UnitPosition, "player")
  a, b, inst = num(a), num(b), num(inst)
  if a and b then
    p = { c = inst or -1, n = a, w = b, map = tonumber(map) }
    if p.map and Geo and Geo.FromWorld then p.x, p.y = Geo.FromWorld(p.map, a, b) end
    return p
  end
  return nil, "pos"
end

local function parsePos(s)   -- "523.401" (‰) → x, y en %
  local a, b = tostring(s or ""):match("^(%d+)%.(%d+)$")
  a, b = tonumber(a), tonumber(b)
  if not (a and b) or a > 1000 or b > 1000 then return nil end
  return a / 10, b / 10
end
local function parseStepPos(s)   -- "1441.523.401" → map, x, y en %
  local m, a, b = tostring(s or ""):match("^(%d+)%.(%d+)%.(%d+)$")
  m, a, b = tonumber(m), tonumber(a), tonumber(b)
  if not (m and a and b) or m < 1 or m > 100000 or a > 1000 or b > 1000 then return nil end
  return m, a / 10, b / 10
end
J.ParsePos, J.ParseStepPos = parsePos, parseStepPos

--- où est un membre : { c, n, w, map, x, y, src = "jeu"|"treeai", zone, zoneName } ; sinon nil + nom de zone connu
function J.Where(m)
  if not m then return nil end
  if m.me then
    local p = J.Me()
    if p then p.src, p.zone = "jeu", zoneOf(p.map); p.zoneName = mapName(p.zone) end
    return p, p and p.zoneName
  end
  local u, out, zname = m.unit, nil, nil
  if u and m.online ~= false and not inInstance() then
    local map = C_Map and num(call(C_Map.GetBestMapForUnit, u))
    local a, b, _, inst = call(UnitPosition, u)
    a, b, inst = num(a), num(b), num(inst)
    if a and b then
      out = { c = inst or -1, n = a, w = b, map = map, src = "jeu" }
      local Geo = T.Geo
      if map and Geo and Geo.FromWorld then out.x, out.y = Geo.FromWorld(map, a, b) end
    elseif map and C_Map and C_Map.GetPlayerMapPosition then
      local v = call(C_Map.GetPlayerMapPosition, map, u)
      local x, y
      if type(v) == "table" then
        if type(v.GetXY) == "function" then x, y = call(v.GetXY, v) else x, y = v.x, v.y end
      end
      x, y = num(x), num(y)
      if x and y and not (x == 0 and y == 0) then
        out = worldPct(map, x * 100, y * 100)
        if out then out.src = "jeu" end
      end
    end
    if map then zname = mapName(zoneOf(map)) end
    if out then out.zone = zoneOf(out.map) end
  end
  local st = m.addon and m.st
  if not out and st and st.m and st.ps then
    local x, y = parsePos(st.ps)
    out = x and worldPct(st.m, x, y) or nil
    if out then out.src, out.zone = "treeai", zoneOf(out.map) end
  end
  if out then out.zoneName = (out.zone and mapName(out.zone)) or zname end
  if not zname and st then zname = (st.m and mapName(zoneOf(st.m))) or st.z end
  return out, (out and out.zoneName) or zname or m.zoneName
end

--- se rapproche-t-il de ce lieu ? → vitesse, ETA (échantillons des 2 dernières minutes)
function J.ApproachTo(name, place)
  local s = JS.samples[name]
  if not (s and place) then return nil end
  local out, t = {}, now()
  for _, e in ipairs(s) do
    if e.t >= t - 130 then
      local d = E.Dist(e, place)
      if not d then return nil end
      out[#out + 1] = { t = e.t, d = d }
    end
  end
  return E.Approach(out)
end

-- indices de lieu (≤ 1 / 15 s) : regroupés à une entrée / un maître de vol / au point de rencontre, en approche.
-- Personne n'a bougé (cases de 60 yd) et rien en approche : rien à refaire. Maîtres de vol : seulement si deux d'entre
-- vous sont à moins de 120 yd l'un de l'autre (sinon personne ne peut s'y être regroupé).
local function cell60(p) return p and (tostring(p.c) .. ":" .. floor(p.n / 60) .. ":" .. floor(p.w / 60)) or "-" end
local function placeEvidence(list)
  local ev, near = {}, {}
  local cont
  local sig = { tostring(JS.rdv and JS.rdv.id) }
  for _, m in ipairs(list) do
    if m._where then cont = cont or m._where.c end
    sig[#sig + 1] = tostring(m.name) .. "=" .. cell60(m._where)
  end
  local key = tconcat(sig, ";")
  if key == JS.placeKey and not JS.placeMoving then return end
  JS.placeKey = key
  if not cont then JS.placeEv, JS.near, JS.placeMoving = ev, near, false return end
  local places = {}
  for _, d in ipairs(dungeons()) do
    local ep = J.Entrance(d.key)
    if ep and ep.c == cont then places[#places + 1] = { key = "dg:" .. d.key, place = ep, name = d.n, entrance = true } end
  end
  if JS.rdv and JS.rdv.place then places[#places + 1] = { key = "rdv", place = JS.rdv.place, name = JS.rdv.label } end
  local close = false
  for i = 1, #list do
    for j = i + 1, #list do
      local d = list[i]._where and list[j]._where and E.Dist(list[i]._where, list[j]._where)
      if d and d <= 120 then close = true end
    end
  end
  if close then
    for _, fm in ipairs(J.FlightMasters(cont)) do places[#places + 1] = { key = "fm:" .. fm.id, place = fm, name = fm.name, fm = true } end
  end
  local moving = false
  for _, pl in ipairs(places) do
    local at, coming = {}, {}
    local radius = pl.fm and 60 or (NEAR_YD + (pl.place.approx and 150 or 0))
    for _, m in ipairs(list) do
      local p = m._where
      local d = p and E.Dist(p, pl.place)
      if d and d <= radius then at[#at + 1] = m.name
      elseif d and not pl.fm and d < 3000 and J.ApproachTo(m.name, pl.place) then coming[#coming + 1] = m.name end
    end
    if #at >= 2 or (#at >= 1 and not pl.fm and #coming >= 1) then
      for _, n in ipairs(at) do ev[#ev + 1] = { key = pl.key, w = pl.fm and 0.5 or 0.9, what = "at", who = n, name = pl.name } end
    end
    for _, n in ipairs(coming) do ev[#ev + 1] = { key = pl.key, w = 0.6, what = "coming", who = n, name = pl.name } end
    if #coming > 0 then moving = true end
    if #at > 0 or #coming > 0 then near[pl.key] = { at = at, coming = coming, place = pl.place, name = pl.name } end
  end
  JS.placeEv, JS.near, JS.placeMoving = ev, near, moving
end

--- échantillons de position (≤ 1 / 15 s, hors combat) : déplacements, « se rapproche », dernière position dehors
function J.Sample(t, list)
  if t - JS.lastSample < SAMPLE_GAP or inCombat() then return end
  JS.lastSample = t
  local alive = {}
  for _, m in ipairs(list or {}) do
    if m.name and m.online ~= false then
      alive[m.name] = true
      local p = J.Where(m)
      m._where = p
      local s = JS.samples[m.name] or {}
      JS.samples[m.name] = s
      if p then
        s[#s + 1] = { t = t, c = p.c, n = p.n, w = p.w, map = p.map, x = p.x, y = p.y }
        while #s > SAMPLE_KEEP do tremove(s, 1) end
      end
    end
  end
  for name in pairs(JS.samples) do if not alive[name] then JS.samples[name] = nil end end
  placeEvidence(list or {})
end

-- ------------------------------------------------------------------ trajets
local function walkModel()
  local R = T.Route
  local g = type(R) == "table" and type(R.Graph) == "function" and call(R.Graph) or nil
  if g and g.speed and g.detour then return g, g.speed, g.detour end
  local NT = T.NavTime
  local sp = NT and NT.Speed and tonumber((call(NT.Speed, "w"))) or 7
  return nil, sp, 1.3
end
local function walkSec(speed, detour)
  return function(a, b)
    local d = E.Dist(a, b)
    if not d then return nil end
    return d * detour / speed
  end
end
--- le plus rapide de a vers b : { sec, legs, mode, walk } ; la pierre de foyer seulement si elle fait gagner ≥ 3 min
function J.Fastest(a, b)
  if not (a and b) then return nil end
  local R = T.Route
  local g, speed, detour = walkModel()
  local sec, legs
  if g and R.FastestWorld then
    sec, legs = R.FastestWorld(g, a, b, nil)
    local hsOK = R.HearthReady and call(R.HearthReady)
    local bind = hsOK and R.BindPlace and call(R.BindPlace) or nil
    if bind then
      local s2, l2 = R.FastestWorld(g, a, b, { hearth = true, bind = bind })
      if s2 and s2 < huge and (not sec or sec == huge or s2 + HS_GAIN < sec) and E.MainMode(l2) == "hs" then sec, legs = s2, l2 end
    end
  end
  if not sec or sec == huge then
    local w = walkSec(speed, detour)(a, b)
    if not w then return nil end
    sec, legs = w, { { mode = "walk", sec = w, to = b, yd = E.Dist(a, b) } }
  end
  return { sec = sec, legs = legs, mode = E.MainMode(legs), walk = walkSec(speed, detour) }
end

-- arrêts de quête possibles en chemin : rendus prêts (boussole : point de remise le plus proche, XP du jeu) et objectifs
-- du trajet (Planner : position, temps estimé)
local function logQuest(qid)
  local Q = mod("Quests")
  for _, x in ipairs(Q and Q.lastList or {}) do if x.id == qid then return x end end
  return nil
end
function J.Candidates()
  local out, seen = {}, {}
  local Cp = T.Compass
  for _, t in ipairs(type(Cp) == "table" and type(Cp.targets) == "table" and Cp.targets or {}) do
    -- les rendus de la boussole (point de remise le plus proche) ; pas l'arrêt du trajet (t.plan), qui doublerait la quête
    if t.kind == "turnin" and not t.plan and t.questID and t.mapID and t.x and not seen["t" .. t.questID] then
      local p = worldPct(t.mapID, t.x * 100, t.y * 100)
      if p then
        seen["t" .. t.questID] = true
        local lq = logQuest(t.questID)
        local xp = tonumber(t.xp) or (lq and tonumber(lq.xp))
        local title = (lq and lq.title) or t.title or ("quête " .. t.questID)
        out[#out + 1] = { place = p, svc = 15, xp = xp, value = xp or 800, kind = "turn", q = t.questID,
          label = "rendre " .. q(cut(title, 30)) .. (xp and fmt(" (+%s XP)", numText(xp)) or "") }
      end
    end
  end
  local P = T.Planner
  local steps, cur = type(P) == "table" and P.steps, type(P) == "table" and P.cursor
  if type(steps) == "table" and cur then
    local nObj = 0
    for i = cur, #steps do
      local s = steps[i]
      if type(s) == "table" and not s._done and not s._other and s.x and s.map and s.q then
        local ok = "o" .. s.q .. ":" .. tostring(s.o)
        if s.k == "obj" and nObj < 8 and not seen[ok] then
          local p = worldPct(s.map, s.x, s.y)
          if p then
            nObj = nObj + 1
            seen[ok] = true
            local label = type(P.StepLabel) == "function" and call(P.StepLabel, P, s) or ("objectif de la quête " .. s.q)
            out[#out + 1] = { place = p, svc = max(20, (tonumber(s._pred) or 1) * 60), value = (tonumber(s.value) or 300) * 0.5,
              kind = "obj", q = s.q, label = lcfirst(label) }
          end
        elseif s.k == "turn" and not seen["t" .. s.q] and type(P.log) == "table" and type(P.log[s.q]) == "table" and P.log[s.q].done == 1 then
          local p = worldPct(s.map, s.x, s.y)
          if p then
            seen["t" .. s.q] = true
            local label = type(P.StepLabel) == "function" and call(P.StepLabel, P, s) or ("rendre la quête " .. s.q)
            out[#out + 1] = { place = p, svc = 15, xp = nil, approx = true, value = tonumber(s.xp) or 800, kind = "turn", q = s.q,
              label = lcfirst(label) }
          end
        end
      end
    end
  end
  return out
end

--- trajet vers un lieu : fast (le plus rapide) et profit (rentable) ; mis en cache tant que personne ne bouge
local function cell(p) return p and fmt("%s:%d:%d", tostring(p.c), floor(p.n / 60), floor(p.w / 60)) or "-" end
-- Un trajet coûte ~10-20 kinstr (graphe des vols, + variante pierre de foyer) : quand on a bougé, au plus UN trajet
-- recalculé par image, les autres gardent leur ancien résultat en attendant (la page se refait 3 s plus tard) ;
-- lazy = lignes de la page (sinon : clic du joueur, proposition reçue → calcul tout de suite).
local routeFrame, routeDone = -1, 0
function J.RouteTo(key, me, dest, withProfit, lazy)
  if not (me and dest) then return nil end
  local Cp, P = T.Compass, T.Planner
  local R = T.Route
  local g = walkModel()
  local ctx = tconcat({ tostring(withProfit), tostring(g), tostring(type(Cp) == "table" and Cp.targets),
    tostring(type(P) == "table" and P.steps), tostring(type(P) == "table" and P.cursor),
    tostring(type(R) == "table" and R.HearthReady and call(R.HearthReady)) }, "|")
  local k = cell(me) .. "|" .. cell(dest)
  local c = JS.cache[key]
  if c and c.k == k and c.ctx == ctx then return c.r end
  local t = now()
  if t ~= routeFrame then routeFrame, routeDone = t, 0 end
  -- seulement des positions qui ont bougé (mêmes quêtes, même étape, mêmes vols) : l'ancien résultat sert en attendant
  if lazy and c and c.r and c.ctx == ctx and routeDone >= 1 then
    Grp:Dirty()
    return c.r
  end
  routeDone = routeDone + 1
  local f = J.Fastest(me, dest)
  local r = f and { fast = f } or nil
  if r and withProfit then r.profit = E.Detour(me, f.legs, f.sec, J.Candidates(), f.walk) end
  JS.cache[key] = { k = k, ctx = ctx, r = r }
  return r
end

-- ------------------------------------------------------------------ « Rejoindre » : trajet en cours (boussole, étapes)
-- JS.active = { who, label, mode, stops = { { kind, place, label, q, sec, next, final } }, i, t0, sec, aimAt, aimP, left }
-- who = nom d'un membre, "@rdv" (point de rencontre reçu / proposé) ou "@group" (entrée / lieu du groupe)
local function destOf(who)
  if who == "@rdv" then return JS.rdv and JS.rdv.place or nil end
  if type(who) == "string" and who:sub(1, 1) == "@" then return JS.spots[who] end
  for _, m in ipairs(Grp:List()) do
    if m.name == who and not m.me then return (J.Where(m)) end
  end
  return nil
end
J.DestOf = destOf

local function joinLabel(a)
  if a.who == "@rdv" then return "Rendez-vous : " .. cut(JS.rdv and JS.rdv.label or "point de rencontre", 28) end
  return a.label or ("Rejoindre " .. tostring(a.who))
end

local function aim(place, label, qid, force)
  local Cp = T.Compass
  if not (type(Cp) == "table" and type(Cp.SetTarget) == "function" and place and place.map and place.x) then return nil end
  local a = JS.active
  local t = now()
  if a and not force and a.aimP and a.aimLabel == label then
    local moved = E.Dist(a.aimP, place) or huge
    local me = J.Me()
    local left = me and E.Dist(me, place) or huge
    if (moved < max(120, 0.2 * left) or t - (a.aimAt or -1e9) < AIM_GAP) and t - (a.aimAt or -1e9) < AIM_REFRESH then return true end
  end
  local r = call(Cp.SetTarget, Cp, { mapID = place.map, x = place.x / 100, y = place.y / 100, label = cut(label, 40),
    source = "join", questID = qid })
  if a then a.aimAt, a.aimP, a.aimLabel = t, { c = place.c, n = place.n, w = place.w }, label end
  return r
end

-- étapes d'un trajet (les arrêts de quête glissés avant l'étape k de leur tronçon)
local function buildStops(route, mode)
  local f = route.fast
  local before = {}
  if mode == "profit" and route.profit then
    for _, s in ipairs(route.profit.stops) do before[s.k] = before[s.k] or {}; tinsert(before[s.k], s.cand) end
  end
  local stops = {}
  for i, l in ipairs(f.legs) do
    for _, c in ipairs(before[i] or {}) do
      stops[#stops + 1] = { kind = "q", place = c.place, label = ucfirst(c.label), q = c.q, qkind = c.kind, sec = c.svc }
    end
    local nxt = f.legs[i + 1]
    stops[#stops + 1] = { kind = l.mode, place = l.to, sec = l.sec, next = nxt and nxt.mode or nil, final = (i == #f.legs) }
  end
  return stops
end

function J.Start(who, mode, label)
  if inCombat() then Grp:Say("Hors combat seulement") return false end
  local me = J.Me()
  if not me then Grp:Say("Ta position est inconnue (instance ou chargement)") return false end
  local dest = destOf(who)
  if not dest then Grp:Say("Position inconnue pour l'instant") return false end
  local route = J.RouteTo("go:" .. tostring(who), me, dest, mode == "profit")
  if not (route and route.fast) then Grp:Say("Pas de chemin connu d'ici (autre continent sans transport)") return false end
  JS.active = { who = who, mode = mode or "fast", label = label, stops = buildStops(route, mode), i = 1, t0 = now(),
    sec = (mode == "profit" and route.profit) and route.profit.total or route.fast.sec }
  if Grp.SetJoin then Grp:SetJoin(who, JS.active.sec) end
  J.Progress(true)
  return true
end

--- destination du trajet en cours (QuickTravel : le vol à prendre au maître de vol) → { c, n, w, name } | nil
function J.Destination()
  local a = JS.active
  local d = a and destOf(a.who)
  if not (d and d.c and d.n and d.w) then return nil end
  local name = (a.who == "@rdv" and "le point de rencontre") or (a.who == "@group" and "le groupe") or a.who
  return { c = d.c, n = d.n, w = d.w, name = name }
end

function J.Stop(why)
  local a = JS.active
  if not a then return false end
  JS.active = nil
  if Grp.SetJoin then Grp:SetJoin(nil) end
  Grp:Withdraw("join")
  local Cp = T.Compass
  local cur = type(Cp) == "table" and type(Cp.Current) == "function" and call(Cp.Current) or nil
  if type(cur) == "table" and cur.kind == "custom" and a.aimLabel and cur.label == cut(a.aimLabel, 40) and type(Cp.ClearWaypoint) == "function" then
    call(Cp.ClearWaypoint)
  end
  if why then Grp:Say(why) end
  return true
end

local questInLog = logQuest

-- secondes restantes (étape en cours + les suivantes)
local function remaining(a, me, dest)
  local sec = 0
  local _, sp, det = walkModel()
  for j = a.i, #a.stops do
    local s = a.stops[j]
    if j == a.i and me and s.kind ~= "fly" and s.kind ~= "boat" and s.kind ~= "zeppelin" and s.kind ~= "hs" then
      local d = E.Dist(me, (s.final and dest) or s.place)
      sec = sec + (d and (d * det / sp) or (s.sec or 0)) + ((s.kind == "q") and 15 or 0)
    else
      sec = sec + (s.sec or 0)
    end
  end
  return sec
end

--- avance le trajet en cours (2 s, hors combat) : étape atteinte → la suivante ; arrivée → fini ; boussole revue
function J.Progress(force)
  local a = JS.active
  if not a or inCombat() then return end
  local me = J.Me()
  local dest = destOf(a.who)
  if a.who:sub(1, 1) ~= "@" then
    local still = false
    for _, m in ipairs(Grp:List()) do if m.name == a.who and m.online ~= false then still = true end end
    if not still then J.Stop(fmt("%s n'est plus dans le groupe", a.who)) return end
  elseif not dest then
    J.Stop("Point de rencontre annulé") return
  end
  local taxi = pub((call(UnitOnTaxi, "player"))) and true or false
  if me and dest and not taxi and (E.Dist(me, dest) or huge) <= ARRIVE_YD then
    local who = (a.who == "@rdv" and "le point de rencontre") or (a.who == "@group" and "le groupe") or a.who
    J.Stop()
    Grp:Offer("joined", "news", fmt("Tu as rejoint %s", who), 1, { ttl = 20 })
    return
  end
  local guard = 0
  while a.i <= #a.stops and guard < 8 do
    guard = guard + 1
    local s = a.stops[a.i]
    local done = false
    if s.kind == "q" then
      local x = questInLog(s.q)
      if s.qkind == "turn" then done = not x
      else done = (not x) or (x.complete and true or false) end
      if not done and me and dest then   -- dépassé : le joueur a choisi de ne pas s'arrêter
        local dS, dM, dSd = E.Dist(me, s.place), E.Dist(me, dest), E.Dist(s.place, dest)
        if dS and dM and dSd and dS > 200 and dM < dSd - 150 then done = true end
      end
    elseif s.final then
      break
    elseif s.kind == "walk" then
      done = (me and (E.Dist(me, s.place) or huge) <= HUB_YD) or (taxi and s.next == "fly") or false
    elseif s.kind == "fly" or s.kind == "boat" or s.kind == "zeppelin" then
      done = (not taxi and me and (E.Dist(me, s.place) or huge) <= 300) or false
    elseif s.kind == "hs" then
      done = (me and (E.Dist(me, s.place) or huge) <= 500) or false
    end
    if not done then break end
    a.i = a.i + 1
    force = true
  end
  local s = a.stops[a.i]
  local left = remaining(a, me, dest)
  local lab = (a.who == "@rdv") and "Rendez-vous" or (a.label or ("Rejoindre " .. a.who))
  local text
  if taxi then text = fmt("%s : en vol · %s", lab, E.Min(left))
  elseif s and s.kind == "hs" then
    text = fmt("%s : pierre de foyer vers %s · %s", lab, cut(s.place and s.place.name or "ton auberge", 26), E.Min(left))
  elseif s and s.kind == "q" then
    text = fmt("%s : %s en passant · %s", lab, cut(lcfirst(s.label), 40), E.Min(left))
    aim(s.place, s.label, s.q, force)
  elseif s and s.kind == "walk" and not s.final then
    local hub = (s.next == "fly" and "Maître de vol : " or "Quai : ") .. tostring(s.place and s.place.name or "")
    text = fmt("%s : %s · %s", lab, s.next == "fly" and ("vol depuis " .. cut(s.place and s.place.name or "le maître de vol", 26))
      or ("quai : " .. cut(s.place and s.place.name or "le quai", 26)), E.Min(left))
    aim(s.place, hub, nil, force)
  elseif s and (s.kind == "fly" or s.kind == "boat" or s.kind == "zeppelin") and not s.final then
    text = fmt("%s : prends le %s pour %s · %s", lab, MODE_FR[s.kind], cut(s.place and s.place.name or "l'arrivée", 26), E.Min(left))
  else
    local d = me and dest and E.Dist(me, dest)
    text = fmt("%s%s · %s", lab, d and (" : " .. distText(d)) or "", E.Min(left))
    if dest then aim(dest, joinLabel(a), nil, force) end
  end
  a.left = left
  if Grp.SetJoin then Grp:SetJoin(a.who, left) end
  Grp:Offer("join", "join", text, 1, { ttl = 0, onClick = function() J.Progress(true) end,
    tooltip = { joinLabel(a) .. (a.mode == "profit" and " (rentable)" or " (au plus vite)"),
      "Clic : la boussole vise l'étape en cours", "Page Groupe, « En route » : clic pour arrêter" } })
end

-- ------------------------------------------------------------------ point de rencontre, « J'y vais »
local function friends(list, v4only)
  local out = {}
  for _, m in ipairs(list or Grp:List()) do
    if not m.me and m.online ~= false and (not v4only or m.addon == "4") then out[#out + 1] = m end
  end
  return out
end

--- lieu de rencontre (≥ 2 positions sur le même continent, écart ≥ 600 yd) : maître de vol du milieu ou prochain arrêt commun
function J.MeetingSpot(list)
  local pos, c0, sig = {}, nil, {}
  for _, m in ipairs(list) do
    if m.online ~= false then
      local p = m._where
      if p then
        c0 = c0 or p.c
        if p.c == c0 then pos[#pos + 1] = { name = m.me and "toi" or m.name, place = p }; sig[#sig + 1] = cell(p) end
      end
    end
  end
  if #pos < 2 then return nil end
  local nx = Grp.next
  local e = nx and nx.mission
  local key = tconcat(sig, ";") .. "|" .. tostring(e and e.id) .. "|" .. tostring(JS.intent and JS.intent.key)
  if JS.spotCache and JS.spotCache.k == key then return JS.spotCache.spot end
  local spread = 0
  for i = 1, #pos do for j = i + 1, #pos do spread = max(spread, E.Dist(pos[i].place, pos[j].place) or 0) end end
  local spot
  local it = JS.intent
  if spread >= 600 and not (it and it.target) then
    local zones = {}
    for _, m in ipairs(list) do if m._where and m._where.zone then zones[m._where.zone] = true end end
    if it and it.zone then zones[it.zone] = true end
    local spots = {}
    for _, p in ipairs(J.FlightMasters(c0)) do
      if zones[zoneOf(p.map)] then spots[#spots + 1] = { place = p, kind = "fm", label = p.name } end
    end
    local Cp = T.Compass
    if e and e.id and type(Cp) == "table" and type(Cp.targets) == "table" then
      for _, t in ipairs(Cp.targets) do
        if t.questID == e.id and t.mapID and t.x then
          local p = worldPct(t.mapID, t.x * 100, t.y * 100)
          if p and p.c == c0 then
            spots[#spots + 1] = { place = p, kind = "hub", useful = true,
              label = (t.kind == "turnin" and "rendre " or "faire ") .. q(cut(e.title, 26)) }
          end
          break
        end
      end
    end
    local best, far, farName = E.Meeting(pos, spots)
    if best then best.far, best.farName, best.spread = far, farName, spread; spot = best end
  end
  JS.spotCache = { k = key, spot = spot }
  return spot
end

function J.Propose(spot)
  if inCombat() then Grp:Say("Hors combat seulement") return false end
  spot = spot or JS.spot
  if not (spot and spot.place and spot.place.map and spot.place.x) then return false end
  local t = now()
  if t - JS.lastPropose < PROPOSE_GAP then Grp:Say("Déjà proposé il y a un instant") return false end
  JS.lastPropose = t
  JS.seq = (JS.seq % 999) + 1
  local p = spot.place
  JS.rdv = { id = JS.seq, by = myName(), mine = true, place = p, label = spot.label, kind = spot.kind, at = t, goers = {} }
  Grp:QueueEvent("meet", tostring(floor(p.map)), tostring(floor(p.x * 10 + 0.5)), tostring(floor(p.y * 10 + 0.5)),
    spot.label or "", spot.kind or "fm", tostring(JS.seq))
  Grp:Say("Point de rencontre proposé au groupe : " .. cut(spot.label or "ici", 30))
  Grp:Dirty(true)
  return true
end

--- proposition reçue (fil v4 : meet + genre + n°)
function J.OnProposal(m, map, x, y, label, kind, id)
  local p = worldPct(map, x * 100, y * 100)
  if not p then return end
  JS.rdv = { id = id, by = m.name, place = p, label = clean(label, 24) or "point de rencontre", kind = kind, at = now(), goers = {} }
  local me = J.Me()
  local r = me and J.RouteTo("rdv", me, p, false)
  local eta = r and r.fast and E.Min(r.fast.sec)
  Grp:Offer("rdv", "rdv", fmt("%s propose un rendez-vous : %s%s — clic : j'y vais", m.name, cut(JS.rdv.label, 28),
    eta and (" · toi " .. eta) or ""), 2, { ttl = RDV_TTL, sound = true, soundKind = "group",
      onClick = function() J.Go() end,
      tooltip = { "Point de rencontre proposé par " .. m.name, "Clic : la boussole y va (au plus vite) et le groupe sait que tu viens" } })
  Grp:Dirty(true)
end

function J.Go()
  local r = JS.rdv
  if not r then return false end
  if not J.Start("@rdv", "fast") then return false end
  if not r.mine then Grp:QueueEvent("go", tostring(r.id), tostring(floor((JS.active and JS.active.sec) or 0))) end
  r.goers[myName()] = { eta = JS.active and JS.active.sec, at = now() }
  Grp:Withdraw("rdv")
  return true
end

function J.OnGo(m, a)
  local r = JS.rdv
  local id = tonumber(a[2])
  if not (r and id and r.id == id) then return end
  r.goers[m.name] = { eta = tonumber(a[3]), at = now() }
  Grp:Dirty(true)
end

-- ------------------------------------------------------------------ où va chacun, intention du groupe
--- zone visée par un membre : étape TreeAI (sp) → zone choisie (wz) → zone actuelle ; → zone, source
function J.Heading(m)
  if m.me then
    local P = T.Planner
    local s = type(P) == "table" and type(P.steps) == "table" and P.cursor and P.steps[P.cursor] or nil
    if type(s) == "table" and s.map then return zoneOf(s.map), "step" end
    local p = m._where or J.Me()
    return p and zoneOf(p.map) or nil, "here"
  end
  local st = m.addon and m.st
  if st and st.sp then
    local map = parseStepPos(st.sp)
    if map then return zoneOf(map), "step" end
  end
  if st and tonumber(st.wz) and st.wz > 0 then return zoneOf(st.wz), "pick" end
  local p = m._where
  if p and p.map then return zoneOf(p.map), "here" end
  if st and tonumber(st.m) and st.m > 0 then return zoneOf(st.m), "here" end
  return nil, nil
end

local function leaderName()
  local SQ = T.Squad
  return SQ and SQ.Leader and call(SQ.Leader) or nil
end
local function mapsOf(list)
  local maps = {}
  for _, m in ipairs(list) do
    local p = m._where
    if p and p.map then maps[#maps + 1] = p.map end
    if m.addon and m.st and tonumber(m.st.m) then maps[#maps + 1] = m.st.m end
  end
  return maps
end

--- chat de groupe (lu seulement) : indices gardés en mémoire (lieu dérivé, qui, quand) — jamais le texte, jamais sauvé
function J.OnChat(text, sender)
  text, sender = pub(text), pub(sender)
  if type(text) ~= "string" or text == "" or #text > 255 or type(sender) ~= "string" then return end
  local who = shortName(sender)
  if not who then return end
  local list = Grp:List()
  local member
  for _, m in ipairs(list) do if m.name == who then member = m end end
  if not member then return end
  local sigs = E.ParseChat(text, J.PlaceIndex(mapsOf(list)))
  if #sigs == 0 then return end
  local t = now()
  local lead = (leaderName() == who) and 1.5 or 1
  for _, s in ipairs(sigs) do
    local key
    if s.kind == "dg" then key = "dg:" .. s.key
    elseif s.kind == "z" or s.kind == "finish" then key = "z:" .. s.key
    elseif s.kind == "fm" then key = "fm:" .. s.key
    elseif s.kind == "farm" then
      local p = member._where or (member.me and J.Me()) or nil
      key = p and p.map and ("z:" .. tostring(zoneOf(p.map))) or nil
    elseif s.kind == "wait" then key = "wait:" .. who
    else key = s.kind end
    if key then
      JS.chat[#JS.chat + 1] = { key = key, w = s.w * lead, at = t, tau = (s.kind == "wait") and 150 or 300, what = "chat",
        who = who, name = s.name, sub = s.kind }
    end
  end
  while #JS.chat > 30 do tremove(JS.chat, 1) end
  Grp:Dirty(true)
end

-- quêtes communes (tenues par ≥ 2 membres) par en-tête de zone de MON journal
function J.CommonByZone()
  local out = {}
  local me = Grp._me
  local idx = Grp._state and Grp._state.index
  if not (me and me.quests and idx) then return out end
  for _, e in ipairs(idx) do
    local qq = me.quests[e.id]
    if qq and qq.header and #e.holders >= 2 then out[qq.header] = (out[qq.header] or 0) + 1 end
  end
  return out
end

-- indices : passés (chat, vote, rendez-vous, trajet partagé ; avec déclin) + présents (positions, étapes, objectif…)
function J.Evidence(list, t)
  local ev = {}
  local lead = leaderName()
  local function add(key, w, what, who, extra)
    local e = extra or {}
    e.key, e.w, e.what, e.who = key, w * ((who and who == lead) and 1.5 or 1), what, who
    ev[#ev + 1] = e
  end
  for _, e in ipairs(JS.chat) do ev[#ev + 1] = e end
  local v = JS.vote
  if v and v.closed and v.result and v.choices[v.result] then
    local c = v.choices[v.result]
    ev[#ev + 1] = { key = c.k == "d" and ("dg:" .. c.v) or ("z:" .. c.v), w = 2.0, at = v.closedAt, tau = 900, what = "vote" }
  end
  local r = JS.rdv
  if r and t - r.at < RDV_TTL then
    local n = 1
    for _ in pairs(r.goers) do n = n + 1 end
    ev[#ev + 1] = { key = "rdv", w = 1.0 + 0.4 * n, at = r.at, tau = 900, what = "rdv", who = r.by }
  end
  for name, p in pairs(JS.plans) do
    if t - p.at < PLAN_TTL and p.zone then add("z:" .. p.zone, 1.2, "plan", name, { at = p.at, tau = 600 }) end
  end
  local g = Grp.Goal and Grp:Goal()
  if type(g) == "table" and g.k == "zone" and g.z then
    local z = J.ZoneByName(g.z, mapsOf(list))
    if z then add("z:" .. tostring(z), 1.5, "goal", nil) end
  end
  local D = T.Dungeon
  if active(D) and type(D.Planned) == "function" then
    local d, why = call(D.Planned)
    if type(d) == "table" and d.key then add("dg:" .. d.key, why == "choisi" and 0.8 or 0.5, "planned") end
  end
  local zoneCount = {}
  for _, m in ipairs(list) do
    if m.online ~= false then
      local st = m.addon and m.st
      local d = st and st.z and dungeonByName(st.z)
      if not d and m.me and inInstance() then d = dungeonByName(clean(pub((call(GetRealZoneText))), 40)) end
      if d then add("dg:" .. d.key, 1.2, "inside", m.name, { name = d.n }) end
      local p = m._where
      if p and p.zone then zoneCount[p.zone] = (zoneCount[p.zone] or 0) + 1 end
      local h, src = J.Heading(m)
      if h and (src == "step" or src == "pick") then add("z:" .. tostring(h), (m.me and 0.4) or (src == "step" and 0.6 or 0.4), src, m.name) end
    end
  end
  for z, n in pairs(zoneCount) do if n >= 2 then add("z:" .. tostring(z), 0.25 * n, "here") end end
  local common = J.CommonByZone()
  for zname, n in pairs(common) do
    for z in pairs(zoneCount) do
      if mapName(z) == zname then add("z:" .. tostring(z), min(1.2, 0.15 * n), "common", nil, { n = n }) end
    end
  end
  for _, e in ipairs(JS.placeEv) do ev[#ev + 1] = e end
  return ev
end

local function placeName(key)
  local kind, v = tostring(key):match("^(%a+):(.*)$")
  if kind == "dg" then local d = dungeonByKey(v) return d and d.n or v, "dg", d end
  if kind == "z" then return mapName(v) or ("zone " .. v), "z", tonumber(v) end
  if kind == "fm" then
    local D = travel()
    local r = D and D.taxi and D.taxi[tonumber(v)]
    return r and r[1] or "maître de vol", "fm", taxiPlace(tonumber(v), r)
  end
  if key == "rdv" then return JS.rdv and JS.rdv.label or "point de rencontre", "rdv" end
  if kind == "wait" then return v, "wait" end
  return key, key
end
J.PlaceName = placeName

--- deux lectures compatibles : un lieu (entrée de donjon, maître de vol, point de rencontre) et la zone où il se trouve
function J.Compatible(a, b)
  local function zoneKey(k) return tonumber(tostring(k):match("^z:(%d+)$")) end
  local function placeOf(k)
    local kind, v = tostring(k):match("^(%a+):(.*)$")
    if kind == "dg" then return J.Entrance(v) end
    if kind == "fm" then local D = travel() return taxiPlace(tonumber(v), D and D.taxi and D.taxi[tonumber(v)]) end
    if k == "rdv" then return JS.rdv and JS.rdv.place end
    return nil
  end
  local za, zb = zoneKey(a), zoneKey(b)
  if za and zb then return false end
  local z, p = za or zb, placeOf(za and b or a)
  return (z and p and p.map and zoneOf(p.map) == z) and true or false
end

local WHAT = { vote = "Vote du groupe", rdv = "Rendez-vous proposé", plan = "Trajet partagé", goal = "Objectif du groupe",
  planned = "Donjon prévu" }
local function ago(at)
  local s = max(0, now() - (at or now()))
  if s < 60 then return "à l'instant" end
  return fmt("il y a %d min", floor(s / 60 + 0.5))
end

--- l'intention déduite : { key, kind, name, text, tip, conf, target, targetName, targetWho, zone, dungeon } | nil
function J.Infer(list, t)
  local ev = J.Evidence(list, t)
  local res = E.Intent(ev, t, JS.intent and JS.intent.key, J.Compatible)
  local explicit = false
  for _, w in ipairs(res and res.why or {}) do if EXPLICIT[w.e.what] then explicit = true break end end
  -- assez sûr : confiance ≥ 50 %, ou ≥ 40 % avec un indice explicite (chat, vote, rendez-vous, trajet, objectif, sur place)
  if not res or res.score < 0.8 or res.conf < (explicit and 0.4 or 0.5) then JS.intent = nil return nil end
  local name, kind, extra = placeName(res.key)
  local total, inZone, inside, heading = 0, 0, 0, 0
  for _, m in ipairs(list) do
    if m.online ~= false then
      total = total + 1
      local p = m._where
      if kind == "z" and p and p.zone == extra then inZone = inZone + 1 end
      if kind == "z" and (J.Heading(m)) == extra then heading = heading + 1 end
    end
  end
  -- une zone sans indice explicite : seulement si plus de la moitié y est ou s'y dirige (sinon c'est un groupe partagé)
  if kind == "z" and not explicit and heading * 2 <= total then JS.intent = nil return nil end
  for _, w in ipairs(res.why) do if w.e.what == "inside" then inside = inside + 1 end end
  local near = JS.near[res.key]
  local nAt = near and #near.at or 0
  local text, paren
  if kind == "dg" then
    if inside > 0 then text, paren = "Le groupe est dans : " .. name, fmt("%d/%d dedans", inside, total)
    else
      text = "Le groupe va vers : " .. name
      paren = nAt > 0 and fmt("%d/%d à l'entrée", nAt, total) or nil
    end
  elseif kind == "z" then
    local common = 0
    for _, w in ipairs(res.why) do if w.e.what == "common" then common = w.e.n or 0 end end
    if inZone * 2 > total then
      text = "Le groupe finit : " .. name
      paren = common > 0 and fmt("%d quête%s commune%s", common, common > 1 and "s" or "", common > 1 and "s" or "")
        or fmt("%d/%d y sont", inZone, total)
    else
      text, paren = "Le groupe va vers : " .. name, fmt("%d/%d y sont", inZone, total)
    end
  elseif kind == "fm" or kind == "rdv" then
    text = "Le groupe se retrouve à : " .. name
    paren = nAt > 0 and fmt("%d/%d au point de rencontre", nAt, total) or nil
  elseif kind == "wait" then
    text = "Le groupe attend : pause demandée par " .. tostring(name)
  elseif kind == "ah" then
    text = "Le groupe va à l'hôtel des ventes"
  elseif kind == "turn" then
    local nx = Grp.next
    text = (nx and nx.kind == "turnin") and nx.text or "Le groupe rend ses quêtes"
  elseif kind == "dgany" then
    text = "Le groupe veut faire un donjon : bouton « Voter » pour choisir"
  else
    JS.intent = nil
    return nil
  end
  if not paren then   -- la preuve la plus forte, si rien de chiffré
    local top = res.why[1] and res.why[1].e
    if top then
      if top.what == "chat" then paren = "chat de " .. tostring(top.who)
      elseif top.what == "vote" then paren = "vote du groupe"
      elseif top.what == "plan" then paren = "trajet de " .. tostring(top.who)
      elseif top.what == "goal" then paren = "objectif du groupe"
      elseif top.what == "planned" then paren = "donjon prévu"
      elseif top.what == "coming" then paren = tostring(top.who) .. " y va" end
    end
  end
  if floor(res.conf * 100 + 0.5) < 70 then   -- pas sûr : on le dit dans la phrase même
    text = text:gsub("^Le groupe va vers", "Le groupe semble aller vers"):gsub("^Le groupe finit", "Le groupe semble finir")
      :gsub("^Le groupe se retrouve à", "Le groupe semble se retrouver à")
  end
  local pct = floor(res.conf * 100 + 0.5)
  local confW = pct >= 70 and "forte" or (pct >= 50 and "moyenne" or "faible")
  local tip = { fmt("Ce que le groupe semble vouloir faire — confiance %s (%d %%)", confW, pct) }
  local me = myName()
  for i, w in ipairs(res.why) do
    if i > 6 then break end
    local e = w.e
    local who = (e.who == me) and "toi" or tostring(e.who)
    local line
    if e.what == "chat" then
      line = fmt("%s : %s (%s)", e.who == me and "Ton message" or ("Chat de " .. who), e.name and q(e.name) or ((e.sub == "wait" and "pause") or (e.sub == "ah" and "hôtel des ventes")
        or (e.sub == "farm" and "farm ici") or (e.sub == "turn" and "rendre") or (e.sub == "dgany" and "un donjon") or "intention"), ago(e.at))
    elseif e.what == "inside" then line = fmt("%s est dans %s", who, q(e.name or name))
    elseif e.what == "at" then line = fmt("%s est sur place (%s)", who, cut(e.name or name, 30))
    elseif e.what == "coming" then line = fmt("%s se rapproche de %s", who, cut(e.name or name, 30))
    elseif e.what == "step" then line = fmt("Étape TreeAI de %s : %s", who, name)
    elseif e.what == "pick" then line = fmt("Zone choisie par %s (trajet TreeAI) : %s", who, name)
    elseif e.what == "common" then line = fmt("%d quête%s en commun dans %s", e.n or 0, (e.n or 0) > 1 and "s" or "", name)
    elseif e.what == "here" then line = "Plusieurs d'entre vous y sont déjà"
    else line = (WHAT[e.what] or e.what) .. ((e.who and e.what ~= "goal") and (" : " .. who) or "") end
    tip[#tip + 1] = line
  end
  local unknown = {}
  for _, m in ipairs(list) do if not m.me and m.online ~= false and not m.addon and not m._where then unknown[#unknown + 1] = m.name end end
  if #unknown > 0 then tip[#tip + 1] = "Inconnu (sans TreeAI, hors de vue) : " .. andList(unknown, 3) end
  -- cible pour « Rejoindre » : l'entrée, le point, sinon le membre le mieux placé dans la zone
  local target, tname, twho
  if kind == "dg" then
    target = J.Entrance(extra and extra.key or "")
    tname = target and ((target.approx and "l'entrée (≈, d'après Classic) de " or "l'entrée de ") .. name) or nil
  elseif kind == "fm" then target, tname = extra, "le maître de vol de " .. name
  elseif kind == "rdv" then target, tname = JS.rdv and JS.rdv.place, name
  elseif kind == "z" then
    for _, m in ipairs(list) do
      if not m.me and m._where and m._where.zone == extra then target, tname, twho = m._where, m.name, m.name break end
    end
  end
  JS.intent = { key = res.key, kind = kind, name = name, text = paren and (text .. " (" .. paren .. ")") or text, tip = tip,
    conf = res.conf, target = target, targetName = tname, targetWho = twho, zone = (kind == "z") and extra or nil,
    dungeon = (kind == "dg") and extra or nil, explicit = explicit }
  return JS.intent
end

--- suis-je déjà avec le groupe pour cette intention ?
local function aligned(it, list)
  if not it then return true end
  local me
  for _, m in ipairs(list) do if m.me then me = m end end
  local p = me and me._where
  if it.kind == "z" then
    local h = me and J.Heading(me)
    return (p and p.zone == it.zone) or h == it.zone
  elseif it.kind == "dg" then
    if inInstance() then return dungeonByName(clean(pub((call(GetRealZoneText))), 40)) == it.dungeon end
    return (it.target and p and (E.Dist(p, it.target) or huge) <= NEAR_YD + 150) and true or false
  elseif it.kind == "fm" or it.kind == "rdv" then
    return (it.target and p and (E.Dist(p, it.target) or huge) <= NEAR_YD) and true or false
  end
  return true
end
J.Aligned = aligned
local FOLLOW_KIND = { z = true, dg = true, fm = true, rdv = true }

--- conflit de zones (où va chacun) → { text, group = zone, ref = { name }, follow } | nil
function J.Conflict(list)
  local counts, order, who = {}, {}, {}
  local myH, mySrc, lead = nil, nil, leaderName()
  for _, m in ipairs(list) do
    if m.online ~= false then
      local h, src = J.Heading(m)
      if h and not mapName(h) then h = nil end
      m._heading, m._hsrc = h, src
      if h then
        if not counts[h] then counts[h] = 0; order[#order + 1] = h; who[h] = {} end
        counts[h] = counts[h] + 1
        if not m.me then tinsert(who[h], m) end
        if m.me then myH, mySrc = h, src end
      end
    end
  end
  if #order < 2 or not myH then return nil end
  tsort(order, function(a, b) if counts[a] ~= counts[b] then return counts[a] > counts[b] end return a < b end)
  local group
  if counts[order[1]] > counts[order[2]] then group = order[1]
  else
    for _, m in ipairs(list) do if m.name == lead and not m.me and m._heading then group = m._heading end end
  end
  local function names(ms) local o = {} for _, m in ipairs(ms) do o[#o + 1] = m.name end return andList(o, 3) end
  local function verb(ms)   -- « va vers » si l'un suit une étape / une zone TreeAI, sinon « est dans »
    for _, m in ipairs(ms) do
      if m.addon and (m._hsrc == "step" or m._hsrc == "pick") then return #ms > 1 and "vont vers" or "va vers" end
    end
    return #ms > 1 and "sont dans" or "est dans"
  end
  local side = { "toi" }
  for _, m in ipairs(who[myH] or {}) do side[#side + 1] = m.name end
  local mySide = andList(side, 4) .. ((mySrc == "step") and " vers " or " dans ")
  local other = (group and group ~= myH) and group or nil
  if not other then
    for _, h in ipairs(order) do if h ~= myH and #who[h] > 0 then other = h break end end
  end
  if not other or #who[other] == 0 then return nil end
  return { text = fmt("%s %s %s, %s%s", names(who[other]), verb(who[other]), q(mapName(other)), mySide, q(mapName(myH))),
    group = other, ref = { name = who[other][1].name }, mine = myH, follow = (group ~= nil and group ~= myH) }
end

--- adopter la zone du groupe (zone de travail du trajet), puis la boussole vers le membre / le lieu de référence s'il est loin
function J.Adopt(zone, ref)
  if inCombat() then Grp:Say("Hors combat seulement") return false end
  zone = tonumber(zone)
  local done = false
  local Z = T.ZonePlan
  if zone and type(Z) == "table" and type(Z.SetMode) == "function" and active(Z) then
    call(Z.SetMode, Z, "auto")
    call(Z.SetMode, Z, "zone", zone)
    done = true
  end
  local me = J.Me()
  if type(ref) == "table" then
    local target = ref.name and destOf(ref.name) or ref.place
    if me and target and (E.Dist(me, target) or huge) > 150 then
      if ref.name then J.Start(ref.name, "fast", "Rejoindre " .. ref.name)
      else
        JS.spots["@group"] = target
        J.Start("@group", "fast", "Rejoindre le groupe" .. (ref.label and (" : " .. cut(ref.label, 30)) or ""))
      end
    end
  end
  if done then Grp:Say("Ton trajet suit le groupe : " .. q(mapName(zone) or "zone")) end
  return done or JS.active ~= nil
end

--- « Suivre le groupe » : l'intention déduite d'abord, sinon la zone de la majorité / du chef
function J.Follow(list)
  list = list or Grp:List()
  local it = JS.intent
  if it and it.conf >= 0.45 and FOLLOW_KIND[it.kind] then
    if it.kind == "z" then
      return J.Adopt(it.zone, (it.targetWho and { name = it.targetWho }) or (it.target and { place = it.target, label = it.name }) or nil)
    end
    if it.kind == "dg" then
      local D = T.Dungeon
      if type(D) == "table" and type(D.SetPlanned) == "function" and it.dungeon then call(D.SetPlanned, it.dungeon.key) end
    end
    if it.target then
      JS.spots["@group"] = it.target
      return J.Start("@group", "fast", "Rejoindre le groupe : " .. cut(it.targetName or it.name, 34))
    end
    Grp:Say("Donjon prévu : " .. cut(it.name, 34))
    return true
  end
  local c = J.Conflict(list)
  if c and c.follow then return J.Adopt(c.group, c.ref) end
  Grp:Say("Tu suis déjà le groupe")
  return false
end

--- « Partager mon trajet » : zone de travail + arrêt en cours (les amis TreeAI peuvent le suivre d'un clic)
function J.ShareRoute()
  if inCombat() then Grp:Say("Hors combat seulement") return false end
  local t = now()
  if t - JS.lastShare < SHARE_GAP then Grp:Say("Trajet déjà partagé il y a un instant") return false end
  local me
  for _, m in ipairs(Grp:List()) do if m.me then me = m end end
  local zone = me and J.Heading(me)
  if not zone then Grp:Say("Pas de trajet à partager") return false end
  JS.lastShare = t
  local P = T.Planner
  local s = type(P) == "table" and type(P.steps) == "table" and P.cursor and P.steps[P.cursor] or nil
  local label = (type(s) == "table" and type(P.StepLabel) == "function") and call(P.StepLabel, P, s) or ""
  local sp = (type(s) == "table" and s.map and s.x) and fmt("%d.%d.%d", s.map, floor(s.x * 10 + 0.5), floor(s.y * 10 + 0.5)) or ""
  Grp:QueueEvent("plan", tostring(zone), label or "", type(s) == "table" and tostring(s.q or "") or "", sp)
  Grp:Say("Trajet partagé avec le groupe : " .. q(mapName(zone) or "zone"))
  return true
end

function J.OnPlan(m, a)
  local zone = tonumber(a[2])
  if not zone or zone < 1 or zone > 100000 then return end
  zone = zoneOf(zone)
  local map, x, y = parseStepPos(a[5])
  local p = map and worldPct(map, x, y) or nil
  local plan = { zone = zone, label = clean(a[3], 40), q = tonumber(a[4]), place = p, at = now() }
  JS.plans[m.name] = plan
  local zn = mapName(zone) or "sa zone"
  Grp:Offer("plan:" .. m.name, "plan", fmt("%s partage son trajet : %s%s — clic : suivre", m.name, q(zn),
    plan.label and (" · " .. cut(plan.label, 30)) or ""), 1, { ttl = PLAN_TTL,
      onClick = function() J.Adopt(zone, p and { place = p, label = plan.label } or nil) end,
      tooltip = { "Trajet TreeAI de " .. m.name, "Clic : ton trajet passe dans la même zone (Trajet → Zone : Auto pour revenir)" } })
  Grp:Dirty(true)
end

-- ------------------------------------------------------------------ vote léger (fil GRP, clics seulement)
local function choiceLabel(c)
  if c.k == "d" then local d = dungeonByKey(c.v) return d and d.n or tostring(c.v) end
  return mapName(c.v) or ("zone " .. tostring(c.v))
end
--- choix : zones où va le groupe + les plus fournies à mon niveau (ZonePlan), donjons à portée de TOUS (pas déjà faits)
function J.VoteChoices(list)
  local out, seen = {}, {}
  local function addZ(z)
    z = tonumber(z)
    if z and not seen["z" .. z] and #out < 2 and mapName(z) then seen["z" .. z] = true; out[#out + 1] = { k = "z", v = z } end
  end
  for _, m in ipairs(list) do if m.online ~= false then addZ((J.Heading(m))) end end
  local Z = T.ZonePlan
  if #out < 2 and type(Z) == "table" and type(Z.ZoneChoices) == "function" and active(Z) then
    for _, c in ipairs(call(Z.ZoneChoices, Z) or {}) do if (c.n or 0) >= 3 then addZ(c.map) end end
  end
  local lo, hi = 99, 0
  for _, m in ipairs(list) do
    local lv = tonumber(m.level)
    if lv and m.online ~= false then lo, hi = min(lo, lv), max(hi, lv) end
  end
  local D = T.Dungeon
  local dl = {}
  if lo <= hi then
    for _, d in ipairs(dungeons()) do
      if (d.faction == nil or d.faction == faction()) and lo >= (d.min or 1) - 1 and hi <= (d.max or 60) + 2 then
        local doneD = type(D) == "table" and type(D.IsDone) == "function" and call(D.IsDone, d.key)
        if not doneD then dl[#dl + 1] = d end
      end
    end
  end
  local mid = (lo + hi) / 2
  tsort(dl, function(a, b)
    local da, db = abs((a.min + a.max) / 2 - mid), abs((b.min + b.max) / 2 - mid)
    if da ~= db then return da < db end
    return a.key < b.key
  end)
  for i = 1, min(2, #dl) do out[#out + 1] = { k = "d", v = dl[i].key } end
  for _, c in ipairs(out) do c.label = choiceLabel(c) end
  return out
end

local function voters(list)
  local out = { myName() }
  for _, m in ipairs(friends(list, true)) do out[#out + 1] = m.name end
  return out
end

function J.StartVote(list)
  if inCombat() then Grp:Say("Hors combat seulement") return false end
  list = list or Grp:List()
  local t = now()
  if #friends(list, true) == 0 then Grp:Say("Personne n'a TreeAI dans le groupe : demandez-vous de vive voix") return false end
  if JS.vote and not JS.vote.closed then Grp:Say("Un vote est déjà en cours") return false end
  if t - JS.lastVote < VOTE_GAP then Grp:Say("Un vote vient d'avoir lieu") return false end
  local ch = J.VoteChoices(list)
  if #ch < 2 then Grp:Say("Rien à voter : une seule suite possible") return false end
  JS.lastVote = t
  JS.seq = (JS.seq % 999) + 1
  JS.vote = { id = JS.seq, by = myName(), at = t, choices = ch, ballots = {} }
  local codes = {}
  for _, c in ipairs(ch) do codes[#codes + 1] = c.k .. tostring(c.v) end
  Grp:QueueEvent("vote", tostring(JS.seq), codes[1], codes[2], codes[3] or "", codes[4] or "")
  Grp:Say("Vote lancé : choisis ta suite dans la page Groupe")
  Grp:Dirty(true)
  return true
end

function J.OnVote(m, a)
  local id = tonumber(a[2])
  if not id or (JS.vote and not JS.vote.closed and JS.vote.by == myName()) then return end
  local ch = {}
  for i = 3, 6 do
    local k, v = tostring(a[i] or ""):match("^([zd])(%w+)$")
    if k == "z" and tonumber(v) and tonumber(v) >= 1 and tonumber(v) <= 100000 then ch[#ch + 1] = { k = "z", v = tonumber(v) }
    elseif k == "d" and #v <= 12 and dungeonByKey(v) then ch[#ch + 1] = { k = "d", v = v } end
  end
  if #ch < 2 then return end
  for _, c in ipairs(ch) do c.label = choiceLabel(c) end
  JS.vote = { id = id, by = m.name, at = now(), choices = ch, ballots = {} }
  Grp:Offer("vote", "vote", fmt("%s lance un vote : la suite du groupe — clic : voter", m.name), 2, { ttl = VOTE_TTL,
    sound = true, soundKind = "group", onClick = function() Grp:OpenPage() end,
    tooltip = { "Vote de " .. m.name .. " (amis TreeAI)", "Clic : la page Groupe, une ligne par choix" } })
  Grp:Dirty(true)
end

function J.Ballot(i)
  local v = JS.vote
  if not (v and not v.closed and v.choices[i]) then return false end
  v.ballots[myName()] = i
  Grp:QueueEvent("ballot", tostring(v.id), tostring(i))
  Grp:Withdraw("vote")
  J.VoteTick(now())
  Grp:Dirty(true)
  return true
end

function J.OnBallot(m, a)
  local v = JS.vote
  local id, i = tonumber(a[2]), tonumber(a[3])
  if not (v and id == v.id and i and v.choices[i]) or v.closed then return end
  v.ballots[m.name] = i
  J.VoteTick(now())
  Grp:Dirty(true)
end

function J.VoteTick(t)
  local v = JS.vote
  if not v then return end
  if v.closed then
    if t - (v.closedAt or t) > VOTE_KEEP then JS.vote = nil end
    return
  end
  local n, best, tie, waiting = E.Tally(v, voters(Grp:List()))
  if #waiting == 0 or t - v.at > VOTE_TTL then
    v.closed, v.closedAt, v.result, v.tie = true, t, (not tie) and best or nil, tie
    v.count = best and n[best] or 0
    Grp:Withdraw("vote")
    if v.result then
      local c = v.choices[v.result]
      Grp:Offer("voted", "news", fmt("Vote : %s (%d voix) — clic : suivre", c.label, v.count), 2, { ttl = 300,
        onClick = function() J.AdoptChoice(c) end, tooltip = { "Résultat du vote du groupe", "Clic : suivre ce choix" } })
    elseif best then
      Grp:Offer("voted", "news", "Vote : égalité — à vous de choisir (page Groupe)", 1, { ttl = 120 })
    end
  end
end

function J.AdoptChoice(c)
  if not c then return false end
  if c.k == "d" then
    local D = T.Dungeon
    if type(D) == "table" and type(D.SetPlanned) == "function" then call(D.SetPlanned, c.v) end
    local ep = J.Entrance(c.v)
    if ep then
      JS.spots["@group"] = ep
      return J.Start("@group", "fast", "Rejoindre le groupe : " .. (ep.approx and "l'entrée (≈) de " or "l'entrée de ") .. cut(c.label, 26))
    end
    Grp:Say("Donjon prévu : " .. cut(c.label, 34))
    return true
  end
  local ref
  for _, m in ipairs(Grp:List()) do if not m.me and m._where and m._where.zone == c.v then ref = { name = m.name } break end end
  return J.Adopt(c.v, ref)
end

-- ------------------------------------------------------------------ cycle (appelé par Group:Tick, 2 s, hors combat)
function J.Tick(t, list)
  J.Sample(t, list)
  J.Progress()
  J.VoteTick(t)
  local r = JS.rdv
  if r then   -- rendez-vous fini : 10 min passées, ou au moins deux d'entre vous sur place depuis 1 min
    local near = JS.near.rdv
    if near and #near.at >= 2 then r.metAt = r.metAt or t else r.metAt = nil end
    if t - r.at > RDV_TTL or (r.metAt and t - r.metAt > 60) then
      JS.rdv = nil
      Grp:Withdraw("rdv")
      if JS.active and JS.active.who == "@rdv" then J.Stop() end
    end
  end
  for name, p in pairs(JS.plans) do if t - p.at > PLAN_TTL then JS.plans[name] = nil end end
  for i = #JS.chat, 1, -1 do if t - JS.chat[i].at > 1800 then tremove(JS.chat, i) end end
end

-- entrée apprise : un ami TreeAI passe d'un lieu dehors (m + ps) à la zone d'un donjon
function J.OnZone(oldMap, oldP, newZone)
  local d = dungeonByName(newZone)
  if not (d and tonumber(oldMap) and oldP) then return false end
  local x, y = parsePos(oldP)
  return x and J.LearnEntrance(d.key, oldMap, x, y) or false
end
-- la mienne : à l'entrée dans une instance, ma dernière position dehors (≤ 40 s)
function J.OnWorld()
  if not inInstance() then return false end
  local d = dungeonByName(clean(pub((call(GetRealZoneText))), 40))
  local s = JS.samples[myName()]
  local last = s and s[#s]
  if d and last and last.map and last.x and now() - last.t <= 40 then return J.LearnEntrance(d.key, last.map, last.x, last.y) end
  return false
end

-- ------------------------------------------------------------------ lignes de la page Groupe
-- target = « Bravo (Les Tarides) » / « le groupe : l'entrée de … » ; deux lignes : au plus vite, rentable (s'il y a lieu)
local function joinRows(out, who, target, dest, me, key, destName)
  local r = J.RouteTo(key, me, dest, true, true)
  local f = r and r.fast
  if not f then return end
  local label = "Rejoindre " .. target
  destName = destName or target
  local tipF = { "Le plus rapide : " .. MODE_FR[f.mode] .. " (" .. E.Min(f.sec) .. ")" }
  for _, l in ipairs(E.LegLines(f.legs, destName)) do tipF[#tipF + 1] = l end
  tipF[#tipF + 1] = "Temps : ta vitesse mesurée, tes vols connus"
  out[#out + 1] = { text = label .. " : au plus vite", right = MODE_FR[f.mode] .. " · " .. E.Min(f.sec),
    prio = 1, kind = "group", tooltip = tipF, onClick = function() J.Start(who, "fast", label) end, clickHint = "Clic : la boussole y va" }
  local p = r.profit
  if p then
    local bits = { E.Extra(p.extra) }
    if p.turnins > 0 then bits[#bits + 1] = fmt("%d rendu%s", p.turnins, p.turnins > 1 and "s" or "") end
    if p.objs > 0 then bits[#bits + 1] = fmt("%d objectif%s", p.objs, p.objs > 1 and "s" or "") end
    if p.xp > 0 then bits[#bits + 1] = fmt("%s+%s XP", p.approx and "≈ " or "", numText(p.xp)) end
    local tipP = { fmt("Même arrivée en %s : des quêtes en passant (≤ 20 %% de temps en plus)", E.Min(p.total)) }
    for _, l in ipairs(E.LegLines(f.legs, destName, p.stops)) do tipP[#tipP + 1] = l end
    if p.approx and p.xp > 0 then tipP[#tipP + 1] = "≈ : XP en partie estimée (le jeu ne l'a pas encore donnée)" end
    out[#out + 1] = { text = "Rentable : " .. tconcat(bits, " · "), right = E.Min(p.total), prio = 1, kind = "group", tooltip = tipP,
      onClick = function() J.Start(who, "profit", label) end, clickHint = "Clic : la boussole passe par ces quêtes" }
  end
end

--- sections de la page : { intent = {…}, group = {…}, join = {…} } ; shown = page Groupe à l'écran
function J.Sections(list, shown, t)
  local res = { intent = {}, group = {}, join = {} }
  if inCombat() then return JS.lastSections or res end
  for _, m in ipairs(list) do m._where = (m.online ~= false) and J.Where(m) or nil end
  -- 1. l'intention déduite : une ligne claire ; annoncée (« Maintenant ») quand elle change et que je n'y suis pas
  local it = J.Infer(list, t)
  if it then
    local tip = {}
    for _, l in ipairs(it.tip) do tip[#tip + 1] = l end
    local ok = aligned(it, list)
    local canFollow = (not ok) and FOLLOW_KIND[it.kind]
    tip[#tip + 1] = ok and "Tu y es déjà" or (canFollow and "Clic : suivre le groupe" or nil)
    res.intent[1] = { text = it.text, prio = ok and 1 or 2, kind = "group", tooltip = tip,
      onClick = canFollow and function() J.Follow() end or nil, clickHint = canFollow and "Clic : suivre le groupe" or nil }
    if JS.lastIntentKey ~= it.key then
      JS.lastIntentKey = it.key
      if canFollow and it.conf >= 0.6 and it.explicit then
        Grp:Offer("intent", "intent", it.text, 1, { ttl = 120, sound = true, soundKind = "group",
          onClick = function() J.Follow() end, tooltip = tip })
      else
        Grp:Withdraw("intent")
      end
    end
  elseif JS.lastIntentKey then
    JS.lastIntentKey = nil
    Grp:Withdraw("intent")
  end
  -- 2. conflit de zones
  local c = J.Conflict(list)
  JS.conflict = c
  if c then
    res.group[#res.group + 1] = { text = c.text, prio = c.follow and 2 or 1, kind = "group",
      tooltip = c.follow and { "Où va chacun : étape TreeAI, zone choisie, sinon zone actuelle", "Clic : suivre le groupe (zone du trajet)" }
        or { "Où va chacun : étape TreeAI, zone choisie, sinon zone actuelle", "« Partager mon trajet » pour qu'il te suive d'un clic" },
      onClick = c.follow and function() J.Adopt(c.group, c.ref) end or nil, clickHint = c.follow and "Clic : suivre le groupe" or nil }
  end
  -- 3. vote
  local v = JS.vote
  if v then
    local n, _, _, waiting = E.Tally(v, voters(list))
    local first = #res.group + 1
    if not v.closed then
      local mine = v.ballots[myName()]
      for i, ch in ipairs(v.choices) do
        res.group[#res.group + 1] = { text = fmt("%s %s (%d voix)", mine == i and "Ton vote :" or "Voter :", ch.label, n[i] or 0),
          prio = mine and 1 or 2, kind = "group", onClick = (not mine) and function() J.Ballot(i) end or nil,
          clickHint = (not mine) and "Clic : voter pour ce choix" or nil,
          tooltip = { "Vote de " .. (v.by == myName() and "toi" or v.by), "En attente : " .. (#waiting > 0 and andList(waiting, 4) or "personne") } }
      end
    else
      local bits = {}
      for i, ch in ipairs(v.choices) do bits[#bits + 1] = fmt("%s %d", ch.label, n[i] or 0) end
      local win = v.result and v.choices[v.result]
      res.group[#res.group + 1] = { text = win and fmt("Vote : %s gagne (%s)", win.label, tconcat(bits, " · ")) or ("Vote : égalité (" .. tconcat(bits, " · ") .. ")"),
        prio = 1, kind = "group", onClick = win and function() J.AdoptChoice(win) end or nil, clickHint = win and "Clic : suivre ce choix" or nil,
        tooltip = { "Résultat du vote", win and "Clic : suivre ce choix" or "À vous de choisir" } }
    end
    local noTAI = {}
    for _, m in ipairs(list) do if not m.me and m.online ~= false and m.addon ~= "4" then noTAI[#noTAI + 1] = m.name end end
    if #noTAI > 0 then
      for i = first, #res.group do
        local tt = res.group[i].tooltip
        tt[#tt + 1] = "Ne votent pas (sans TreeAI 4) : " .. andList(noTAI, 3)
      end
    end
  end
  -- 4. trajets partagés reçus
  local pnames = {}
  for name in pairs(JS.plans) do pnames[#pnames + 1] = name end
  tsort(pnames)
  for _, name in ipairs(pnames) do
    local p = JS.plans[name]
    if t - p.at < PLAN_TTL then
      res.group[#res.group + 1] = { text = fmt("Trajet de %s : %s%s", name, q(mapName(p.zone) or "sa zone"), p.label and (" · " .. cut(p.label, 30)) or ""),
        prio = 1, kind = "group", onClick = function() J.Adopt(p.zone, p.place and { place = p.place, label = p.label } or nil) end,
        clickHint = "Clic : suivre ce trajet", tooltip = { "Trajet TreeAI partagé par " .. name, "Clic : le suivre (zone du trajet)" } }
    end
  end
  -- 5. donjon prévu : prêt ?
  local D = T.Dungeon
  if active(D) and type(D.Planned) == "function" and type(D.Checklist) == "function" and #list >= 2 then
    local d = call(D.Planned)
    if type(d) == "table" and d.key then
      if not JS.dgAt or t - JS.dgAt > 30 or JS.dgKey ~= d.key then
        local cl = call(D.Checklist, d) or {}
        local bad, warn, first = 0, 0, nil
        for _, x in ipairs(cl) do
          if x.state == "bad" then bad = bad + 1; first = first or x.text elseif x.state == "warn" then warn = warn + 1 end
        end
        if not first then for _, x in ipairs(cl) do if x.state == "warn" then first = x.text break end end end
        JS.dgLine, JS.dgAt, JS.dgKey = { bad = bad, warn = warn, first = first }, t, d.key
      end
      local dl = JS.dgLine
      res.group[#res.group + 1] = { text = (dl.bad + dl.warn == 0) and fmt("Donjon prévu : %s — prêt", d.n)
        or fmt("Donjon prévu : %s — %s", d.n, cut(dl.first or "à préparer", 60)), prio = dl.bad > 0 and 2 or 1, kind = "group",
        tooltip = { "Liste de la page Donjons (niveaux, rôles, réparation, sacs, munitions)", "Clic : la page Donjons" },
        onClick = function() if T.Window and T.Window._active and T.Window.Open then T.Window.Open("dungeon") end end }
    end
  end
  -- 6. « Rejoindre » (page à l'écran, ou trajet en cours)
  if shown or JS.active then
    local me
    for _, m in ipairs(list) do if m.me then me = m._where end end
    local a = JS.active
    if a then
      res.join[#res.join + 1] = { text = "En route : " .. ((a.who == "@rdv") and "point de rencontre" or lcfirst(a.label or a.who):gsub("^rejoindre ", "")),
        right = a.left and E.Min(a.left) or nil, prio = 1, kind = "group", onClick = function() J.Stop("Trajet vers le groupe arrêté") end,
        clickHint = "Clic : arrêter", tooltip = { a.mode == "profit" and "Rentable (quêtes en passant)" or "Au plus vite", "Clic : arrêter" } }
    end
    if me and shown then
      local groupWho = (it and not aligned(it, list) and it.targetWho) or nil
      if it and it.target and not it.targetWho and not aligned(it, list) and (E.Dist(me, it.target) or huge) > NEAR_YD then
        JS.spots["@group"] = it.target
        joinRows(res.join, "@group", "le groupe (" .. cut(it.target.approx and it.name or (it.targetName or it.name), 48) .. ")", it.target, me,
          "@group", it.targetName or it.name)
      end
      for _, m in ipairs(list) do
        if not m.me and m.online ~= false then
          local p, zn = m._where, nil
          if not p then _, zn = J.Where(m) end
          local st = m.addon and m.st
          if st and st.j == myName() then
            local left = max(60, (tonumber(st.je) or 0) - (t - (m.jeAt or t)))
            res.join[#res.join + 1] = { text = fmt("%s arrive vers toi : ≈ %s — attends-le", m.name, E.Min(left)), prio = 2, kind = "group",
              tooltip = { m.name .. " a lancé « Rejoindre » vers toi (TreeAI)", "Temps estimé par son jeu" } }
          elseif m.dead or m.ghost then
            if p and (E.Dist(me, p) or 0) > NEAR_YD then
              res.join[#res.join + 1] = { text = fmt("%s est mort : attends qu'il revienne", m.name), prio = 1, kind = "group" }
            end
          elseif p then
            local d = E.Dist(me, p)
            if d and d > NEAR_YD then
              local r = J.RouteTo("m:" .. m.name, me, p, true, true)   -- même cache que ses lignes « Rejoindre »
              local v2, eta = J.ApproachTo(m.name, me)
              if v2 and eta and r and r.fast and eta <= r.fast.sec + 30 then
                res.join[#res.join + 1] = { text = fmt("%s se rapproche : ≈ %s — attends-le", m.name, E.Min(eta)), prio = 1, kind = "group",
                  tooltip = { "Sa position se rapproche de la tienne depuis 30 s ou plus", "Toi vers lui : " .. E.Min(r.fast.sec) } }
              else
                local zn2 = (p.zoneName and me.zone ~= p.zone) and p.zoneName or nil
                joinRows(res.join, m.name, (groupWho == m.name) and ("le groupe (" .. m.name .. (zn2 and (", " .. zn2) or "") .. ")")
                  or (m.name .. (zn2 and (" (" .. zn2 .. ")") or "")), p, me, "m:" .. m.name, m.name)
              end
            elseif not d then
              res.join[#res.join + 1] = { text = fmt("%s est sur un autre continent%s", m.name, p.zoneName and (" (" .. p.zoneName .. ")") or ""),
                prio = 1, kind = "group", tooltip = { "Pas de trajet calculé d'un continent à l'autre sans transport connu" } }
            end
          else
            res.join[#res.join + 1] = { text = zn and fmt("%s est dans %s : position exacte inconnue", m.name, q(zn)) or fmt("%s : position inconnue", m.name),
              prio = 1, kind = "group", tooltip = { m.addon and "Son jeu ne partage pas sa position (instance, chargement)"
                or "Sans TreeAI : le jeu ne donne que ce qu'il montre (hors de vue, instance)" } }
          end
        end
      end
      local spot = J.MeetingSpot(list)
      JS.spot = spot
      if spot then
        local r = J.RouteTo("spot", me, spot.place, false, true)
        local tip = { fmt("Le plus juste pour tous : le plus loin en est à %s (%s)", distText(spot.far), tostring(spot.farName)) }
        if spot.kind == "hub" then tip[#tip + 1] = "Prochain arrêt commun : vous avez tous quelque chose à y faire" end
        local v4 = #friends(list, true) > 0
        tip[#tip + 1] = v4 and "Clic : proposer au groupe (les amis TreeAI ont « J'y vais »)" or "Clic : la boussole y va (dis-le à tes amis : ils n'ont pas TreeAI)"
        res.join[#res.join + 1] = { text = fmt("Point de rencontre : %s%s", spot.kind == "fm" and "maître de vol de " or "", cut(spot.label, 34)),
          right = r and r.fast and ("toi " .. E.Min(r.fast.sec)) or nil, prio = 1, kind = "group", tooltip = tip,
          onClick = function()
            if v4 then J.Propose(spot)
            else
              JS.rdv = { id = 0, by = myName(), mine = true, place = spot.place, label = spot.label, at = now(), goers = {} }
              J.Go()
            end
          end, clickHint = v4 and "Clic : proposer au groupe" or "Clic : y aller" }
      end
      local rv = JS.rdv
      if rv and t - rv.at < RDV_TTL and rv.place and (rv.id or 0) > 0 then
        local goers, waiting, tip = {}, 0, { "Point de rencontre de " .. (rv.by == myName() and "toi" or rv.by) }
        for _, m in ipairs(list) do
          if not m.me and m.online ~= false then
            local g = rv.goers[m.name]
            if m.name == rv.by then tip[#tip + 1] = m.name .. " : l'a proposé"
            elseif g then
              local eta = g.eta and E.Min(max(60, g.eta - (t - g.at))) or nil
              goers[#goers + 1] = m.name .. (eta and (" (≈ " .. eta .. ")") or "")
              tip[#tip + 1] = m.name .. " : y va" .. (eta and (", ≈ " .. eta) or "")
            elseif m.addon == "4" then waiting = waiting + 1; tip[#tip + 1] = m.name .. " : pas encore répondu"
            else waiting = waiting + 1; tip[#tip + 1] = m.name .. " : sans TreeAI (dis-le-lui)" end
          end
        end
        local going = rv.goers[myName()] ~= nil or (JS.active and JS.active.who == "@rdv")
        tip[#tip + 1] = going and "Tu y vas" or "Clic : j'y vais"
        local who = #goers > 0 and (andList(goers, 2) .. (#goers > 1 and " y vont" or " y va")) or "personne n'a encore répondu"
        res.join[#res.join + 1] = { text = fmt("Rendez-vous %s : %s%s", cut(rv.label or "", 28), who,
          (#goers > 0 and waiting > 0) and fmt(" · %d sans réponse", waiting) or ""), prio = 1, kind = "group",
          onClick = (not going) and function() J.Go() end or nil, clickHint = (not going) and "Clic : j'y vais" or nil, tooltip = tip }
      end
    end
  end
  JS.lastSections = res
  return res
end

--- boutons de la page (barre du haut)
function J.Controls(list)
  local out = {}
  local v4 = #friends(list, true) > 0
  local c = JS.conflict
  local it = JS.intent
  if (c and c.follow) or (it and it.conf >= 0.45 and FOLLOW_KIND[it.kind] and not aligned(it, list)) then
    out[#out + 1] = { text = "Suivre le groupe", label = "Suivre le groupe", control = true, prio = 1,
      tooltip = { "Ce que le groupe veut faire (déduit) ou la zone de la majorité / du chef",
        "Ton trajet passe dans cette zone ; la boussole va vers eux s'ils sont loin" },
      onClick = function() J.Follow() end }
  end
  if v4 then
    out[#out + 1] = { text = "Partager mon trajet", label = "Partager mon trajet", control = true, prio = 1,
      tooltip = { "Envoie ta zone de travail et ton arrêt en cours aux amis TreeAI", "Ils peuvent le suivre d'un clic" },
      onClick = function() J.ShareRoute() end }
    out[#out + 1] = { text = "Voter la suite", label = "Voter", control = true, prio = 1,
      tooltip = { "Vote léger : zones et donjons à votre niveau", "Chaque ami TreeAI clique son choix (rien dans le chat)" },
      onClick = function() J.StartVote() end }
  end
  return out
end

--- distance (yd) entre moi et un membre, positions vraies seulement (jeu ou ami TreeAI) ; nil = inconnue
function J.DistTo(m)
  if inCombat() or not m or m.me then return nil end
  local mp = m._where or J.Where(m)
  local me = J.Me()
  return mp and me and E.Dist(me, mp) or nil, mp
end
J.DistText = distText

--- mot de la carte d'un membre (« vient vers toi »)
function J.StateWords(m)
  local st = m.addon and m.st
  if st and st.j and st.j ~= "" then
    if st.j == myName() then return "vient vers toi" end
    if st.j == "@rdv" then return "va au point de rencontre" end
    if st.j == "@group" then return "rejoint le groupe" end
    return "rejoint " .. st.j
  end
  return nil
end

--- résumé pour l'appli (intention dérivée seulement : jamais de texte du chat)
function J.Summary()
  local it = JS.intent
  if not it then return nil end
  return { text = it.text, conf = floor(it.conf * 100 + 0.5), kind = it.kind }
end

function J.Reset()
  JS.cache, JS.samples, JS.chat, JS.plans, JS.placeEv, JS.near, JS.spots = {}, {}, {}, {}, {}, {}, {}
  JS.active, JS.rdv, JS.vote, JS.intent, JS.conflict, JS.spot, JS.spotCache = nil, nil, nil, nil, nil, nil, nil
  JS.lastSections, JS.lastIntentKey, JS.dgAt, JS.placeKey, JS.placeMoving = nil, nil, nil, nil, nil
  JS.lastSample = -1e9
end
