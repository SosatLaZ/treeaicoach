-- TreeAI — « en clair » (agent SIMPLE) : transforme les calculs (modèle de coût, verdicts, routes) en phrases
-- de tous les jours. Une seule règle : le joueur lit UNE action, verbe en premier, avec le lieu et le temps en
-- mots simples (« Va tuer 6 Loups au nord (≈ 3 min) », « Rends « Sang de lion » à Dughan (≈ 1 min) »).
-- Jamais d'XP/min, de score, de coefficient ni de % (sauf une progression) dans l'interface principale ; les
-- infobulles gardent au plus 1–2 nombres simples (minutes, XP). Pur (aucun appel WoW) : testé seul.
-- API : TreeAI.Plain.{Minutes, Dir, Where, Plural, ObjAction, Task, Tag, Why, RouteName, Reason, IsPlain}.
-- Lua 5.1, aucune globale.
local T = _G.TreeAI
if not T then return end

local P = {}
T.Plain = P

local floor, pi = math.floor, math.pi

local function trim(s) return (tostring(s or ""):gsub("^%s+", ""):gsub("%s+$", "")) end
local cut = T.Cut   -- (4.0) Core : n caractères UTF-8 (« … » compris), jamais une lettre accentuée coupée
P.Cut = cut

-- « ≈ 3 min » (jamais moins d'1 min, au-delà d'une heure « ≈ 1 h 20 »)
function P.Minutes(sec)
  sec = tonumber(sec)
  if not sec or sec <= 0 then return nil end
  local m = floor(sec / 60 + 0.5)
  if m < 1 then m = 1 end
  if m < 60 then return "≈ " .. m .. " min" end
  local h = floor(m / 60)
  local r = m - h * 60
  return r >= 5 and ("≈ %d h %02d"):format(h, r) or ("≈ " .. h .. " h")
end

-- cap (radians, 0 = nord, sens anti-horaire comme la boussole) → « au nord », « à l'est »…
local DIRS = { "au nord", "au nord-ouest", "à l'ouest", "au sud-ouest", "au sud", "au sud-est", "à l'est", "au nord-est" }
local LETTER = { N = "au nord", NE = "au nord-est", E = "à l'est", SE = "au sud-est", S = "au sud", SO = "au sud-ouest",
                 O = "à l'ouest", NO = "au nord-ouest", SW = "au sud-ouest", W = "à l'ouest", NW = "au nord-ouest" }
function P.Dir(bearing)
  if type(bearing) == "string" then return LETTER[bearing] end
  bearing = tonumber(bearing)
  if not bearing then return nil end
  local i = floor(((bearing % (2 * pi)) + pi / 8) / (pi / 4)) % 8
  return DIRS[i + 1]
end

-- « à Hurlevent » / « vers Forêt d'Elwynn » (pas d'article à deviner)
function P.InZone(zone)
  zone = trim(zone)
  if zone == "" or zone == "?" then return nil end   -- (3.9.9) zone inconnue (Quests : "?") : rien plutôt que « à ? »
  if not zone:find(" ", 1, true) and not zone:find("'", 1, true) then return "à " .. zone end
  return "vers " .. zone
end

-- Lieu en mots simples : dist (yards), bearing, zone de la cible, zone actuelle.
function P.Where(dist, bearing, zone, here)
  dist = tonumber(dist)
  local dir = P.Dir(bearing)
  if zone and here and zone ~= "" and zone ~= here and (not dist or dist > 1200) then return P.InZone(zone) end
  if not dist then return dir end
  if dist < 40 then return "juste à côté" end
  if dist < 200 then return dir and ("tout près, " .. dir) or "tout près" end
  if dist > 1200 then return dir and (dir .. ", assez loin") or "assez loin" end
  return dir
end

-- pluriel simple des mots avant « de / des / du / d' » (« Jeune loup » → « Jeunes loups », « Peau de loup » →
-- « Peaux de loup », « Loup des bois » → « Loups des bois ») ; les noms propres après le 1er mot restent.
local STOP = { de = true, des = true, du = true, ["d'"] = true, ["à"] = true, au = true, aux = true, en = true,
               la = true, le = true, les = true, et = true }
local function plWord(w)
  if w:find("[sxz]$") then return w end
  if w:find("eau$") or w:find("eu$") then return w .. "x" end
  if w:find("al$") and #w > 3 then return w:sub(1, -3) .. "aux" end
  return w .. "s"
end
function P.Plural(name, n)
  name = trim(name)
  if (tonumber(n) or 2) < 2 or name == "" then return name end
  local out, stop, i = {}, false, 0
  for w in name:gmatch("%S+") do
    i = i + 1
    local lw = w:lower()
    if STOP[lw] or lw:find("^d'") or lw:find("^l'") then stop = true end
    if not stop and (i == 1 or not w:find("^%u")) and not w:find("%d") then w = plWord(w) end
    out[#out + 1] = w
  end
  return table.concat(out, " ")
end

-- « Trouver la ferme » → « Trouve la ferme » ; « Aller à … » → « Va à … »
local function imperative(text)
  text = trim(text):gsub("%s*:%s*%d+%s*/%s*%d+%s*$", "")
  local first, rest = text:match("^(%S+)(.*)$")
  if not first then return nil end
  local lf = first:lower()
  if lf == "aller" then return "Va" .. rest end
  if lf == "parler" then return "Parle" .. rest end
  if lf:find("er$") and #lf > 3 then return first:sub(1, 1):upper() .. first:sub(2, -2) .. rest end
  return nil
end

local function mobName(s)
  return (trim(s):gsub("%s+tu[ée]+e?s?$", ""):gsub("%s+slain$", ""):gsub("%s+killed$", ""))
end

-- Action d'un objectif : o = { name, have, need, t (m/i/o/e/r/x) }, e = estimation Tracker { mob, mobs, t }.
function P.ObjAction(o, e)
  if type(o) ~= "table" then return nil end
  e = type(e) == "table" and e or {}
  local name = trim(o.name or o.text)
  local need = tonumber(o.need) or 1
  local left = math.max(1, need - (tonumber(o.have) or 0))
  local t = o.t or e.t
  if name == "" then return nil end
  local killed = name:find("%stu[ée]+e?s?$") or name:find("%sslain$")
  if e.rate and e.mob and not killed then t = "i" end   -- objet connu comme butin (Tracker) : on le récupère
  if (t == "m" or killed) and t ~= "i" then
    local mob = mobName(name)
    if left == 1 then return "Va tuer " .. mob end
    return ("Va tuer %d %s"):format(left, P.Plural(mob, left))
  elseif t == "i" then
    local what = left == 1 and name or (left .. " " .. P.Plural(name, left))
    if e.mob and e.mob ~= "" then
      return ("Récupère %s sur les %s"):format(what, P.Plural(mobName(e.mob), 2))
    end
    return (left == 1 and "Trouve " or "Ramasse ") .. what
  elseif t == "o" then
    return left == 1 and ("Trouve " .. name) or ("Trouve %d %s"):format(left, P.Plural(name, left))
  elseif t == "r" then
    return "Gagne la confiance : " .. name
  end
  local imp = imperative(name)
  if imp then return imp end
  if need > 1 then return ("Encore %d : %s"):format(left, name) end
  return "Fais : " .. name
end

-- LA ligne « À faire maintenant ». x = { complete, title, npc, turnZone, zone, here, obj, est, dist, bearing, sec }
function P.Task(x)
  if type(x) ~= "table" then return nil end
  local title = cut(x.title or "?", 34)
  local where = P.Where(x.dist, x.bearing, x.complete and (x.turnZone or x.zone) or x.zone, x.here)
  local t = P.Minutes(x.sec)
  local s
  if x.complete then
    s = "Rends « " .. title .. " »" .. (x.npc and x.npc ~= "" and (" à " .. cut(x.npc, 24)) or "")
    if where and where ~= "juste à côté" and not where:find("^tout près") then s = s .. ", " .. where end
  else
    s = x.obj and P.ObjAction(x.obj, x.est) or nil
    if not s then s = "Fais « " .. title .. " »" end
    if where then s = s .. " " .. where end
  end
  if t then s = s .. " (" .. t .. ")" end
  return s
end

-- Étiquette d'une quête (UN mot) + une raison courte pour l'infobulle.
-- c = { complete, verdict = "keep"|"later"|"abandon", reason, hard, group, sec, mates }
P.TAG = { ready = "Prête à rendre", drop = "À retirer", group = "À plusieurs", fast = "Rapide", long = "Long" }
P.TAG_COLOR = { ready = "|cff33ff33", drop = "|cffff6050", group = "|cff66ccff", fast = "|cffffd100", long = "|cffaaaaaa" }
P.FAST_SEC = 600
function P.Tag(c)
  c = c or {}
  local key, why
  if c.complete then key, why = "ready", "Tu as tout fait : il ne reste qu'à la rendre."
  elseif c.verdict == "abandon" then key, why = "drop", c.reason and ("Elle ne vaut plus le coup : " .. c.reason .. ".") or "Elle ne vaut plus le coup."
  elseif c.hard then key, why = "group", "Trop dure seul : fais-la avec tes potes."
  elseif (tonumber(c.sec) or 1e9) <= P.FAST_SEC and c.verdict ~= "later" then
    key, why = "fast", "Vite faite" .. (P.Minutes(c.sec) and (" (" .. P.Minutes(c.sec) .. ")") or "") .. "."
  else
    key, why = "long", (c.verdict == "later" and c.reason) and ("Pas tout de suite : " .. c.reason .. ".")
      or ("Elle prend du temps" .. (P.Minutes(c.sec) and (" (" .. P.Minutes(c.sec) .. ")") or "") .. ".")
  end
  if (c.mates or 0) > 0 and key ~= "drop" then why = why .. " Tes potes l'ont aussi." end
  return P.TAG[key], why, key
end
function P.TagText(c)
  local word, _, key = P.Tag(c)
  return (P.TAG_COLOR[key] or "") .. word .. "|r"
end

-- « Pourquoi ? » en une phrase.
function P.Why(c, ctx)
  c, ctx = c or {}, ctx or {}
  if ctx.pinned then return "Tu l'as épinglée en premier." end
  if ctx.step then return "C'est la prochaine étape de ton trajet." end
  if ctx.urgent then return "À régler avant de continuer." end
  if c.complete then return "Elle est finie : rends-la pour toucher l'XP." end
  if (c.mates or 0) > 0 then return "Tes potes l'ont aussi : faites-la ensemble." end
  if c.hard then return "La meilleure quête, mais elle se fait mieux à plusieurs." end
  return "C'est la quête la plus rapide autour de toi."
end

-- noms des trajets (app TreeAI → plan) : 3 choix en mots simples
P.ROUTE = { xph = "Le plus rapide", auto = "Le plus rapide", clear = "Tout finir ici", squad = "Avec mes potes",
            safe = "Sans galère" }
function P.RouteName(id, title)
  local k = tostring(id or ""):gsub("%-.*$", "")
  return P.ROUTE[k] or (title and tostring(title)) or "Trajet"
end

-- Raison simple (mots, pas de nombres) d'une liste de raisons { {p = "trop facile maintenant"}, … }
function P.Reason(list, n)
  local out = {}
  for _, r in ipairs(list or {}) do
    local t = type(r) == "table" and r.p or nil
    if t and t ~= "" then
      local dup = false
      for _, x in ipairs(out) do if x == t then dup = true end end
      if not dup then out[#out + 1] = t end
    end
    if #out >= (n or 2) then break end
  end
  return table.concat(out, ", ")
end

-- Garde-fou : un texte de l'interface principale ne parle pas en jargon INTERNE (λ, score, /100, coefficient,
-- codes de debug). Les chiffres utiles au joueur (XP/min, XP/h, %, minutes) sont permis : il les aime.
P.FORBIDDEN = { "score", "λ", "/100", "coefficient", "×0" }
function P.IsPlain(text)
  local s = tostring(text or ""):gsub("|c%x%x%x%x%x%x%x%x", ""):gsub("|r", ""):gsub("|T.-|t", "")
  local low = s:lower()
  for _, w in ipairs(P.FORBIDDEN) do
    if low:find(w, 1, true) then return false, w end
  end
  return true
end

