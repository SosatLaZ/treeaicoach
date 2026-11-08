-- TreeAI — module « Groupe » (GROUPMERGE, 4.0) : UN seul module pour le groupe de 2 à 4 amis, avec ou sans TreeAI.
-- Il remplace Squad (cerveau de groupe), Missions (tableau des missions), Team (membres sans TreeAI) et l'ancien
-- Group (synchro S1/Q1) : un protocole, un onglet, un propriétaire par information.
--
-- Ce que voit le joueur (onglet « Groupe » ; vrai, utile, actionnable, sinon rien) :
--   * l'objectif du groupe (« niveau 30 ensemble », « finir « <zone> » ») : bouton « Objectif », partagé ;
--   * le prochain arrêt commun (rendre ensemble, la quête que le plus de monde a) et le regroupement ;
--   * les écarts de niveau (« Bravo est 2 niveaux derrière : partage-lui « A » et « B » ») ;
--   * « Avant de tirer, attends : … » quand tu cibles un monstre hors combat (épinglé dans « Maintenant ») ;
--   * buffs / dissipations : « Lance Robustesse sur Bravo » si tu peux, sinon qui peut le faire ;
--   * une carte par membre : classe, niveau, état (mort, fantôme, AFK, hors ligne, loin, autre zone, PV / mana hors
--     combat, familier), son étape en cours (amis TreeAI), les quêtes qu'il n'a pas / que tu n'as pas, les signaux
--     d'alerte (arme grise, emplacement vide, durabilité, sacs pleins, munitions), son départ annoncé ;
--   * la course à l'XP de la soirée (une ligne, amis TreeAI) ; le point de rencontre et « Prêt ? ».
-- Rien dans le chat, aucune action à la place du joueur (partager, buffer, dissiper, marquer = consignes).
--
-- Protocole v4 : un seul type « GRP » (TreeAI:Send, canal PARTY seulement, mis en pause par le cœur en instance) :
--   "4;D;<seq>;k=v;…;+<quête>;-<id>"   différences seulement depuis le dernier envoi (champs + journal)
--   "4;F;<seq>;k=v;…;+<quête>;…"       état complet (arrivée dans le groupe, demande, trou détecté)
--   "4;R;<nom|*>"                      « renvoie-moi ton état complet »
--   "4;B"                              module coupé : oubliez mes données
--   "4;E;<quoi>;…"                     goal / meet / ask / ans (sur clic du joueur) ; 4.0.1 (GROUPJOIN) : meet + genre +
--                                      n° (proposition « J'y vais »), go (j'y vais), vote / ballot, plan (trajet partagé)
--   quête = "id,niv,drapeaux(C fini, G groupe),titre,m3/8.i0/5" (même forme que Squad v2)
--   4.0.1 : champs ps (position ‰ sur la carte m, ≤ 1 / 20 s, seulement si on a bougé), sp (position de l'étape en cours),
--   wz (zone de travail du trajet), j / je (qui je rejoins, temps restant) — un client 4.0.0 les ignore (clés inconnues).
-- « Rejoindre », point de rencontre, intention du groupe, vote : Modules/GroupJoin.lua (même module, M.Join).
-- « Maintenant » : UNE ligne du groupe au plus (M:Offer → la plus importante) ; tout le reste dans la page.
-- Débit : seau de jetons propre (3, +1 / 5 s) → au plus 1 message / 5 s en régime établi, RIEN si rien ne change ;
-- jamais par-dessus un envoi encore en file chez le cœur. Réception : membres actuels du groupe seulement, seau
-- de jetons par membre, version ≠ 4 ignorée, tout revalidé (bornes, longueurs, caractères).
-- Compat 3.9.x (une version, à retirer en 4.1) : décodage de SQS / SQQ / SQD (Squad v2) et QTM (rendez-vous).
-- Façades : TreeAI.Squad, TreeAI.Missions, TreeAI.Team (+ T.modules.*) gardent les API lues par les autres modules
-- (Dungeon, QuickTravel, Planner, Export, Todo, Tracker, Detect, Radar, Prof, Economy, Abandon…).
-- Coût : tout est événementiel (les gestionnaires posent un drapeau) ; un seul ticker (2 s) fait le travail hors
-- combat ; les lignes ne sont recalculées que si quelque chose a changé (onglet visible : ≤ 1 / 3 s ; caché : ≤ 1 / 30 s,
-- sauf une ligne épinglée qui change). Lua 5.1, aucune globale créée.
local tonumber = function(v) return _G.tonumber(v) end   -- (4.0.8) tonumber(f()) : un 2e retour de f n'est jamais une base
local _, ns = ...
ns = type(ns) == "table" and ns or {}
local T = _G.TreeAI
if type(T) ~= "table" or type(T.RegisterModule) ~= "function" then return end

local PROTO = 4
local TICK = 2
local SEND_GAP, FULL_GAP, BUCKET, REFILL = 5, 15, 3, 5
local MAX_Q = 25
local BUILD_SHOWN, BUILD_HIDDEN = 3, 30
local INSPECT_GAP, INSPECT_CACHE, INSPECT_TIMEOUT = 10, 600, 5
local AURA_GAP, UNITQ_GAP, QUEST_GAP, STEP_GAP = 15, 15, 5, 5
local MEET_GAP, MEET_RECV_GAP, MEET_TTL = 15, 10, 600
local READY_TIMEOUT = 30
local SESSION_GAP, GOAL_TTL, GOAL_DONE_KEEP = 3 * 3600, 8 * 3600, 600
local POS_GAP, POS_MOVE = 20, 30                 -- (GROUPJOIN) position partagée : ≤ 1 / 20 s, ≥ 30 ‰ de la carte

local floor, mmax, mmin, abs = math.floor, math.max, math.min, math.abs
local tconcat, tsort, tremove = table.concat, table.sort, table.remove
local fmt = string.format

local M = T:RegisterModule("Group", {
  title = "Groupe",
  defaults = { enabled = true, cards = true, pull = true, quests = true, support = true, gear = true, goals = true,
               race = true, toasts = true, meetCompass = true },
  charDefaults = { leaveAt = 0, ready = true, goal = false, goalDone = false, session = false, xs = 0, xsLast = 0 },
  options = {
    { key = "cards",   type = "toggle", label = "Une carte par membre (état, étape en cours, quêtes)", section = "Groupe" },
    { key = "pull",    type = "toggle", label = "« Avant de tirer, attends… » quand tu cibles un monstre", section = "Groupe" },
    { key = "quests",  type = "toggle", label = "Quêtes à partager (qui n'a pas quoi)", section = "Groupe" },
    { key = "support", type = "toggle", label = "Buffs et dissipations (qui peut les donner)", section = "Soutien" },
    { key = "gear",    type = "toggle", label = "Signaux d'équipement (inspection discrète, hors combat)", section = "Soutien" },
    { key = "goals",   type = "toggle", label = "Objectif du groupe et prochain arrêt commun", section = "Ensemble" },
    { key = "race",    type = "toggle", label = "Course à l'XP de la soirée (une ligne)", section = "Ensemble" },
    { key = "meetCompass", type = "toggle", label = "La boussole suit le point de rencontre reçu", section = "Ensemble" },
    { key = "toasts",  type = "toggle", label = "Annonces (niveau d'un ami, objectif atteint)", section = "Ensemble" },
  },
})
if not M then return end
M.PROTO = PROTO
ns.Group = M
M.members = {}   -- compat (Quests.FriendsWith) : [nom] = membre qui a TreeAI (champ .quests)

-- crochets gardés dans les tables du cœur (aucune édition de Core.lua)
do
  local info = T.MODULE_INFO
  if type(info) == "table" then
    info.Group = info.Group or { lite = true, gate = "group" }
    info.Group.desc = "Groupe : tes amis (avec ou sans TreeAI), objectif, quêtes à partager, prêts à tirer"
  end
  if type(T.MODULE_TAB) == "table" then T.MODULE_TAB.Group = "group" end
  if type(T.TOPIC_IDS) == "table" then T.TOPIC_IDS["Group:meet"] = "regroup" end
end

-- ------------------------------------------------------------------ utilitaires (appels gardés)
local function pass(ok, ...) if ok then return ... end return nil end
local function call(fn, ...) if type(fn) ~= "function" then return nil end return pass(pcall(fn, ...)) end
local function pub(v)   -- valeur secrète (Forever, en combat) → nil
  local f = _G.issecretvalue
  if v ~= nil and type(f) == "function" then
    local ok, s = pcall(f, v)
    if not ok or s then return nil end
  end
  return v
end
local function now() return call(GetTime) or 0 end
local function epoch() return call(time) or 0 end
local function inCombat() return (T.InCombat and T:InCombat()) and true or false end
local function int(v, lo, hi, def)
  v = tonumber(v)
  if not v or v ~= v then return def end
  if v < lo then v = lo elseif v > hi then v = hi end
  return floor(v + 0.5)
end
local function utf8cut(s, n)
  if #s <= n then return s end
  s = s:sub(1, n)
  local i = #s
  while i > 0 and s:byte(i) >= 128 and s:byte(i) < 192 do i = i - 1 end
  if i > 0 and s:byte(i) >= 192 then
    local lead = s:byte(i)
    local need = (lead >= 240 and 4) or (lead >= 224 and 3) or 2
    if #s - i + 1 < need then s = s:sub(1, i - 1) end
  end
  return s
end
-- texte (reçu ou envoyé) : ni code |, ni séparateur du protocole, borné
local function clean(v, n)
  if type(v) ~= "string" and type(v) ~= "number" then return nil end
  v = tostring(v):gsub("[%c|~;=]", " "):gsub("%s+", " "):gsub("^ ", ""):gsub(" $", "")
  if v == "" then return nil end
  return utf8cut(v, n or 40)
end
local function cleanTitle(v, n) local s = clean(v, n or 30) return s and (s:gsub(",", " ")) end
M.Clean = clean
local function shortName(full)
  if type(full) ~= "string" or full == "" then return nil end
  return (full:gsub("%-.*$", ""))
end
local function q(s) return "« " .. tostring(s or "?") .. " »" end
local function andList(t, maxn)
  local n = #t
  if n == 0 then return "" end
  maxn = mmin(maxn or n, n)
  local s = maxn == 1 and tostring(t[1]) or (tconcat(t, ", ", 1, maxn - 1) .. " et " .. tostring(t[maxn]))
  if n > maxn then s = s .. fmt(" (+%d)", n - maxn) end
  return s
end
M.AndList = andList
local function pct(a, b)
  a, b = tonumber(pub(a)), tonumber(pub(b))
  if not a or not b or b <= 0 then return nil end
  return floor(a * 100 / b + 0.5)
end
local function numText(n)
  local F = T.Fmt
  if F and F.Num then return F.Num(n) end
  return tostring(floor(tonumber(n) or 0))
end
local function myName() return shortName(pub((call(UnitName, "player")))) or "?" end
local function inGroup()
  if type(IsInGroup) == "function" then return call(IsInGroup) and true or false end
  return ((tonumber(call(GetNumGroupMembers)) or tonumber(call(GetNumPartyMembers)) or 0) > 0)
end
local function partyUnit(u) return type(u) == "string" and #u == 6 and u:sub(1, 5) == "party" end
local function byName(a, b) return a.name < b.name end

-- ------------------------------------------------------------------ données de classe
local CLASS_FR = { WARRIOR = "Guerrier", PALADIN = "Paladin", HUNTER = "Chasseur", ROGUE = "Voleur", PRIEST = "Prêtre",
  SHAMAN = "Chaman", MAGE = "Mage", WARLOCK = "Démoniste", DRUID = "Druide" }
M.CLASS_FR = CLASS_FR
local MANA = { PALADIN = true, HUNTER = true, PRIEST = true, SHAMAN = true, MAGE = true, WARLOCK = true, DRUID = true }
local CASTER = { MAGE = true, WARLOCK = true, PRIEST = true }        -- leur mana compte avant un pull
local PET_AT = { HUNTER = 10, WARLOCK = 1 }                         -- niveau où le familier compte
local ROLE_FR = { TANK = "tank", HEALER = "soins" }
local CLASS_ICON = { WARRIOR = "Interface\\Icons\\INV_Sword_27", PALADIN = "Interface\\Icons\\INV_Hammer_01",
  HUNTER = "Interface\\Icons\\INV_Weapon_Bow_07", ROGUE = "Interface\\Icons\\INV_ThrowingKnife_04",
  PRIEST = "Interface\\Icons\\INV_Staff_30", SHAMAN = "Interface\\Icons\\Spell_Nature_BloodLust",
  MAGE = "Interface\\Icons\\INV_Staff_13", WARLOCK = "Interface\\Icons\\Spell_Nature_FaerieFire",
  DRUID = "Interface\\Icons\\Ability_Druid_Maul" }
-- buffs de classe (tous rangs ; nom localisé via le 1er ID, repli FR)
M.BUFFS = {
  { key = "fort", class = "PRIEST", name = "Robustesse", spells = { 1243, 1244, 1245, 2791, 10937, 10938, 21562, 21564 } },
  { key = "int", class = "MAGE", name = "Intelligence des Arcanes", mana = true, spells = { 1459, 1460, 1461, 10156, 10157, 23028 } },
  { key = "motw", class = "DRUID", name = "Marque du fauve", spells = { 1126, 5232, 6756, 5234, 8907, 9884, 9885, 21849, 21850 } },
  { key = "bless", class = "PALADIN", name = "Bénédiction", spells = { 19740, 19834, 19835, 19836, 19837, 19838, 25291,
    19742, 19850, 19852, 19853, 19854, 25290, 20217, 1038, 20911, 19977, 25782, 25894, 25898, 25895, 25899, 25890 } },
}
local DISPEL_FR = { Magic = "magie", Curse = "malédiction", Poison = "poison", Disease = "maladie" }
M.DISPELS = {
  Magic = { { "PRIEST", { 527, 988 }, "Dissipation de la magie" }, { "PALADIN", { 4987 }, "Épuration" } },
  Curse = { { "MAGE", { 475 }, "Délivrance de la malédiction" }, { "DRUID", { 2782 }, "Délivrance de la malédiction" } },
  Poison = { { "DRUID", { 8946, 2893 }, "Guérison du poison" }, { "PALADIN", { 1152, 4987 }, "Purification" },
             { "SHAMAN", { 526 }, "Guérison du poison" } },
  Disease = { { "PRIEST", { 528, 552 }, "Guérison des maladies" }, { "PALADIN", { 1152, 4987 }, "Purification" },
              { "SHAMAN", { 2870 }, "Guérison des maladies" } },
}
local SLOT_FR = { [1] = "tête", [3] = "épaules", [5] = "torse", [6] = "taille", [7] = "jambes", [8] = "pieds",
  [9] = "poignets", [10] = "mains", [15] = "dos", [16] = "arme" }
local GEAR_SLOTS = { 1, 2, 3, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18 }

-- ------------------------------------------------------------------ fil (pur, testable)
local W = {}
M.Wire = W
local OBJ_TYPE = { item = "i", monster = "m", object = "o", event = "e", reputation = "r" }

--- quête { id, level, title, done, group, obj = { {t, have, need} } } -> "id,niv,drapeaux,titre,objectifs"
function W.EncodeEntry(qq)
  local id = int(qq and qq.id, 1, 10000000, nil)
  if not id then return nil end
  local o = {}
  for j, ob in ipairs(qq.obj or {}) do
    if j > 6 then break end
    local need = int(ob.need, 1, 999, 1)
    local have = int(ob.have, 0, 999, ob.finished and need or 0)
    local t = OBJ_TYPE[ob.t] or (type(ob.t) == "string" and ob.t:match("^[mioerx]$")) or "x"
    o[#o + 1] = t .. mmin(have, need) .. "/" .. need
  end
  return fmt("%d,%d,%s,%s,%s", id, int(qq.level, 0, 80, 0), (qq.done and "C" or "") .. (qq.group and "G" or ""),
    cleanTitle(qq.title, 30) or "", tconcat(o, "."))
end

--- "id,niv,drapeaux,titre,objectifs" -> quête (nil si invalide) ; accepte les drapeaux de Squad v2 (Dn)
function W.DecodeEntry(s)
  if type(s) ~= "string" or #s > 300 then return nil end
  local id, lvl, flags, title, objs = s:match("^(%d+),(%d*),(%w*),([^,]*),(.*)$")
  id = int(id, 1, 10000000, nil)
  if not id then return nil end
  local qq = { id = id, level = int(lvl, 0, 80, 0), title = cleanTitle(title, 30), obj = {} }
  qq.done = flags:find("C", 1, true) and true or false
  qq.group = flags:find("G", 1, true) and true or false
  for t, h, n in objs:gmatch("([mioerx])(%d+)/(%d+)") do
    if #qq.obj >= 6 then break end
    local need = int(n, 1, 999, 1)
    qq.obj[#qq.obj + 1] = { t = t, have = int(h, 0, need, 0), need = need }
  end
  return qq
end

-- champs d'état : { min, max, défaut } (nombres) ou longueur max (textes)
W.NUM = { x = { 0, 100, 0 }, r = { 0, 150, 0 }, xs = { 0, 100000000, 0 }, du = { -1, 100, -1 }, b = { 0, 400, 0 },
  bt = { 0, 400, 0 }, am = { -1, 99999, -1 }, fo = { 0, 99, 0 }, wa = { 0, 99, 0 }, hs = { -1, 1, -1 },
  lm = { -1, 600, -1 }, rd = { 0, 1, 1 }, m = { 0, 1000000, 0 }, qm = { 0, 50, 25 }, wz = { 0, 100000, 0 },
  je = { 0, 36000, 0 } }
W.STR = { hb = 24, pr = 60, z = 30, st = 64, gz = 12, ps = 9, sp = 16, j = 24 }
W.KEYS = { "x", "r", "xs", "du", "b", "bt", "am", "fo", "wa", "hs", "lm", "rd", "m", "qm", "hb", "pr", "z", "st", "gz",
  "ps", "sp", "wz", "j", "je" }

--- valeur reçue -> valeur propre (nil = effacée / clé inconnue) ; positions validées (‰ ≤ 1000, carte ≤ 100000)
function W.Field(k, v)
  if k == "ps" then
    local a, b = tostring(v or ""):match("^(%d+)%.(%d+)$")
    a, b = tonumber(a), tonumber(b)
    return (a and b and a <= 1000 and b <= 1000) and (a .. "." .. b) or nil
  elseif k == "sp" then
    local mp, a, b = tostring(v or ""):match("^(%d+)%.(%d+)%.(%d+)$")
    mp, a, b = tonumber(mp), tonumber(a), tonumber(b)
    return (mp and a and b and mp >= 1 and mp <= 100000 and a <= 1000 and b <= 1000) and (mp .. "." .. a .. "." .. b) or nil
  end
  local spec = W.NUM[k]
  if spec then return int(v, spec[1], spec[2], spec[3]) end
  local n = W.STR[k]
  if n then return clean(v, n) end
  return nil
end

local function wireValue(v)
  if v == nil then return "" end
  if type(v) == "number" then return tostring(floor(v + 0.5)) end
  return clean(v, 64) or ""
end
W.Value = wireValue

--- étape en cours : "genre,quête,objectif,libellé"
function W.EncodeStep(k, qid, o, label)
  k = (type(k) == "string" and k:match("^%l+$")) and k:sub(1, 8) or "go"
  return fmt("%s,%d,%d,%s", k, int(qid, 0, 10000000, 0), int(o, 0, 9, 0), clean(label, 48) or "")
end
function W.DecodeStep(s)
  if type(s) ~= "string" then return nil end
  local k, qid, o, label = s:match("^(%l+),(%d*),(%d*),(.*)$")
  if not k then return nil end
  return { k = k:sub(1, 8), q = int(qid, 0, 10000000, 0), o = int(o, 0, 9, 0), l = clean(label, 48) }
end

function W.Build(kind, seq, fields, adds, dels)
  local t = { tostring(PROTO), kind, tostring(seq) }
  for _, k in ipairs(W.KEYS) do
    if fields and fields[k] ~= nil then t[#t + 1] = k .. "=" .. fields[k] end
  end
  for _, e in ipairs(adds or {}) do t[#t + 1] = "+" .. e end
  for _, id in ipairs(dels or {}) do t[#t + 1] = "-" .. id end
  return tconcat(t, ";")
end

--- message reçu -> { kind, seq, fields, add, del } | { kind = "R", target } | { kind = "E", ev, args } | nil
function W.Parse(s)
  if type(s) ~= "string" or #s > 6000 then return nil end
  local ver, kind, rest = s:match("^(%d+);(%u);?(.*)$")
  if tonumber(ver) ~= PROTO then return nil end
  local out = { kind = kind }
  if kind == "D" or kind == "F" then
    local seq, body = rest:match("^(%d+);?(.*)$")
    out.seq = tonumber(seq)
    if not out.seq then return nil end
    out.fields, out.add, out.del = {}, {}, {}
    local n = 0
    for item in body:gmatch("[^;]+") do
      n = n + 1
      if n > 90 then break end
      local c = item:sub(1, 1)
      if c == "+" then
        local qq = W.DecodeEntry(item:sub(2))
        if qq and #out.add < MAX_Q then out.add[#out.add + 1] = qq end
      elseif c == "-" then
        local id = int(item:sub(2), 1, 10000000, nil)
        if id then out.del[#out.del + 1] = id end
      else
        local k, v = item:match("^(%l%l?)=(.*)$")
        if k and (W.NUM[k] or W.STR[k]) then out.fields[k] = v end
      end
    end
  elseif kind == "R" then
    out.target = clean(rest, 24) or "*"
  elseif kind == "E" then
    local a = {}
    for part in (rest .. ";"):gmatch("([^;]*);") do
      a[#a + 1] = part
      if #a >= 8 then break end
    end
    out.ev, out.args = a[1], a
    if not out.ev or out.ev == "" then return nil end
  elseif kind ~= "B" then
    return nil
  end
  return out
end

-- ------------------------------------------------------------------ compat 3.9.x (Squad v2) — à retirer en 4.1
local L = {}
M.Legacy = L
function L.DecodeState(p)
  if type(p) ~= "table" or tonumber(p.v) ~= 2 then return nil end
  local st = {}
  for _, k in ipairs({ "x", "r", "du", "b", "bt", "am", "fo", "wa", "lm", "rd", "m", "qm" }) do
    local spec = W.NUM[k]
    st[k] = int(p[k], spec[1], spec[2], spec[3])
  end
  local hs = tonumber(p.hs)
  st.hs = (not hs or hs < 0) and -1 or (hs == 0 and 0 or 1)
  st.hb, st.pr, st.z = clean(p.hb, 24), clean(p.pr, 60), clean(p.z, 30)
  return st
end
function L.Entries(body)
  local out, n = {}, 0
  if type(body) ~= "string" then return out end
  for entry in body:gmatch("[^;]+") do
    local id = tonumber(entry:match("^(%d+),"))
    if id and #entry <= 300 then
      n = n + 1
      if n > MAX_Q then break end
      out[id] = entry
    end
  end
  return out
end
local function sortedIds(map)
  local ids = {}
  for id in pairs(map) do ids[#ids + 1] = id end
  tsort(ids)
  return ids
end
function L.Hash(map)
  local h = 5381
  for _, id in ipairs(sortedIds(map)) do
    local e = map[id]
    for i = 1, #e do h = (h * 33 + e:byte(i)) % 16777213 end
    h = (h * 33 + 59) % 16777213
  end
  return h
end
function L.ApplyDiff(map, changes)
  if type(changes) ~= "string" or #changes > 6000 then return nil end
  local out, n = {}, 0
  for k, v in pairs(map) do out[k] = v end
  for part in changes:gmatch("[^;]+") do
    local op, rest = part:sub(1, 1), part:sub(2)
    if op == "+" then
      local id = tonumber(rest:match("^(%d+),"))
      if not id or #rest > 300 then return nil end
      out[id] = rest
    elseif op == "-" then
      local id = tonumber(rest)
      if not id then return nil end
      out[id] = nil
    else
      return nil
    end
  end
  for _ in pairs(out) do n = n + 1 end
  if n > MAX_Q then return nil end
  return out
end
local function decodeAll(entries)
  local out = {}
  for id, e in pairs(entries) do out[id] = W.DecodeEntry(e) end
  return out
end

-- ------------------------------------------------------------------ moteur (pur, déterministe)
local E = {}
M.Engine = E

function E.Progress(qq)
  local h, n = 0, 0
  for _, o in ipairs(qq and qq.obj or {}) do h = h + mmin(o.have or 0, o.need or 0); n = n + (o.need or 0) end
  if n == 0 then n = 1; h = (qq and qq.done) and 1 or 0 end
  if qq and qq.done then h = n end
  return h, n
end

--- a-t-il la quête ? true / false / nil (inconnu : pas de TreeAI et pas une de mes quêtes)
local function holds(m, id)
  if m.me or m.addon then return m.quests ~= nil and m.quests[id] ~= nil end
  if m.onQuest then return m.onQuest[id] end
  return nil
end
E.Holds = holds

--- index des quêtes du groupe : { id, title, level, group, holders, done, need, missing, unknown, per, multi, allDone }
function E.Index(list)
  local byId, res = {}, {}
  for _, m in ipairs(list) do
    if (m.me or m.addon) and m.quests then
      for id, qq in pairs(m.quests) do
        local e = byId[id]
        if not e then
          e = { id = id, holders = {}, done = {}, need = {}, missing = {}, per = {}, level = qq.level or 0 }
          byId[id] = e
          res[#res + 1] = e
        end
        e.title = e.title or qq.title
        e.group = e.group or qq.group
        e.holders[#e.holders + 1] = m.name
        local h, n = E.Progress(qq)
        e.per[m.name] = { have = h, need = n, done = qq.done and true or false, obj = qq.obj or {} }
        if qq.done then e.done[#e.done + 1] = m.name else e.need[#e.need + 1] = m.name end
      end
    end
  end
  for _, m in ipairs(list) do   -- sans TreeAI : présent sur mes quêtes (progression inconnue)
    if not (m.me or m.addon) and m.onQuest then
      for id, on in pairs(m.onQuest) do
        local e = byId[id]
        if e and on then
          e.holders[#e.holders + 1] = m.name
          e.unknown = e.unknown or {}
          e.unknown[#e.unknown + 1] = m.name
        end
      end
    end
  end
  for _, e in ipairs(res) do
    e.title = e.title or ("quête #" .. e.id)
    tsort(e.holders); tsort(e.done); tsort(e.need)
    local has = {}
    for _, n in ipairs(e.holders) do has[n] = true end
    for _, m in ipairs(list) do
      if not has[m.name] and holds(m, e.id) == false then e.missing[#e.missing + 1] = m.name end
    end
    tsort(e.missing)
    e.multi = #e.holders >= 2
    e.allDone = #e.need == 0 and not e.unknown
  end
  tsort(res, function(a, b)
    if #a.holders ~= #b.holders then return #a.holders > #b.holders end
    return a.id < b.id
  end)
  return res
end

--- tableau compatible Missions (Todo, IA) : + pct, objNames (mon journal), remaining, tri « finissable ensemble »
function E.Board(index, mine, meName)
  local out = {}
  for _, e in ipairs(index or {}) do
    local have, total = 0, 0
    for _, p in pairs(e.per) do have, total = have + p.have, total + p.need end
    e.pct = total > 0 and floor(have * 100 / total) or 0
    e.remaining = total - have
    e.objNames = {}
    local my = mine and mine[e.id]
    for j, o in ipairs(my and my.obj or {}) do e.objNames[j] = o.name end
    e.claims = e.claims or {}
    e._me, e._status = meName, nil
    out[#out + 1] = e
  end
  tsort(out, function(a, b)
    if a.multi ~= b.multi then return a.multi end
    if a.allDone ~= b.allDone then return a.allDone end
    if #a.holders ~= #b.holders then return #a.holders > #b.holders end
    if a.remaining ~= b.remaining then return a.remaining < b.remaining end
    return a.id < b.id
  end)
  return out
end

-- (COHERE) « rendre » seulement si la quête est dans MON journal ET finie ; nil = journal pas encore lu
local myQuests = nil   -- posé par le relevé du journal (runtime)
local function mineReady(id)
  id = tonumber(id)
  if myQuests then
    local qq = myQuests[id]
    return (qq and qq.done) and true or false
  end
  local Qm = T.GetModule and T:GetModule("Quests")
  local list = Qm and Qm.lastList
  if type(list) ~= "table" then return nil end
  for _, x in ipairs(list) do if x.id == id then return x.complete and true or false end end
  return false
end

local function whoText(names)
  local o = {}
  for i, n in ipairs(names or {}) do if i <= 3 then o[#o + 1] = tostring(n) end end
  return tconcat(o, ", ") .. ((#(names or {}) > 3) and "…" or "")
end

function E.MemberStatus(e, name)
  local p = e.per and e.per[name]
  local who = (e._me and name == e._me) and "toi" or name
  if not p then return who .. " : pas la quête" end
  if p.done then return who .. " : terminé" end
  local bits = {}
  for j, o in ipairs(p.obj or {}) do
    local have, need = mmin(o.have or 0, o.need or 0), o.need or 0
    if need - have > 0 then
      local label = e.objNames and e.objNames[j]
      -- « Rassembleur Fléau-Mort 2/8 » (le compte du jeu), jamais « 6 Rassembleur Fléau-Mort tué restants »
      bits[#bits + 1] = label and (need > 1 and fmt("%s %d/%d", label, have, need) or label) or fmt("%d/%d", have, need)
      if #bits >= 2 then break end
    end
  end
  if #bits == 0 then return who .. " : presque" end
  return who .. " : " .. tconcat(bits, ", ")
end
function E.StatusLine(e)
  if e._status then return e._status end
  local t = {}
  for _, n in ipairs(e.need or {}) do t[#t + 1] = E.MemberStatus(e, n) end
  for _, n in ipairs(e.done or {}) do t[#t + 1] = ((e._me and n == e._me) and "toi" or n) .. " : terminé" end
  e._status = tconcat(t, ", ")
  return e._status
end

--- prochain arrêt commun : rendre ensemble (si dans MON journal), sinon la quête que le plus de monde a
--- meName (facultatif) : mon nom, écrit « toi »
function E.Next(board, meName)
  for _, e in ipairs(board or {}) do
    if e.multi and e.allDone then
      if mineReady(e.id) == false then
        return { mission = e, kind = "theirs", text = fmt("%s peu%s rendre %s (pas dans ton journal)", whoText(e.holders),
          #e.holders > 1 and "vent" or "t", q(e.title)) }
      end
      return { mission = e, kind = "turnin", text = fmt("Rendez ensemble %s (%d/%d prêts)", q(e.title), #e.done, #e.holders) }
    end
  end
  local best
  for _, e in ipairs(board or {}) do
    if e.multi and not e.allDone and #e.need > 0 then
      if not best or #e.holders > #best.holders or (#e.holders == #best.holders and e.pct > best.pct) then best = e end
    end
  end
  if not best then return nil end
  local bits = {}
  for _, n in ipairs(best.holders) do
    local p = best.per[n]
    local who = (meName and n == meName) and "toi" or n
    if p then bits[#bits + 1] = p.done and (who .. " fini") or fmt("%s %d/%d", who, p.have, p.need) end
    if #bits >= 4 then break end
  end
  return { mission = best, kind = "do", text = fmt("Ensemble : %s — %s", q(best.title), tconcat(bits, ", ")) }
end

--- qui n'est pas prêt avant un pull (membres AUTRES que moi, état lu par le jeu) -> { "Bravo 30 % mana", … }
function E.PullBlockers(list)
  local why = {}
  for _, m in ipairs(list or {}) do
    if not m.me then
      local w
      if m.online == false then w = "hors ligne"
      elseif m.ghost then w = "fantôme"
      elseif m.dead then w = "mort"
      elseif m.afk then w = "AFK"
      elseif m.far then w = "loin"
      elseif m.mana and m.mana < 35 and (m.role == "HEALER" or CASTER[m.class or ""]) then w = fmt("%d %% mana", m.mana)
      elseif m.hp and m.hp < 50 then w = fmt("%d %% PV", m.hp)
      elseif m.petMissing then w = "sans familier"
      elseif m.addon and m.st and m.st.rd == 0 then w = "pas prêt" end
      if w then why[#why + 1] = m.name .. " " .. w end
    end
  end
  return why
end

--- prêt ? (moi ou un membre sans TreeAI) -> ok, raison
function E.Ready(m)
  if not m then return true, nil end
  if m.online == false then return false, "hors ligne" end
  if m.dead then return false, m.ghost and "fantôme" or "mort" end
  if m.afk then return false, "AFK" end
  if m.hp and m.hp < 50 then return false, fmt("PV %d %%", m.hp) end
  if m.mana and m.mana < 40 and MANA[m.class or ""] and m.class ~= "HUNTER" then return false, fmt("mana %d %%", m.mana) end
  if m.petMissing then return false, "familier absent" end
  if m.st and m.st.rd == 0 then return false, "pas prêt" end
  return true, nil
end

-- quête adaptée à un niveau : pas rouge (≤ +2), pas grise (≥ -5)
local function suits(ql, pl)
  ql, pl = tonumber(ql) or 0, tonumber(pl)
  if ql <= 0 or not pl then return true end
  local d = ql - pl
  return d <= 2 and d >= -5
end
E.Suits = suits

local function titlesOf(list, n)
  local t = {}
  for i = 1, mmin(#list, n) do t[i] = q(list[i].title) end
  return t
end
local function byTitle(a, b)
  if (a.title or "") ~= (b.title or "") then return (a.title or "") < (b.title or "") end
  return a.id < b.id
end

local function orList(t, maxn)
  local n = #t
  maxn = mmin(maxn or n, n)
  if n == 0 then return "" end
  local s = maxn == 1 and tostring(t[1]) or (tconcat(t, ", ", 1, maxn - 1) .. " ou " .. tostring(t[maxn]))
  if n > maxn then s = s .. fmt(" (+%d)", n - maxn) end
  return s
end

--- quêtes à partager (les miennes qu'ils n'ont pas, à leur niveau) et à demander (les leurs, amis TreeAI, à mon niveau),
--- une ligne par quête ; covered[nom][id] = déjà proposée dans une ligne d'écart de niveau -> lignes { text, prio, tooltip, questID }
function E.ShareLines(list, mine, myLevel, pushable, covered)
  covered = covered or {}
  local give, take, byQ = {}, {}, {}
  for id, qq in pairs(mine or {}) do
    if not qq.done and (not pushable or pushable(id)) then
      local who = {}
      for _, m in ipairs(list) do
        if not m.me and m.online ~= false and holds(m, id) == false and suits(qq.level, m.level)
            and not (covered[m.name] and covered[m.name][id]) then who[#who + 1] = m.name end
      end
      if #who > 0 then tsort(who); give[#give + 1] = { qq = qq, who = who } end
    end
  end
  for _, m in ipairs(list) do
    if not m.me and m.addon and m.online ~= false and m.quests then
      for id, qq in pairs(m.quests) do
        if not (mine and mine[id]) and not qq.done and suits(qq.level, myLevel) and not (covered[""] and covered[""][id]) then
          local e = byQ[id]
          if not e then e = { qq = qq, who = {} }; byQ[id] = e; take[#take + 1] = e end
          e.who[#e.who + 1] = m.name
        end
      end
    end
  end
  local function order(a, b)
    if #a.who ~= #b.who then return #a.who > #b.who end
    return byTitle(a.qq, b.qq)
  end
  tsort(give, order)
  tsort(take, order)
  local out = {}
  for i, e in ipairs(give) do
    if i > 3 then break end
    out[#out + 1] = { text = fmt("Partage %s à %s", q(e.qq.title), andList(e.who, 3)), prio = 1, questID = e.qq.id,
      tooltip = { "Tu as cette quête, eux non (à leur niveau)", "Journal (L) → la quête → Partager" } }
  end
  if #give > 3 then
    local tip = { "Encore à partager :" }
    for i = 4, mmin(#give, 12) do tip[#tip + 1] = q(give[i].qq.title) .. " → " .. andList(give[i].who) end
    out[#out + 1] = { text = fmt("Encore %d quête%s à partager", #give - 3, #give > 4 and "s" or ""), prio = 1, tooltip = tip }
  end
  for i, e in ipairs(take) do
    if i > 2 then break end
    out[#out + 1] = { text = fmt("Demande %s à %s", q(e.qq.title), orList(e.who, 3)), prio = 1,
      tooltip = { "Ils ont cette quête, toi non (à ton niveau)", "Demande-leur de la partager" } }
  end
  return out
end

--- écarts de niveau (≥ 2 sous le plus haut du groupe) -> lignes { text, prio, tooltip, who }
function E.LevelGaps(list, mine, myLevel, pushable)
  local maxL = 0
  for _, m in ipairs(list) do if m.online ~= false and tonumber(m.level) then maxL = mmax(maxL, m.level) end end
  local out = {}
  for _, m in ipairs(list) do
    local gap = maxL - (tonumber(m.level) or maxL)
    if gap >= 2 and m.online ~= false then
      local text, tip, ids
      if m.me then
        local offer, from = {}, {}
        for _, o in ipairs(list) do
          if not o.me and o.addon and o.quests then
            for id, qq in pairs(o.quests) do
              if not (mine and mine[id]) and not qq.done and suits(qq.level, m.level) and not from[id] then
                from[id] = o.name
                offer[#offer + 1] = qq
              end
            end
          end
        end
        tsort(offer, byTitle)
        if #offer > 0 then
          ids = { offer[1].id, offer[2] and offer[2].id or nil }
          text = fmt("Tu es %d niveaux derrière : demande %s à %s", gap, andList(titlesOf(offer, 2)), from[offer[1].id])
          tip = { "Quêtes des amis que tu n'as pas (à ton niveau) :" }
          for i = 1, mmin(#offer, 8) do tip[#tip + 1] = q(offer[i].title) .. " — " .. from[offer[i].id] end
        elseif gap >= 4 then
          text = fmt("Tu es %d niveaux derrière le groupe", gap)
        end
      else
        local give = {}
        for id, qq in pairs(mine or {}) do
          if not qq.done and holds(m, id) == false and (not pushable or pushable(id)) and suits(qq.level, m.level) then give[#give + 1] = qq end
        end
        tsort(give, byTitle)
        if #give > 0 then
          ids = { give[1].id, give[2] and give[2].id or nil }
          text = fmt("%s est %d niveaux derrière : partage-lui %s", m.name, gap, andList(titlesOf(give, 2)))
          tip = { "Ses quêtes en plus = plus d'XP pour lui :" }
          for i = 1, mmin(#give, 8) do tip[#tip + 1] = q(give[i].title) end
          tip[#tip + 1] = "Journal (L) → la quête → Partager"
        elseif gap >= 4 then   -- sans quête à lui partager : une ligne seulement pour un vrai écart (sinon rien à faire)
          local r = m.addon and m.st and tonumber(m.st.r) or 0
          text = fmt("%s est %d niveaux derrière%s", m.name, gap, r >= 20 and " (en repos : il rattrape)" or "")
        end
      end
      if text then out[#out + 1] = { text = text, prio = gap >= 4 and 2 or 1, tooltip = tip, who = m.me and "" or m.name, ids = ids } end
    end
  end
  return out
end

--- objectif du groupe -> { text, prio, tip, done } | nil
function E.GoalLine(goal, list)
  if type(goal) ~= "table" then return nil end
  if goal.k == "level" then
    local n = tonumber(goal.n) or 0
    local behind, done, total = {}, 0, 0
    for _, m in ipairs(list) do
      if m.online ~= false or m.me then
        total = total + 1
        local lv = tonumber(m.level) or 0
        if lv >= n then done = done + 1
        else
          local x = (m.me or m.addon) and m.st and tonumber(m.st.x)
          behind[#behind + 1] = fmt("%s %d%s", m.me and "toi" or m.name, lv, x and fmt(" (%d %%)", x) or "")
        end
      end
    end
    if total > 0 and done == total then
      return { text = fmt("Objectif atteint : niveau %d pour tout le monde !", n), prio = 2, done = true,
        tip = { "Objectif du groupe", "Clic : en choisir un autre" } }
    end
    return { text = fmt("Objectif niveau %d ensemble : %d/%d — %s", n, done, total, tconcat(behind, ", ")), prio = 1,
      tip = { "Objectif du groupe" .. (goal.by and (" (choisi par " .. goal.by .. ")") or ""),
        "Niveau actuel (et % du niveau pour les amis TreeAI)", "Clic : changer l'objectif" } }
  elseif goal.k == "zone" then
    local left, ready, parts, unknown = 0, 0, {}, {}
    for _, m in ipairs(list) do
      local gz = m.gz
      if gz then
        left, ready = left + gz[1], ready + gz[2]
        if gz[1] > 0 then parts[#parts + 1] = fmt("%s %d", m.me and "toi" or m.name, gz[1]) end
      elseif not m.me and m.online ~= false then
        unknown[#unknown + 1] = m.name
      end
    end
    local tip = { "Objectif du groupe" .. (goal.by and (" (choisi par " .. goal.by .. ")") or ""),
      "Quêtes de cette zone encore dans les journaux (amis TreeAI)" }
    if #unknown > 0 then tip[#tip + 1] = "Inconnu (sans TreeAI) : " .. tconcat(unknown, ", ") end
    tip[#tip + 1] = "Clic : changer l'objectif"
    if left == 0 and ready == 0 then
      return { text = fmt("Objectif atteint : %s est finie !", q(goal.z)), prio = 2, done = true, tip = tip }
    end
    local text = fmt("Objectif : finir %s — %d quête%s restante%s", q(goal.z), left, left > 1 and "s" or "", left > 1 and "s" or "")
    if #parts > 0 then text = text .. " (" .. tconcat(parts, ", ") .. ")" end
    if ready > 0 then text = text .. fmt(" · %d à rendre", ready) end
    return { text = text, prio = 1, tip = tip }
  end
  return nil
end

--- course à l'XP de la soirée (amis TreeAI + moi) -> { {name, xs} } trié
function E.Leaderboard(list)
  local out = {}
  for _, m in ipairs(list) do
    local xs = (m.me or m.addon) and m.st and tonumber(m.st.xs) or nil
    if xs and xs > 0 then out[#out + 1] = { name = m.name, xs = xs, me = m.me } end
  end
  tsort(out, function(a, b) if a.xs ~= b.xs then return a.xs > b.xs end return a.name < b.name end)
  return out
end

--- regroupement : quelqu'un dans une autre zone que la majorité -> { text, prio } | nil
function E.Regroup(list)
  local counts, zones = {}, {}
  for _, m in ipairs(list) do
    if m.online ~= false and m.zoneKey then
      if not counts[m.zoneKey] then counts[m.zoneKey] = 0; zones[#zones + 1] = m.zoneKey end
      counts[m.zoneKey] = counts[m.zoneKey] + 1
    end
  end
  if #zones < 2 then return nil end
  tsort(zones, function(a, b) if counts[a] ~= counts[b] then return counts[a] > counts[b] end return a < b end)
  local main = zones[1]
  if counts[main] < 2 or counts[zones[2]] == counts[main] then return nil end
  local away, meAway, mainName = {}, false, nil
  for _, m in ipairs(list) do
    if m.online ~= false and m.zoneKey then
      if m.zoneKey == main then mainName = mainName or m.zoneName
      elseif m.me then meAway = true
      else away[#away + 1] = m.name .. (m.zoneName and (" (" .. m.zoneName .. ")") or "") end
    end
  end
  if meAway then
    return { text = "Rejoins le groupe" .. (mainName and (" : ils sont dans " .. q(mainName)) or " : il est dans une autre zone"), prio = 2 }
  end
  if #away == 0 then return nil end
  return { text = fmt("Regroupez-vous : %s %s dans une autre zone", andList(away, 3), #away > 1 and "sont" or "est"), prio = 1 }
end

--- signaux d'alerte d'un membre (équipement inspecté + état TreeAI) -> { mots }, prio
function E.Flags(m)
  local f, prio = {}, 1
  local gear = m.gear
  if gear and not gear.partial then
    local w = gear.weapon
    if w and w.quality == 0 then f[#f + 1] = "arme grise"
    elseif w and w.quality == 1 and (tonumber(m.level) or 0) >= 15 then f[#f + 1] = "arme blanche" end
    if #(gear.missing or {}) > 0 then
      local names = {}
      for _, s in ipairs(gear.missing) do names[#names + 1] = SLOT_FR[s] end
      f[#f + 1] = "rien en " .. andList(names, 3)
    end
    if gear.ilvl and tonumber(m.level) and m.level >= 10 and gear.ilvl < m.level - 8 then
      f[#f + 1] = fmt("équipement niv. %d", gear.ilvl)
    end
  end
  local st = m.addon and m.st
  if st then
    if st.du and st.du >= 0 and st.du < 25 then f[#f + 1] = fmt("durabilité %d %%", st.du); if st.du < 10 then prio = 2 end end
    if st.bt and st.bt > 0 and st.b == 0 then f[#f + 1] = "sacs pleins" end
    if m.class == "HUNTER" and st.am and st.am >= 0 and st.am < 200 then
      f[#f + 1] = fmt("%d munitions", st.am)
      if st.am < 100 then prio = 2 end
    end
  end
  return f, prio
end

-- conseil de jet (texte seulement, jamais de jet automatique)
local function set(...) local t = {} for _, v in ipairs({ ... }) do t[v] = 1 end return t end
local WEAPON = {
  [0] = set("WARRIOR", "PALADIN", "HUNTER", "SHAMAN"), [1] = set("WARRIOR", "PALADIN", "HUNTER", "SHAMAN"),
  [2] = set("HUNTER", "ROGUE", "WARRIOR"), [3] = set("HUNTER", "ROGUE", "WARRIOR"),
  [4] = set("WARRIOR", "PALADIN", "ROGUE", "SHAMAN", "PRIEST", "DRUID"), [5] = set("WARRIOR", "PALADIN", "SHAMAN", "DRUID"),
  [6] = set("WARRIOR", "PALADIN", "HUNTER"), [7] = set("WARRIOR", "PALADIN", "ROGUE", "HUNTER", "MAGE", "WARLOCK"),
  [8] = set("WARRIOR", "PALADIN", "HUNTER"), [10] = set("DRUID", "HUNTER", "MAGE", "PRIEST", "SHAMAN", "WARLOCK", "WARRIOR"),
  [13] = set("WARRIOR", "ROGUE", "HUNTER", "SHAMAN", "DRUID"),
  [15] = set("WARRIOR", "ROGUE", "HUNTER", "MAGE", "PRIEST", "WARLOCK", "SHAMAN", "DRUID"),
  [16] = set("WARRIOR", "ROGUE"), [18] = set("HUNTER", "ROGUE", "WARRIOR"), [19] = set("MAGE", "PRIEST", "WARLOCK"),
}
local RANGED_FIRST = { [2] = "HUNTER", [3] = "HUNTER", [18] = "HUNTER" }
local ARMOR_NAME = { [1] = "tissu", [2] = "cuir", [3] = "maille", [4] = "plaques" }
local function bestArmor(class, level)
  if class == "WARRIOR" or class == "PALADIN" then return (level or 0) >= 40 and 4 or 3 end
  if class == "HUNTER" or class == "SHAMAN" then return (level or 0) >= 40 and 3 or 2 end
  if class == "ROGUE" or class == "DRUID" then return 2 end
  return 1
end
--- item = { name, classID, subClassID, equipLoc } ; list = membres { name, class, level } -> texte | nil
function E.LootHint(item, list)
  if type(item) ~= "table" or #(list or {}) < 2 then return nil end
  local cid, sid = tonumber(item.classID), tonumber(item.subClassID)
  local name = clean(item.name, 40) or "objet"
  local need = {}
  local sorted = {}
  for _, m in ipairs(list) do if m.class then sorted[#sorted + 1] = m end end
  tsort(sorted, byName)
  if cid == 2 and sid and WEAPON[sid] then
    local pref = RANGED_FIRST[sid]
    for _, m in ipairs(sorted) do if WEAPON[sid][m.class] and (not pref or m.class == pref) then need[#need + 1] = m.me and "toi" or m.name end end
    if pref and #need == 0 then
      for _, m in ipairs(sorted) do if WEAPON[sid][m.class] then need[#need + 1] = m.me and "toi" or m.name end end
    end
  elseif cid == 4 and sid then
    if item.equipLoc == "INVTYPE_CLOAK" or sid == 0 then
      return fmt("Jet %s : utile à tous, Besoin seulement si c'est mieux que le sien", q(name))
    elseif ARMOR_NAME[sid] then
      for _, m in ipairs(sorted) do if bestArmor(m.class, m.level) == sid then need[#need + 1] = m.me and "toi" or m.name end end
      if #need == 0 then
        for _, m in ipairs(sorted) do if bestArmor(m.class, m.level) >= sid then need[#need + 1] = m.me and "toi" or m.name end end
      end
    elseif sid == 6 then
      for _, m in ipairs(sorted) do
        if m.class == "WARRIOR" or m.class == "PALADIN" or m.class == "SHAMAN" then need[#need + 1] = m.me and "toi" or m.name end
      end
    else
      return nil
    end
  else
    return nil
  end
  if #need == 0 then return fmt("Jet %s : personne ne l'utilise, Cupidité pour tous", q(name)) end
  local kind = (cid == 4 and ARMOR_NAME[sid]) and (" (" .. ARMOR_NAME[sid] .. ")") or ""
  return fmt("Jet %s : Besoin %s%s ; les autres Cupidité", q(name), andList(need, 3), kind)
end

-- ------------------------------------------------------------------ état d'exécution
local API = T.API or {}
local roster, members = {}, {}       -- autres membres (ordre du groupe) / [nom court] = membre
local me = { name = "?", unit = "player", me = true, addon = "me", st = {}, quests = {}, entries = {} }
local mine = me.st                   -- mes champs (envoyés aux amis TreeAI)
local sentVal, sentEntries = {}, {}  -- ce que les amis en savent
local gearCache, auraCache, mapNames, knowsCache, pushCache = {}, {}, {}, {}, {}
local dirty = { quests = true, xp = true, bags = true, dura = true, prof = true, zone = true, view = true,
  live = true, unitQ = true, urgent = false }
local S = { lastScan = -1e9, lastBags = -1e9, lastBuild = -1e9, lastSend = -1e9, lastFull = -1e9, tokens = BUCKET,
  tokenAt = nil, seq = 0, wantFull = false, req = {}, reqAt = {}, events = {}, lastInspect = -1e9, lastAura = -1e9,
  lastUnitQ = -1e9, lastStep = -1e9, stepKey = false, shown = false, auraSig = "", meetAt = {}, lastMeet = -1e9 }
local body, pullText, pullOn = {}, nil, false
local inBuckets = {}
M._state, M._me, M._roster = S, me, function() return roster end

local function setMine(k, v)
  if mine[k] ~= v then mine[k] = v; S.gen = (S.gen or 0) + 1 end
end

local function memberList()
  local list = { me }
  for _, m in ipairs(roster) do list[#list + 1] = m end
  tsort(list, byName)
  return list
end
function M:List() return memberList() end
function M:Dirty(urgent) dirty.view = true; if urgent then dirty.urgent = true end end
--- (GROUPJOIN) qui je rejoins (nom, "@rdv", "@group" ou nil) et le temps restant (s) : champs j / je du fil
function M:SetJoin(who, eta)
  setMine("j", who and clean(who, 24) or nil)
  setMine("je", who and int(eta, 0, 36000, 0) or nil)
end

-- ------------------------------------------------------------------ « Maintenant » : UNE ligne du groupe au plus
-- Toutes les annonces du groupe passent par M:Offer ; seule la plus importante va dans « Maintenant » (Suggest) — les
-- autres restent des lignes en tête de la page. Une ligne épinglée de l'onglet (« Avant de tirer », « Lance … ») compte
-- aussi : elle n'est épinglée que si aucune annonce plus importante n'est en cours.
local NOW_RANK = { pull = 100, say = 95, ready = 90, vote = 85, meet = 80, rdv = 80, join = 60, turnin = 55, cast = 50,
  intent = 45, plan = 40, news = 30 }   -- say = réponse à un clic (6 s) : le joueur doit la voir ; vote = à cliquer en 2 min
M.NOW_RANK = NOW_RANK
local news = {}   -- [clé] = { key, kind, rank, text, prio, opts, exp, at, posted, postedText, sounded }
local function newsSorted()
  local out = {}
  for _, e in pairs(news) do out[#out + 1] = e end
  tsort(out, function(a, b)
    if a.rank ~= b.rank then return a.rank > b.rank end
    if a.at ~= b.at then return a.at > b.at end
    return a.key < b.key
  end)
  return out
end
function M:NowApply()
  local t = now()
  for k, e in pairs(news) do
    if e.exp and e.exp <= t then news[k] = nil; if e.posted then self:Clear(k) end end
  end
  local best = newsSorted()[1]
  local pinRank = (pullText and NOW_RANK.pull) or (S.castTop and NOW_RANK.cast) or 0
  if best and best.rank <= pinRank then best = nil end
  S.pinNow = pinRank > 0 and best == nil
  for k, e in pairs(news) do
    if e ~= best and e.posted then e.posted = nil; self:Clear(k) end
  end
  if best and (not best.posted or best.postedText ~= best.text or best.postedPrio ~= best.prio) then
    local o = {}
    for k, v in pairs(best.opts or {}) do o[k] = v end
    o.tab = "now"   -- « Maintenant » seulement : dans la page, l'annonce a sa propre ligne (pas deux fois la même)
    o.ttl = best.exp and mmax(1, best.exp - t) or 0
    if best.sounded then o.sound = false end
    self:Suggest(best.key, best.text, best.prio, o)
    best.posted, best.postedText, best.postedPrio = true, best.text, best.prio
    best.sounded = best.sounded or (o.sound and true) or false
  end
end
--- une annonce du groupe : kind = clé de NOW_RANK ; opts = { ttl, onClick, tooltip, sound, soundKind, cooldown }
function M:Offer(key, kind, text, prio, opts)
  if type(text) ~= "string" or text == "" then return self:Withdraw(key) end
  opts = opts or {}
  local t = now()
  local e = news[key]
  if not e then e = { key = key, at = t }; news[key] = e
  elseif e.text ~= text then e.at = t end
  e.kind, e.rank, e.text, e.prio, e.opts = kind, NOW_RANK[kind] or NOW_RANK.news, text, prio or 1, opts
  local ttl = opts.ttl
  if ttl == nil then ttl = 60 end
  e.exp = (ttl > 0) and (t + ttl) or nil
  dirty.view, dirty.urgent = true, true
  self:NowApply()
  return e
end
function M:Withdraw(key)
  local e = news[key]
  news[key] = nil
  self:Clear(key)
  if e then dirty.view = true; self:NowApply() end
end
--- réponse courte à un clic (6 s), comme une annonce (jamais le chat)
function M:Say(text) return self:Offer("say", "say", text, 1, { ttl = 6 }) end

-- ------------------------------------------------------------------ façades (anciens noms, API gardées)
local SQ = { name = "Squad", CLASS_FR = CLASS_FR, PROTO = 2, _members = {}, Engine = { Progress = E.Progress } }
local MI = { name = "Missions", board = {}, list = {}, next = nil,
  Engine = { Next = E.Next, StatusLine = E.StatusLine, MemberStatus = E.MemberStatus, Board = E.Board, Progress = E.Progress } }
local TM = { name = "Team" }
local LIFE = { _active = true, _suspended = true, _gateOff = true, char = true }
for _, f in ipairs({ SQ, MI, TM }) do
  setmetatable(f, { __index = function(_, k) if LIFE[k] then return M[k] end end })
  f.IsEnabled = function() return M:IsEnabled() end
end
M.Facades = { Squad = SQ, Missions = MI, Team = TM }
T.Squad, T.Missions, T.Team = SQ, MI, TM
if type(T.modules) == "table" then
  for n, f in pairs(M.Facades) do if T.modules[n] == nil then T.modules[n] = f end end
end

-- ------------------------------------------------------------------ groupe : membres et état lu par le jeu
local function newMember(name) return { name = name, st = {}, quests = {}, seen = 0 } end

local function forgetAddon(m)
  m.addon, m.legacy, m.seq, m.step, m.gz, m.legacyEntries = nil, nil, nil, nil, nil, nil
  m.quests = {}
  for _, k in ipairs(W.KEYS) do m.st[k] = nil end
  M.members[m.name], SQ._members[m.name] = nil, nil
end

function M:ScanRoster()
  S.lastRosterScan = now()
  local keep, added, list = {}, {}, {}
  local myN = myName()
  me.name = myN
  for i = 1, 4 do
    local u = "party" .. i
    if call(UnitExists, u) then
      local nm = shortName(pub((call(UnitName, u))))
      if nm and nm ~= myN and not keep[nm] then
        local m = members[nm]
        if not m then m = newMember(nm); members[nm] = m; added[#added + 1] = nm end
        m.unit = u
        local _, cls = call(UnitClass, u)
        m.class = pub(cls) or m.class
        m.guid = pub(call(UnitGUID, u)) or m.guid
        local role = pub(call(UnitGroupRolesAssigned, u))
        m.role = (role and ROLE_FR[role]) and role or nil
        keep[nm] = true
        list[#list + 1] = m
      end
    end
  end
  local changed = #added > 0 or #list ~= #roster
  for nm, m in pairs(members) do
    if not keep[nm] then
      members[nm], M.members[nm], SQ._members[nm] = nil, nil, nil
      if m.guid then gearCache[m.guid] = nil end
      changed = true
    end
  end
  for i, m in ipairs(list) do if roster[i] ~= m then changed = true end end
  roster = list
  if #added > 0 then
    S.wantFull = true
    for _, nm in ipairs(added) do S.req[#S.req + 1] = nm end
  end
  if changed then
    dirty.view, dirty.unitQ, dirty.live = true, true, true
    S.indexDirty = true
  end
  return roster
end

local function mapName(id)
  if not id then return nil end
  local n = mapNames[id]
  if n == nil then
    local info = C_Map and call(C_Map.GetMapInfo, id)
    n = type(info) == "table" and clean(pub(info.name), 30) or false
    mapNames[id] = n
  end
  return n or nil
end

-- état instantané (valeurs secrètes → nil ; PV / mana seulement hors combat) + vue compat (Squad:State)
function M:ReadLive(m, fight, myMap, myZone)
  local u = m.unit
  if not u then return end
  m.gear = m.guid and gearCache[m.guid] or nil
  m.online = call(UnitIsConnected, u) ~= false
  m.ghost = pub(call(UnitIsGhost, u)) and true or false
  m.dead = pub(call(UnitIsDeadOrGhost, u)) and true or false
  m.afk = pub(call(UnitIsAFK, u)) and true or false
  local lv = tonumber(pub(call(UnitLevel, u)))
  if lv and lv > 0 then m.level = lv end
  if fight then
    m.hp, m.mana = nil, nil
  else
    m.hp = pct(call(UnitHealth, u), call(UnitHealthMax, u))
    m.manaUser = pub(call(UnitPowerType, u)) == 0
    m.mana = m.manaUser and pct(call(UnitPower, u, 0), call(UnitPowerMax, u, 0)) or nil
  end
  local need = PET_AT[m.class or ""]
  local petUnit = (u == "player") and "pet" or (u .. "pet")
  m.petMissing = (need and (m.level or 0) >= need and m.online and not m.dead
    and not (call(UnitExists, petUnit) and not call(UnitIsDead, petUnit))) and true or nil
  local st = m.st
  st.c, st.l = m.class, m.level
  st.d = m.ghost and 2 or (m.dead and 1 or 0)
  st.af = m.afk and 1 or 0
  st.h, st.p = m.hp, m.mana or -1
  st.pt = need and (m.petMissing and 2 or 1) or -1
  if u ~= "player" then
    local inR, checked = call(UnitInRange, u)
    inR, checked = pub(inR), pub(checked)
    if checked then m.far = not inR else m.far = nil end
    local map = C_Map and pub(call(C_Map.GetBestMapForUnit, u))
    m.otherZone, m.zoneName, m.zoneKey = false, nil, nil
    if (m.addon and st.z) and myZone then
      m.zoneKey, m.zoneName = st.z, st.z
    elseif map then
      m.zoneName = mapName(map)
      m.zoneKey = (map == myMap and myZone) or m.zoneName or ("map" .. map)
    end
    if m.zoneKey and myZone and m.zoneKey ~= myZone and not (map and map == myMap) then m.otherZone = true end
  end
  local y, x, _, inst = call(UnitPosition, u)
  y, x, inst = tonumber(pub(y)), tonumber(pub(x)), tonumber(pub(inst))
  if y and x then st.py, st.px, st.pi = floor(y / 5 + 0.5) * 5, floor(x / 5 + 0.5) * 5, inst or -1
  elseif m.addon and st.m and st.ps and T.Geo and T.Geo.World then
    -- (GROUPJOIN) le jeu ne la donne pas : la position que l'ami TreeAI partage (‰ de sa carte), même repère
    local a, b = tostring(st.ps):match("^(%d+)%.(%d+)$")
    local wn, ww, wc = T.Geo.World(st.m, (tonumber(a) or 0) / 10, (tonumber(b) or 0) / 10)
    if a and wn then st.py, st.px, st.pi = floor(wn / 5 + 0.5) * 5, floor(ww / 5 + 0.5) * 5, tonumber(wc) or -1
    else st.py, st.px, st.pi = nil, nil, nil end
  else st.py, st.px, st.pi = nil, nil, nil end
end

-- niveau gagné, mort : annonces légères + statistiques de session
function M:Transitions()
  local s = self:Session()
  for _, m in ipairs(roster) do
    if m.lastLevel and m.level and m.level > m.lastLevel then
      self:Toast("level:" .. m.name, fmt("%s passe niveau %d !", m.name, m.level), 1, { ttl = 10 })
      if s then
        local lv = s.levels[m.name] or { from = m.lastLevel }
        lv.to = m.level
        s.levels[m.name] = lv
        self:Event(s, fmt("%s passe niveau %d", m.name, m.level))
      end
    end
    m.lastLevel = m.level or m.lastLevel
    if m.wasDead == false and m.dead and s then
      s.deaths[m.name] = (s.deaths[m.name] or 0) + 1
      self:Event(s, m.name .. " est mort")
    end
    if m.online ~= false then m.wasDead = m.dead and true or false end
  end
end

function M:Refresh()
  local fight = inCombat()
  local myMap = C_Map and pub(call(C_Map.GetBestMapForUnit, "player"))
  local myZone = clean(call(GetRealZoneText), 30)
  me.zoneKey, me.zoneName = myZone, myZone
  me.class = me.class or select(2, call(UnitClass, "player"))
  self:ReadLive(me, fight)
  for _, m in ipairs(roster) do self:ReadLive(m, fight, myMap, myZone) end
  self:Transitions()
end

-- ------------------------------------------------------------------ mon état (champs du fil)
local HEARTHSTONE = 6948
local function durability()
  if type(GetInventoryItemDurability) ~= "function" then return -1 end
  local worst
  for slot = 1, 18 do
    local cur, mx = call(GetInventoryItemDurability, slot)
    cur, mx = tonumber(cur), tonumber(mx)
    if cur and mx and mx > 0 then
      local r = cur / mx
      if not worst or r < worst then worst = r end
    end
  end
  return worst and floor(worst * 100 + 0.5) or -1
end
local function countItems(list)
  local n = 0
  if type(list) ~= "table" or not API.ItemCount then return 0 end
  for _, it in ipairs(list) do
    local id = type(it) == "table" and it[1] or it
    if tonumber(id) then n = n + (tonumber(call(API.ItemCount, id)) or 0) end
  end
  return n
end
local function hearth()
  local have = API.ItemCount and (tonumber(call(API.ItemCount, HEARTHSTONE)) or 0) or 0
  if have <= 0 then return -1, nil end
  local start, dur
  if C_Container and C_Container.GetItemCooldown then start, dur = call(C_Container.GetItemCooldown, HEARTHSTONE) end
  if not start and type(GetItemCooldown) == "function" then start, dur = call(GetItemCooldown, HEARTHSTONE) end
  start, dur = tonumber(start), tonumber(dur)
  local ready = not (start and dur and start > 0 and dur > 0 and start + dur - now() > 0)
  return ready and 0 or 1, clean(call(GetBindLocation), 24)
end
local function professions()
  local out = {}
  if type(GetProfessions) == "function" and type(GetProfessionInfo) == "function" then
    local idx = { call(GetProfessions) }
    for i = 1, 5 do
      if idx[i] then
        local n, _, rank = call(GetProfessionInfo, idx[i])
        if n then out[#out + 1] = fmt("%s %d", n, tonumber(rank) or 0) end
      end
    end
  elseif type(GetNumSkillLines) == "function" and type(GetSkillLineInfo) == "function" then
    local inProf = false
    for i = 1, mmin(call(GetNumSkillLines) or 0, 80) do
      local n, header, _, rank = call(GetSkillLineInfo, i)
      if header then
        local h = tostring(n or ""):lower()
        inProf = h:find("tier", 1, true) or h:find("profession", 1, true) or false
      elseif inProf and n then
        out[#out + 1] = fmt("%s %d", n, tonumber(rank) or 0)
      end
    end
  end
  return clean(tconcat(out, " "), 60)
end
local function maxQuests()
  local n = C_QuestLog and C_QuestLog.GetMaxNumQuestsCanAccept and tonumber((call(C_QuestLog.GetMaxNumQuestsCanAccept)))
  return n or 25
end
local function leaveMinutes(char)
  local at = char and tonumber(char.leaveAt) or 0
  if at <= 0 then return -1 end
  local left = at - epoch()
  if left <= 0 then
    char.leaveAt = 0
    return -1
  end
  return mmin(600, math.ceil(left / 60))
end

function M:CollectMine(force)
  local t = now()
  if dirty.xp or force then
    dirty.xp = false
    local xp, mx = tonumber(pub(call(UnitXP, "player"))), tonumber(pub(call(UnitXPMax, "player")))
    if xp and mx and mx > 0 then
      setMine("x", floor(xp * 100 / mx))
      setMine("r", mmin(150, floor((tonumber(pub(call(GetXPExhaustion))) or 0) * 100 / mx)))
    end
    setMine("xs", self.char and floor(tonumber(self.char.xs) or 0) or 0)
  end
  if (dirty.bags and t - S.lastBags >= 10) or force then
    dirty.bags, S.lastBags = false, t
    local free, total = call(API.FreeSlots)
    setMine("b", tonumber(free) or 0)
    setMine("bt", tonumber(total) or 0)
    setMine("am", (me.class == "HUNTER") and (tonumber(pub(call(GetInventoryItemCount, "player", 0))) or -1) or -1)
    if force or t - (S.lastFood or -1e9) >= 60 then   -- nourriture / eau / pierre : rarement utiles, lus ≤ 1 / min
      S.lastFood = t
      local items = T.Data and T.Data.Macros and T.Data.Macros.items or {}
      setMine("fo", mmin(99, countItems(items.food)))
      setMine("wa", mmin(99, countItems(items.water)))
      local hs, hb = hearth()
      setMine("hs", hs)
      setMine("hb", hb)
    else
      dirty.bags = true
    end
  end
  if dirty.dura or force then dirty.dura = false; setMine("du", durability()) end
  if dirty.prof or force then dirty.prof = false; setMine("pr", professions()) end
  if dirty.zone or force then
    dirty.zone = false
    setMine("z", clean(call(GetRealZoneText), 30))
    setMine("m", C_Map and int(pub(call(C_Map.GetBestMapForUnit, "player")), 0, 1000000, 0) or 0)
  end
  -- (GROUPJOIN) position partagée « ps » (‰ de la carte « m ») : ≤ 1 / 20 s, seulement si on a bougé (≥ 3 % de la carte) ou
  -- changé de carte ; rien en instance (sans objet) — les amis l'ont du jeu quand il la donne, ceci est le repli
  if force or t - (S.lastPos or -1e9) >= POS_GAP then
    S.lastPos = t
    local map, x, y = self:MyPos()
    if map and x and y and not pub((call(IsInInstance))) then
      if x > 1 or y > 1 then x, y = x / 100, y / 100 end
      local px, py = int(x * 1000, 0, 1000, 0), int(y * 1000, 0, 1000, 0)
      local o = S.posSent
      if force or not o or o.map ~= map or abs(px - o.x) + abs(py - o.y) >= POS_MOVE then
        S.posSent = { map = map, x = px, y = py }
        setMine("m", int(map, 0, 1000000, 0))
        setMine("ps", px .. "." .. py)
      end
    elseif mine.ps then
      S.posSent = nil
      setMine("ps", nil)
    end
  end
  setMine("lm", leaveMinutes(self.char))
  setMine("rd", (self.char and self.char.ready == false) and 0 or 1)
  if not mine.qm then setMine("qm", maxQuests()) end
end

-- XP de la soirée (exacte, comptée par PLAYER_XP_UPDATE ; nouvelle soirée après 2 h sans XP)
local xpLast, xpLastMax
function M:TrackXP()
  local c = self.char
  if not c then return end
  local xp, mx = tonumber(pub(call(UnitXP, "player"))), tonumber(pub(call(UnitXPMax, "player")))
  if not xp or not mx then return end
  local t = epoch()
  if t - (tonumber(c.xsLast) or 0) > 2 * 3600 then c.xs = 0 end
  if xpLast then
    if xp >= xpLast then c.xs = (tonumber(c.xs) or 0) + (xp - xpLast)
    elseif xpLastMax then c.xs = (tonumber(c.xs) or 0) + (xpLastMax - xpLast) + xp end
  end
  xpLast, xpLastMax = xp, mx
  c.xsLast = t
end

-- étape en cours (Planner) : changée → un champ du fil
function M:PollStep(t)
  if t - S.lastStep < STEP_GAP then return end
  S.lastStep = t
  local P = T.Planner
  local s = type(P) == "table" and type(P.steps) == "table" and P.cursor and P.steps[P.cursor] or nil
  -- (GROUPJOIN) zone choisie pour le trajet (ZonePlan, mode « zones » seulement : en auto, c'est la zone où je suis)
  local Z = T.ZonePlan
  if type(Z) == "table" and type(Z.Allowed) == "function" and Z._active ~= false and Z.char then
    local A = call(Z.Allowed, Z)
    local wz
    if type(A) == "table" and A.mode == "zones" and type(A.maps) == "table" then
      for mm in pairs(A.maps) do if tonumber(mm) and (not wz or mm < wz) then wz = mm end end
    end
    setMine("wz", wz and int(wz, 0, 100000, 0) or nil)
  end
  local key = s and (tostring(s.k) .. ":" .. tostring(s.q) .. ":" .. tostring(s.o)) or ""
  if key == S.stepKey then return end
  S.stepKey = key
  if not s then setMine("st", nil); setMine("sp", nil) return end
  local label = type(P.StepLabel) == "function" and call(P.StepLabel, P, s) or nil
  setMine("st", W.EncodeStep(s.k, s.q, s.o, label or s.label))
  local sx, sy, smap = tonumber(s.x), tonumber(s.y), tonumber(s.map)
  setMine("sp", (sx and sy and smap) and fmt("%d.%d.%d", smap, int(sx * 10, 0, 1000, 0), int(sy * 10, 0, 1000, 0)) or nil)
  me.step = W.DecodeStep(mine.st)
end

-- ------------------------------------------------------------------ mon journal (lecture seule)
local function objName(text)
  if type(text) ~= "string" then return nil end
  local on = T.ObjectiveName
  local n = on and call(on, text) or text:gsub("%s*:?%s*%d+%s*/%s*%d+.*$", "")
  n = tostring(n or ""):gsub("%s+tuée?s?$", ""):gsub("%s+slain$", "")   -- « Rassembleur Fléau-Mort tué » -> le nom
  return cleanTitle(n, 28)
end

function M:ScanQuests()
  S.lastScan, dirty.quests = now(), false
  local quests, header, count = {}, nil, 0
  local goal = self:Goal()
  local gzone = goal and goal.k == "zone" and goal.z or nil
  local gz = { 0, 0 }
  local CQ = C_QuestLog
  local function addQ(qq)
    count = count + 1
    quests[qq.id] = qq
    if gzone and qq.header == gzone then
      if qq.done then gz[2] = gz[2] + 1 else gz[1] = gz[1] + 1 end
    end
  end
  if CQ and CQ.GetNumQuestLogEntries and CQ.GetInfo then
    for i = 1, mmin(tonumber((call(CQ.GetNumQuestLogEntries))) or 0, 80) do
      local info = call(CQ.GetInfo, i)
      if type(info) == "table" then
        if info.isHeader then header = clean(info.title, 30)
        elseif info.questID and not info.isHidden and count < MAX_Q then
          local id = info.questID
          local qq = { id = id, title = cleanTitle(info.title, 30), level = int(info.level, 0, 80, 0), idx = i, obj = {}, header = header }
          qq.done = call(CQ.IsComplete, id) and true or false
          qq.group = (tonumber(info.suggestedGroup) or 0) > 1
          for j, o in ipairs(call(CQ.GetQuestObjectives, id) or {}) do
            if j > 6 then break end
            if type(o) == "table" then
              qq.obj[#qq.obj + 1] = { t = o.type, have = tonumber(o.numFulfilled) or 0, need = tonumber(o.numRequired) or 1,
                finished = o.finished, name = objName(o.text) }
            end
          end
          addQ(qq)
        end
      end
    end
  elseif type(GetNumQuestLogEntries) == "function" and type(GetQuestLogTitle) == "function" then
    for i = 1, mmin(tonumber((call(GetNumQuestLogEntries))) or 0, 80) do
      local title, lvl, sugg, isHeader, _, isComplete, _, qid = call(GetQuestLogTitle, i)
      if title and isHeader then header = clean(title, 30)
      elseif title and qid and count < MAX_Q then
        local qq = { id = qid, title = cleanTitle(title, 30), level = int(lvl, 0, 80, 0), idx = i, obj = {}, header = header }
        qq.done = (isComplete == 1 or isComplete == true)
        qq.group = (tonumber(sugg) or 0) > 1
        for j = 1, mmin(tonumber((call(GetNumQuestLeaderBoards, i))) or 0, 6) do
          local text, kind, finished = call(GetQuestLogLeaderBoard, j, i)
          local h, n = tostring(text or ""):match("(%d+)%s*/%s*(%d+)")
          qq.obj[#qq.obj + 1] = { t = kind, have = tonumber(h) or (finished and 1 or 0), need = tonumber(n) or 1,
            finished = finished, name = objName(text) }
        end
        addQ(qq)
      end
    end
  end
  local entries, changed = {}, false
  for id, qq in pairs(quests) do
    local e = W.EncodeEntry(qq)
    entries[id] = e
    if me.entries[id] ~= e then changed = true end
  end
  for id in pairs(me.entries) do if not entries[id] then changed = true end end
  me.quests, me.entries, me.scanned = quests, entries, true
  myQuests = quests
  setMine("gz", gzone and (gz[1] .. "." .. gz[2]) or nil)
  me.gz = gzone and gz or nil
  if changed then
    S.gen = (S.gen or 0) + 1
    S.indexDirty, dirty.view, dirty.unitQ = true, true, true
    pushCache = {}
  end
end

local function pushable(id)
  local v = pushCache[id]
  if v == nil then
    v = true
    local CQ = C_QuestLog
    if CQ and CQ.IsPushableQuest then v = call(CQ.IsPushableQuest, id) ~= false end
    pushCache[id] = v
  end
  return v
end

-- membres sans TreeAI : sont-ils sur MES quêtes ? (C_QuestLog.IsUnitOnQuest, repli IsUnitOnQuest classique)
local function unitOnQuest(u, id, idx)
  local CQ = C_QuestLog
  local f = CQ and CQ.IsUnitOnQuest
  if f then
    local ok, r = pcall(f, u, id)
    if not ok then ok, r = pcall(f, id, u) end
    if ok then
      if r == nil then return nil end
      return pub(r) and true or false
    end
  end
  if type(IsUnitOnQuest) == "function" and idx then
    local ok, r = pcall(IsUnitOnQuest, idx, u)
    if ok then return (pub(r) == 1 or pub(r) == true) end
  end
  return nil
end
function M:ScanUnitQuests()
  S.lastUnitQ, dirty.unitQ = now(), false
  local sig = {}
  for _, m in ipairs(roster) do
    if not m.addon and m.unit and m.online ~= false then
      local h = {}
      for id, qq in pairs(me.quests) do
        local v = unitOnQuest(m.unit, id, qq.idx)
        h[id] = v
        sig[#sig + 1] = id .. (v == true and "+" or (v == false and "-" or "?"))
      end
      m.onQuest = h
      sig[#sig + 1] = m.name
    else
      m.onQuest = nil
    end
  end
  tsort(sig)
  local key = tconcat(sig, ",")
  if key ~= S.unitQSig then
    S.unitQSig = key
    S.indexDirty, dirty.view = true, true
  end
end

-- ------------------------------------------------------------------ envoi (≤ 1 message / 5 s, différences)
local function significant(k, old, new)
  if type(old) ~= "number" or type(new) ~= "number" then return old ~= new end
  if k == "am" then return abs(new - old) >= 50 or (old < 200) ~= (new < 200) or (old < 100) ~= (new < 100) or (old < 0) ~= (new < 0) end
  if k == "du" then return abs(new - old) >= 5 or (old < 25) ~= (new < 25) or (old < 10) ~= (new < 10) end
  if k == "fo" or k == "wa" then return (old ~= new and (old < 10 or new < 10)) or abs(new - old) >= 5 end
  if k == "xs" then return abs(new - old) >= 100 end
  if k == "je" then return abs(new - old) >= 30 or (old == 0) ~= (new == 0) end
  return old ~= new
end
M.Significant = significant

function M:BuildDelta()
  local fields, n = {}, 0
  for _, k in ipairs(W.KEYS) do
    if significant(k, sentVal[k], mine[k]) then fields[k] = wireValue(mine[k]); n = n + 1 end
  end
  local adds, dels = {}, {}
  for id, e in pairs(me.entries) do if sentEntries[id] ~= e then adds[#adds + 1] = e end end
  for id in pairs(sentEntries) do if not me.entries[id] then dels[#dels + 1] = id end end
  if n == 0 and #adds == 0 and #dels == 0 then return nil end
  tsort(adds); tsort(dels)
  local seq = S.seq + 1
  local gen = S.gen
  return W.Build("D", seq, fields, adds, dels), function()
    S.seq, S.sentGen = seq, gen
    for k in pairs(fields) do sentVal[k] = mine[k] end
    for _, e in ipairs(adds) do sentEntries[tonumber(e:match("^(%d+)"))] = e end
    for _, id in ipairs(dels) do sentEntries[id] = nil end
  end
end

function M:BuildFull()
  local fields = {}
  for _, k in ipairs(W.KEYS) do if mine[k] ~= nil then fields[k] = wireValue(mine[k]) end end
  local adds = {}
  for _, e in pairs(me.entries) do adds[#adds + 1] = e end
  tsort(adds)
  local seq = S.seq + 1
  local gen = S.gen
  return W.Build("F", seq, fields, adds, nil), function()
    S.seq, S.sentGen = seq, gen
    for _, k in ipairs(W.KEYS) do sentVal[k] = mine[k] end
    sentEntries = {}
    for id, e in pairs(me.entries) do sentEntries[id] = e end
  end
end

function M:QueueEvent(...)
  local t = { tostring(PROTO), "E" }
  for i = 1, select("#", ...) do t[#t + 1] = clean((select(i, ...)), 40) or "" end
  S.events[#S.events + 1] = { p = tconcat(t, ";"), t = now() }
  self:Flush(now())
end

function M:RequestFull(name)
  local t = now()
  if t - (S.reqAt[name] or -1e9) < 20 then return end
  S.reqAt[name] = t
  S.req[#S.req + 1] = name
end

function M:Flush(t)
  if not (T.Send and inGroup()) or T.commPaused then return false end
  local cs = T.CommStatus and T:CommStatus()
  if type(cs) == "table" and (cs.queued or 0) > 0 then return false end   -- jamais par-dessus un envoi encore en file
  S.tokenAt = S.tokenAt or t
  S.tokens = mmin(BUCKET, S.tokens + (t - S.tokenAt) / REFILL)
  S.tokenAt = t
  if S.tokens < 1 then return false end
  local payload, commit, isEvent
  while S.events[1] and t - S.events[1].t > 30 do tremove(S.events, 1) end   -- un clic d'il y a 30 s ne compte plus
  if #S.events > 0 then
    payload, isEvent = S.events[1].p, true
  elseif S.wantFull and t - S.lastFull >= FULL_GAP then
    payload, commit = self:BuildFull()
  elseif #S.req > 0 then
    payload = fmt("%d;R;%s", PROTO, #S.req > 1 and "*" or S.req[1])
  elseif t - S.lastSend >= SEND_GAP and S.gen ~= S.sentGen and (me.scanned or not dirty.quests) then
    local gen = S.gen
    payload, commit = self:BuildDelta()
    if not payload then S.sentGen = gen end   -- rien de significatif : on ne recalcule plus avant le prochain changement
  end
  if not payload then return false end
  if not T:Send("GRP", payload, "PARTY") then return false end
  S.tokens, S.lastSend = S.tokens - 1, t
  if isEvent then tremove(S.events, 1)
  elseif commit then
    commit()
    if payload:sub(3, 3) == "F" then S.wantFull, S.lastFull = false, t end
  else
    S.req = {}
  end
  return true
end

-- ------------------------------------------------------------------ réception
local function allow(name)   -- 8 jetons, +1 / 2 s par membre
  local t = now()
  local b = inBuckets[name]
  if not b then b = { tok = 8, t = t }; inBuckets[name] = b end
  b.tok = mmin(8, b.tok + (t - b.t) / 2)
  b.t = t
  if b.tok < 1 then return false end
  b.tok = b.tok - 1
  return true
end

function M:MemberOf(sender)
  local name = shortName(sender)
  if not name or name == myName() then return nil end
  local m = members[name]
  if not m and now() - (S.lastRosterScan or -1e9) >= 2 then   -- inconnu : le groupe a peut-être changé (≤ 1 relecture / 2 s)
    self:ScanRoster()
    m = members[name]
  end
  return m
end
function M:IsMember(sender) return self:MemberOf(sender) ~= nil end

local function markAddon(m, kind, questsChanged)
  if m.addon ~= kind then
    m.addon = kind
    M.members[m.name], SQ._members[m.name] = m, m
    dirty.unitQ, questsChanged = true, true
  end
  m.seen = now()
  dirty.view = true
  if questsChanged then S.indexDirty = true end
end

function M:OnWire(m, payload)
  local msg = W.Parse(payload)
  if not msg then return end
  local kind = msg.kind
  if kind == "D" or kind == "F" then
    local oldZ, oldM, oldP = m.st.z, m.st.m, m.st.ps   -- (GROUPJOIN) entrée de donjon apprise au passage dehors → dedans
    if kind == "F" then
      for _, k in ipairs(W.KEYS) do m.st[k] = nil end
      m.quests, m.step, m.gz = {}, nil, nil
    elseif m.seq and msg.seq <= m.seq and m.seq - msg.seq <= 3 then
      return   -- déjà reçu (un numéro bien plus petit = il a rechargé : traité comme un trou ci-dessous)
    elseif not (m.seq and msg.seq == m.seq + 1) or m.addon ~= "4" then
      self:RequestFull(m.name)   -- trou (message perdu, arrivée en cours) : valeurs absolues appliquées quand même
    end
    for k, v in pairs(msg.fields) do
      if k == "st" then
        m.step = W.DecodeStep(v)
        m.st.st = m.step and v or nil
      elseif k == "gz" then
        local n, d = tostring(v):match("^(%d+)%.(%d+)$")
        m.gz = n and { int(n, 0, 99, 0), int(d, 0, 99, 0) } or nil
      else
        m.st[k] = W.Field(k, v)
      end
    end
    local count = 0
    for _ in pairs(m.quests) do count = count + 1 end
    for _, qq in ipairs(msg.add) do
      if m.quests[qq.id] or count < MAX_Q then
        if not m.quests[qq.id] then count = count + 1 end
        m.quests[qq.id] = qq
      end
    end
    for _, id in ipairs(msg.del) do m.quests[id] = nil end
    m.seq, m.legacy = msg.seq, nil
    if msg.fields.je ~= nil then m.jeAt = now() end
    if msg.fields.ps ~= nil then m.pAt = now() end
    if M.Join and m.st.z and m.st.z ~= oldZ and M.Join.OnZone then call(M.Join.OnZone, oldM, oldP, m.st.z) end
    markAddon(m, "4", kind == "F" or #msg.add > 0 or #msg.del > 0 or msg.fields.st ~= nil or msg.fields.gz ~= nil)
  elseif kind == "R" then
    if m.addon ~= "4" then markAddon(m, "4") end
    if (msg.target == "*" and now() - S.lastFull >= 10) or msg.target == myName() then S.wantFull = true end
  elseif kind == "B" then
    forgetAddon(m)
    dirty.view, S.indexDirty = true, true
  elseif kind == "E" then
    self:OnEvent(m, msg.ev, msg.args)
  end
end

-- 3.9.x : Squad v2 (SQS / SQQ / SQD) et rendez-vous de QuickTravel (QTM) — décodage seulement, une version
function M:OnLegacy(m, kind, payload)
  if kind == "SQS" then
    local st = L.DecodeState(payload)
    if not st then return end
    for k, v in pairs(st) do m.st[k] = v end
  elseif kind == "SQQ" then
    if type(payload) ~= "string" then return end
    local ver, body2 = payload:match("^(%d+):(.*)$")
    if tonumber(ver) ~= 2 then return end
    m.legacyEntries = L.Entries(body2)
    m.quests = decodeAll(m.legacyEntries)
  elseif kind == "SQD" then
    if type(payload) ~= "string" then return end
    local ver, base, new, changes = payload:match("^(%d+):(%d+):(%d+):(.*)$")
    if tonumber(ver) ~= 2 or not m.legacyEntries or L.Hash(m.legacyEntries) ~= tonumber(base) then return end
    local applied = L.ApplyDiff(m.legacyEntries, changes)
    if not applied or L.Hash(applied) ~= tonumber(new) then return end
    m.legacyEntries = applied
    m.quests = decodeAll(applied)
  elseif kind == "QTM" then
    local p = payload
    if type(p) ~= "table" or tonumber(p.v) ~= 1 then return end
    local map, x, y = tonumber(p.m), tonumber(p.x), tonumber(p.y)
    if not (map and x and y) or map < 1 or map > 100000 or x < 0 or x > 1000 or y < 0 or y > 1000 then return end
    self:OnMeet(m.name, floor(map), x / 1000, y / 1000, clean(p.l, 24))
    return
  else
    return
  end
  m.legacy = true
  markAddon(m, m.addon == "4" and "4" or "3", kind ~= "SQS")
end

function M:OnComm(kind, payload, sender)
  local m = self:MemberOf(sender)
  if not m or not allow(m.name) then return end
  if kind == "GRP" then return self:OnWire(m, payload) end
  return self:OnLegacy(m, kind, payload)
end

-- ------------------------------------------------------------------ événements partagés (clics)
function M:OnEvent(m, ev, a)
  if m.addon ~= "4" then markAddon(m, "4") end
  if ev == "goal" then
    local k = a[2]
    if k == "level" or k == "zone" or k == "off" then self:SetGoal(k, a[3], m.name) end
  elseif ev == "meet" then
    local map, x, y = int(a[2], 1, 100000, nil), tonumber(a[3]), tonumber(a[4])
    if map and x and y and x >= 0 and x <= 1000 and y >= 0 and y <= 1000 then
      local id = int(a[7], 1, 999, nil)
      if id and self.Join and self.Join.OnProposal then   -- (GROUPJOIN) proposition : « J'y vais » d'un clic
        self.Join.OnProposal(m, map, x / 1000, y / 1000, clean(a[5], 24), clean(a[6], 8), id)
      else
        self:OnMeet(m.name, map, x / 1000, y / 1000, clean(a[5], 24))
      end
    end
  elseif (ev == "go" or ev == "vote" or ev == "ballot" or ev == "plan") and self.Join then
    local f = self.Join[({ go = "OnGo", vote = "OnVote", ballot = "OnBallot", plan = "OnPlan" })[ev]]
    if f then f(m, a) end
  elseif ev == "ask" then
    local id = int(a[2], 1, 1000000, nil)
    if id then self:OnReadyAsk(m.name, id) end
  elseif ev == "ans" then
    local rc, id = S.rc, int(a[2], 1, 1000000, nil)
    if rc and id and rc.id == id then
      rc.answers[m.name] = { r = tonumber(a[3]) == 1, w = clean(a[4], 20) }
      dirty.view, dirty.urgent = true, true
    end
  end
end

-- ------------------------------------------------------------------ objectif du groupe
function M:Goal()
  local c = self.char
  local g = c and c.goal
  if type(g) ~= "table" then return nil end
  if epoch() - (tonumber(g.at) or 0) > GOAL_TTL then c.goal, c.goalDone = false, false return nil end
  return g
end

function M.GoalLabel(g)
  if type(g) ~= "table" then return "aucun" end
  if g.k == "level" then return fmt("niveau %d ensemble", g.n or 0) end
  if g.k == "zone" then return "finir " .. q(g.z) end
  return "aucun"
end

--- kind = "level" | "zone" | "off" ; by = nil (moi : partagé) | nom de l'ami qui l'a choisi
function M:SetGoal(kind, value, by)
  local c = self.char
  if not c then return false end
  local mineGoal = by == nil or by == myName()
  if kind == "off" then
    c.goal = false
  elseif kind == "level" then
    local n = int(value, 2, 60, nil)
    if not n then return false end
    c.goal = { k = "level", n = n, by = by or myName(), at = epoch() }
  elseif kind == "zone" then
    local z = clean(value, 30)
    if not z then return false end
    c.goal = { k = "zone", z = z, by = by or myName(), at = epoch() }
  else
    return false
  end
  c.goalDone = false
  dirty.quests, S.lastScan = true, -1e9    -- recompte des quêtes de la zone
  dirty.view, dirty.urgent = true, true
  S.picking = nil
  if mineGoal then
    self:QueueEvent("goal", kind, kind == "level" and tostring(c.goal.n) or (kind == "zone" and c.goal.z) or "")
    if #roster > 0 then self:Build() end   -- le choix se referme tout de suite
  else
    self:Toast("goal", kind == "off" and fmt("%s a retiré l'objectif du groupe", by)
      or fmt("%s propose l'objectif : %s", by, M.GoalLabel(c.goal)), 1, { ttl = 20 })
  end
  return true
end

function M:Picker(on)
  S.picking = on and now() or nil
  dirty.view, dirty.urgent = true, true
  if T.SelectTab and on then call(T.SelectTab, T, "group") end
  self:Build()
end

-- choix proposés : niveau (le plus haut + 1, + 2), les zones de mon journal qui ont le plus de quêtes
function M:GoalChoices(list)
  local maxL = tonumber(me.level) or 1
  for _, m in ipairs(list) do if tonumber(m.level) then maxL = mmax(maxL, m.level) end end
  local out = {}
  for d = 1, 2 do
    if maxL + d <= 60 then out[#out + 1] = { kind = "level", value = maxL + d, text = fmt("Niveau %d ensemble", maxL + d) } end
  end
  local counts, zones = {}, {}
  for _, qq in pairs(me.quests) do
    local h = qq.header
    if h and not qq.done then
      if not counts[h] then counts[h] = 0; zones[#zones + 1] = h end
      counts[h] = counts[h] + 1
    end
  end
  tsort(zones, function(a, b) if counts[a] ~= counts[b] then return counts[a] > counts[b] end return a < b end)
  for i = 1, mmin(2, #zones) do
    out[#out + 1] = { kind = "zone", value = zones[i],
      text = fmt("Finir %s (%d quête%s)", q(zones[i]), counts[zones[i]], counts[zones[i]] > 1 and "s" or "") }
  end
  return out
end

-- ------------------------------------------------------------------ « Prêt ? »
function M:ReadyAsk()
  if not inGroup() then return false end
  local id = (S.rcSeq or 0) + 1
  S.rcSeq = id
  local myN = myName()
  S.rc = { id = id, by = myN, at = now(), answers = {} }
  self:Refresh()
  local ok, why = E.Ready(me)
  S.rc.answers[myN] = { r = ok, w = why }
  self:QueueEvent("ask", tostring(id))
  dirty.view, dirty.urgent = true, true
  local QT = T.QuickTravel   -- la fenêtre « Tout le monde prêt ? » montre l'état de chacun
  if type(QT) == "table" and type(QT.ShowReady) == "function" then pcall(QT.ShowReady, QT, true) end
  return true
end

--- bouton « Prêt ? » : chef / assistant = l'appel du jeu (tout le monde le voit) ; sinon la vérification TreeAI
function M:ReadyCheck()
  if inCombat() then return false end
  local QT = T.QuickTravel
  if type(QT) == "table" and type(QT.ReadyCheck) == "function" then return QT.ReadyCheck() end
  return self:ReadyAsk()
end

function M:ReadyAnswer(yes, why)
  local rc = S.rc
  if not rc or now() - rc.at > READY_TIMEOUT then return false end
  rc.answers[myName()] = { r = yes and true or false, w = why }
  self:QueueEvent("ans", tostring(rc.id), yes and "1" or "0", why or "")
  self:Withdraw("ready")
  dirty.view = true
  return true
end

function M:OnReadyAsk(by, id)
  S.rc = { id = id, by = by, at = now(), answers = { [by] = { r = true } } }
  self:Refresh()
  local ok, why = E.Ready(me)
  self:Offer("ready", "ready", fmt("%s demande : prêt ?%s — clic : oui", by, ok and "" or (" (toi : " .. why .. ")")), 2,
    { ttl = READY_TIMEOUT, cooldown = 0, sound = true, soundKind = "group",
      tooltip = { "Vérification « prêt ? » de " .. by, "Clic : tu es prêt (le groupe le voit)" },
      onClick = function() M:ReadyAnswer(true) end })
end

-- états pour la ligne du demandeur : { {name, ok, why} }, tous prêts ?
function M:ReadyStates(list)
  local rc = S.rc
  local out, all = {}, true
  for _, m in ipairs(list) do
    local ok, why
    if m.me then
      local a = rc and rc.answers[m.name]
      ok, why = a and a.r, a and a.w
    elseif m.addon == "4" then
      local a = rc and rc.answers[m.name]
      if a then ok, why = a.r, a.w else ok, why = nil, "pas de réponse" end
    else
      ok, why = E.Ready(m)
      why = why or (not m.addon and "sans TreeAI : vu par le jeu") or nil
    end
    if not ok then all = false end
    out[#out + 1] = { name = m.me and "toi" or m.name, ok = ok, why = why }
  end
  return out, all
end

function M:ReadyTick(t)
  local rc = S.rc
  if not rc or rc.done or rc.by ~= myName() then return end
  local states, all = self:ReadyStates(memberList())
  if all then
    rc.done = true
    self:Toast("readyok", "Tout le monde est prêt : go !", 2, { ttl = 10 })
  elseif t - rc.at > READY_TIMEOUT then
    rc.done = true
    local no = {}
    for _, e in ipairs(states) do if not e.ok then no[#no + 1] = e.name .. (e.why and (" (" .. e.why .. ")") or "") end end
    self:Toast("readyno", "Pas prêts : " .. tconcat(no, ", "), 2, { ttl = 15 })
  end
  dirty.view = true
end

-- ------------------------------------------------------------------ point de rencontre (ex-QuickTravel QTM)
local function myPos()
  local D = T.Detect
  if type(D) == "table" and type(D.Me) == "function" then
    local p = call(D.Me)
    if type(p) == "table" and p.mapID and p.x and p.y then return p.mapID, p.x, p.y end
  end
  local Geo = T.Geo
  if type(Geo) == "table" and type(Geo.Player) == "function" then
    local map, x, y = call(Geo.Player)
    if map and x and y then return map, x, y end
  end
  local map = C_Map and pub(call(C_Map.GetBestMapForUnit, "player"))
  local p = map and C_Map.GetPlayerMapPosition and call(C_Map.GetPlayerMapPosition, map, "player")
  if type(p) == "table" then
    local x, y
    if type(p.GetXY) == "function" then x, y = call(p.GetXY, p) else x, y = p.x, p.y end
    x, y = tonumber(pub(x)), tonumber(pub(y))
    if x and y then return map, x, y end
  end
  return nil
end
function M:MyPos() return myPos() end

function M:SendMeet()
  if not inGroup() then return false, "group" end
  local t = now()
  if t - S.lastMeet < MEET_GAP then return false, "gap" end
  local map, x, y = myPos()
  if not (map and x and y) then return false, "pos" end
  if x > 1 or y > 1 then x, y = x / 100, y / 100 end
  local label = clean(call(GetSubZoneText), 24) or clean(call(GetZoneText), 24) or ""
  S.lastMeet = t
  self:QueueEvent("meet", tostring(floor(map)), tostring(floor(x * 1000 + 0.5)), tostring(floor(y * 1000 + 0.5)), label)
  self:Offer("meet", "news", "Point de rencontre envoyé au groupe : ici" .. (label ~= "" and (" (" .. label .. ")") or ""), 1,
    { ttl = 30, sound = false })
  return true
end

--- point reçu (x, y en 0..1) -> { map, x, y, label } ou nil (bridé : 1 / 10 s par membre)
function M:OnMeet(who, map, x, y, label)
  local t = now()
  if t - (S.meetAt[who] or -1e9) < MEET_RECV_GAP then return nil end
  S.meetAt[who] = t
  local function aim()
    local Cp = T.Compass
    if type(Cp) == "table" and type(Cp.SetTarget) == "function" then
      return call(Cp.SetTarget, Cp, { mapID = map, x = x, y = y, label = "Rendez-vous : " .. who, source = "meet" })
    end
  end
  if self.db.meetCompass ~= false and not inCombat() then aim() end
  local where = ""
  local D, Cp = T.Detect, T.Compass
  local p = type(D) == "table" and type(D.Me) == "function" and call(D.Me) or nil
  local G = type(Cp) == "table" and Cp.Geo
  if type(p) == "table" and type(G) == "table" and type(G.Vector) == "function" then
    local d = call(G.Vector, p, { mapID = map, x = x, y = y })
    if tonumber(d) then
      local dt = D.Dist and call(D.Dist, d)
      if not dt then
        local mm = d * 0.9144
        dt = mm >= 1000 and (fmt("%.1f km", mm / 1000):gsub("%.", ",")) or (floor(mm + 0.5) .. " m")
      end
      where = " · " .. dt
    end
  end
  self:Offer("meet", "meet", fmt("Rejoins %s au point de rencontre%s%s", who, (label and label ~= "") and (" (" .. label .. ")") or "",
    where), 2, { ttl = MEET_TTL, sound = true, soundKind = "group", onClick = aim,
      tooltip = { "Point de rencontre", "Envoyé par " .. who, "Clic : la boussole y va" } })
  return { map = map, x = x * 100, y = y * 100, label = label or "" }
end

-- ------------------------------------------------------------------ inspection (équipement) et auras
local function inspectFrameOpen()
  local f = _G.InspectFrame
  return type(f) == "table" and f.IsShown and call(f.IsShown, f) and true or false
end

function M:TryInspect(t)
  if not self.db.gear or inspectFrameOpen() or type(NotifyInspect) ~= "function" then return end
  local p = S.pendingInspect
  if p and t - p.t > INSPECT_TIMEOUT then   -- pas de réponse : ce membre plus tard
    gearCache[p.guid] = gearCache[p.guid] or { t = t, missing = {}, n = 0, partial = true }
    S.pendingInspect, p = nil, nil
  end
  if p or t - S.lastInspect < INSPECT_GAP then return end
  for _, m in ipairs(roster) do
    local c = m.guid and gearCache[m.guid]
    if m.guid and (not c or t - c.t > INSPECT_CACHE or (c.partial and t - c.t > 60)) and m.online ~= false
        and call(UnitIsVisible, m.unit) ~= false
        and (type(CanInspect) ~= "function" or call(CanInspect, m.unit))
        and (type(CheckInteractDistance) ~= "function" or call(CheckInteractDistance, m.unit, 1)) then
      S.lastInspect = t
      S.pendingInspect = { guid = m.guid, unit = m.unit, t = t }
      call(NotifyInspect, m.unit)
      return
    end
  end
end

local function itemInfo(link)
  if API.ItemInfo then return call(API.ItemInfo, link) end
  return call(GetItemInfo, link)
end

function M:ReadGear(unit, guid)
  local c = { t = now(), missing = {}, n = 0, links = {} }
  local sum, cnt = 0, 0
  local det = (C_Item and C_Item.GetDetailedItemLevelInfo) or _G.GetDetailedItemLevelInfo
  for _, slot in ipairs(GEAR_SLOTS) do
    local link = pub(call(GetInventoryItemLink, unit, slot))
    if link then
      c.n = c.n + 1
      c.links[slot] = link      -- DUNGEON2 : butin comparé à l'équipement inspecté (Team.GearOf)
      local name, _, quality, level, _, _, sub = itemInfo(link)
      local il = tonumber((call(det, link))) or tonumber(level)
      if il and il > 0 then sum, cnt = sum + il, cnt + 1 end
      if not name then c.partial = true end
      if slot == 16 then c.weapon = { quality = quality, sub = sub, name = name } end
    elseif SLOT_FR[slot] then
      c.missing[#c.missing + 1] = slot
    end
  end
  c.ilvl = cnt > 0 and floor(sum / cnt + 0.5) or nil
  if c.n == 0 then c.partial = true end
  gearCache[guid] = c
  return c
end

function M:OnInspect(guid)
  guid = pub(guid)
  local p = S.pendingInspect
  if not p or not guid or guid ~= p.guid then return end
  S.pendingInspect = nil
  if pub(call(UnitGUID, p.unit)) ~= guid then return end
  self:ReadGear(p.unit, guid)
  if type(ClearInspectPlayer) == "function" and not inspectFrameOpen() then call(ClearInspectPlayer) end
  dirty.view = true
end

local function spellName(id)
  local n = API.SpellName and call(API.SpellName, id)
  return pub(n)
end
local function knows(ids)
  local key = ids[1]
  local v = knowsCache[key]
  if v == nil then
    v = false
    for _, id in ipairs(ids) do
      if API.IsSpellKnown and call(API.IsSpellKnown, id) then v = id break end
    end
    knowsCache[key] = v
  end
  return v or nil
end

-- auras d'un membre (le cœur ne relaie pas UNIT_AURA des membres du groupe : relues toutes les 15 s, hors combat)
local function readAuras(u)
  local CU = C_UnitAuras
  if not (CU and CU.GetAuraDataByIndex) and type(UnitAura) ~= "function" then return nil end
  local c = { ids = {}, names = {}, debuffs = {} }
  for _, filter in ipairs({ "HELPFUL", "HARMFUL" }) do
    for i = 1, 40 do
      local name, sid, dtype
      if CU and CU.GetAuraDataByIndex then
        local d = call(CU.GetAuraDataByIndex, u, i, filter)
        if type(d) ~= "table" then break end
        name, sid, dtype = pub(d.name), pub(d.spellId), pub(d.dispelName)
      elseif type(UnitAura) == "function" then
        local n, _, _, dt, _, _, _, _, _, id = call(UnitAura, u, i, filter)
        if not n then break end
        name, sid, dtype = pub(n), pub(id), pub(dt)
      else
        break
      end
      if filter == "HELPFUL" then
        if sid then c.ids[sid] = true end
        if name then c.names[name] = true end
      elseif dtype and DISPEL_FR[dtype] then
        c.debuffs[#c.debuffs + 1] = { type = dtype, name = name }
      end
    end
  end
  return c
end

local function hasBuff(c, buff)
  for _, id in ipairs(buff.spells) do if c.ids[id] then return true end end
  if not buff._names then
    buff._names = {}
    for i = 1, mmin(#buff.spells, 3) do local n = spellName(buff.spells[i]); if n then buff._names[n] = true end end
  end
  for n in pairs(buff._names) do if c.names[n] then return true end end
  return false
end

-- quelqu'un (moi ou un membre) peut-il donner un buff / dissiper ? sinon inutile de lire les auras
local function supportUseful()
  local classes = { [me.class or ""] = true }
  for _, m in ipairs(roster) do if m.class and m.online ~= false then classes[m.class] = true end end
  for _, b in ipairs(M.BUFFS) do if classes[b.class] then return true end end
  for _, opts in pairs(M.DISPELS) do for _, o in ipairs(opts) do if classes[o[1]] then return true end end end
  return false
end

function M:PollAuras(t)
  S.lastAura = t
  if not self.db.support or not supportUseful() then return end
  local sig = {}
  local myMap, myZone = C_Map and pub(call(C_Map.GetBestMapForUnit, "player")), clean(call(GetRealZoneText), 30)
  for _, m in ipairs(roster) do
    self:ReadLive(m, false, myMap, myZone)
    local c = (m.unit and m.online ~= false and not m.dead and m.far ~= true) and readAuras(m.unit) or nil
    if c then
      auraCache[m.unit] = c
      local s = {}
      for _, b in ipairs(M.BUFFS) do if not hasBuff(c, b) then s[#s + 1] = b.key end end
      for _, d in ipairs(c.debuffs) do s[#s + 1] = d.type end
      sig[#sig + 1] = m.name .. ":" .. tconcat(s, ",")
    else
      auraCache[m.unit or ""] = nil
    end
  end
  local key = tconcat(sig, "|")
  if key ~= S.auraSig then
    S.auraSig = key
    dirty.view, dirty.urgent = true, true
  end
end

-- ------------------------------------------------------------------ « Avant de tirer » (épinglé dans « Maintenant »)
function M:CheckPull()
  local text
  pullOn = false
  if self.db.pull and #roster > 0 and not inCombat() then
    local ps = T.PlayerState and T:PlayerState() or {}
    if not ps.dead and not ps.ghost and call(UnitExists, "target") and call(UnitCanAttack, "player", "target")
        and not call(UnitIsDead, "target") and not call(UnitIsPlayer, "target") then
      pullOn = true
      local myMap = C_Map and pub(call(C_Map.GetBestMapForUnit, "player"))
      local myZone = clean(call(GetRealZoneText), 30)
      for _, m in ipairs(roster) do self:ReadLive(m, false, myMap, myZone) end
      local why = E.PullBlockers(roster)
      if #why > 0 then text = "Avant de tirer, attends : " .. andList(why, 3) end
    end
  end
  if text ~= pullText then
    pullText = text
    self:Push()
  end
end

function M:PullSoon()
  if S.pullPending then return end
  S.pullPending = true
  if T.After then
    T:After(0.3, function() S.pullPending = false; if M:IsEnabled() then M:CheckPull() end end)
  else
    S.pullPending = false
    self:CheckPull()
  end
end

-- ------------------------------------------------------------------ lignes de l'onglet « Groupe »
local function aimQuest(id, title)
  local Cp = T.Compass
  if type(Cp) == "table" and type(Cp.SetTarget) == "function" and id then
    return call(Cp.SetTarget, Cp, { questID = id, label = title, source = "group" })
  end
end

function M:StateWords(m)
  local w = {}
  if m.online == false then w[1] = "hors ligne" return w end
  if m.ghost then w[#w + 1] = "fantôme" elseif m.dead then w[#w + 1] = "mort" end
  if m.afk then w[#w + 1] = "AFK" end
  -- (GROUPJOIN) distance vraie (positions du jeu / de l'ami TreeAI) quand on l'a : « à 2,7 km (Les Tarides) », « à côté »
  local d
  if M.Join and M.Join.DistTo then d = M.Join.DistTo(m) end
  local near = d and d <= 40
  if d and not near then
    w[#w + 1] = "à " .. M.Join.DistText(d) .. ((m.otherZone and m.zoneName) and (" (" .. m.zoneName .. ")") or "")
  elseif not d and m.otherZone then w[#w + 1] = m.zoneName and ("zone : " .. m.zoneName) or "autre zone"
  elseif not d and m.far then w[#w + 1] = "loin" end
  if not m.dead then
    if m.hp and m.hp < 90 then w[#w + 1] = fmt("PV %d %%", m.hp) end
    if m.mana and m.mana < 80 and MANA[m.class or ""] then w[#w + 1] = fmt("mana %d %%", m.mana) end
    if m.petMissing then w[#w + 1] = "sans familier" end
  end
  local st = m.addon and m.st
  if st and st.lm and st.lm >= 0 then w[#w + 1] = st.lm <= 1 and "part maintenant" or fmt("part dans %d min", st.lm) end
  if st and st.rd == 0 then w[#w + 1] = "pas prêt" end
  local jw = M.Join and M.Join.StateWords and M.Join.StateWords(m)
  if jw then w[#w + 1] = jw end
  if #w == 0 and (near or (d == nil and m.far == false)) then w[1] = "à côté" end
  return w
end

function M:CardLines(add, m)
  local db = self.db
  local head = { m.name }
  local x = m.addon and m.st and m.st.x
  head[#head + 1] = (CLASS_FR[m.class or ""] or "Niveau") .. " " .. tostring(m.level or "?") .. (x and fmt(" (%d %%)", x) or "")
  if m.role and ROLE_FR[m.role] then head[#head + 1] = ROLE_FR[m.role] end
  for _, w in ipairs(self:StateWords(m)) do head[#head + 1] = w end
  local tip = { m.name .. (CLASS_FR[m.class or ""] and (" — " .. CLASS_FR[m.class]) or "") }
  if m.addon == "4" then tip[#tip + 1] = "TreeAI : oui (synchro du groupe)"
  elseif m.addon == "3" then tip[#tip + 1] = "TreeAI 3.9 : demande-lui de mettre à jour (il ne voit pas ton état)"
  else
    tip[#tip + 1] = "Sans TreeAI : état lu par le jeu (niveau, PV, portée, zone, position hors instance)"
    tip[#tip + 1] = "Inconnus sans TreeAI : son étape, sa progression, ses quêtes autres que les tiennes"
  end
  if m.addon and m.st and m.st.r and m.st.r > 0 then tip[#tip + 1] = fmt("Repos : %d %% d'un niveau", m.st.r) end
  add(tconcat(head, " · "), 1, { icon = CLASS_ICON[m.class or ""], tooltip = tip })
  if m.online == false then return end
  -- étape en cours (amis TreeAI)
  local s = m.addon and m.step
  if s and s.l then
    local txt = s.l
    local qq = s.q > 0 and m.quests[s.q]
    local o = qq and s.k == "obj" and qq.obj[s.o]
    if o then txt = txt .. fmt(" (%d/%d)", o.have, o.need) end
    local mineToo = s.q > 0 and me.quests[s.q] ~= nil
    add(m.name .. " : " .. txt, 1, { questID = mineToo and s.q or nil,
      tooltip = { "Étape en cours de " .. m.name .. " (son trajet TreeAI)",
        mineToo and "Tu as cette quête aussi : faites-la ensemble" or nil,
        mineToo and "Clic : la boussole vise cette quête" or nil },
      onClick = mineToo and function() aimQuest(s.q, txt) end or nil })
  end
  -- buffs / dissipations qu'un autre peut lui donner
  local sp = S.plan and S.plan.per[m.name]
  if sp then
    add(m.name .. " : " .. tconcat(sp, " · "), 1, { tooltip = { "Ce qui lui manque et qui peut le lui donner",
      "Dis-le à celui qui peut (ou fais-le si c'est toi)" } })
  end
  -- signaux d'alerte
  if db.gear then
    local f, prio = E.Flags(m)
    if #f > 0 then
      local c = m.guid and gearCache[m.guid]
      local tip4 = { "Signaux d'alerte de " .. m.name }
      if c and c.weapon and c.weapon.name then tip4[#tip4 + 1] = "Arme : " .. tostring(c.weapon.name) end
      if c and c.ilvl then tip4[#tip4 + 1] = fmt("Niveau d'objet moyen : %d", c.ilvl) end
      tip4[#tip4 + 1] = "Dis-le-lui : il pourra s'en occuper au prochain marchand / à l'HV"
      add(m.name .. " : " .. tconcat(f, " · "), prio, { tooltip = tip4 })
    end
  end
end

-- buffs / dissipations : ce que JE peux lancer = lignes épinglées ; ce qu'un autre peut donner = sur la carte du membre
function M:SupportPlan()
  local plan = { cast = {}, per = {} }
  local st = T.PlayerState and T:PlayerState() or {}
  if st.dead or st.ghost or st.taxi then return plan end
  local myClass = me.class
  local function per(name, part)
    plan.per[name] = plan.per[name] or {}
    local p = plan.per[name]
    p[#p + 1] = part
  end
  for _, b in ipairs(M.BUFFS) do
    local mineSpell = myClass == b.class and knows(b.spells)
    local giver
    if not mineSpell then
      for _, m in ipairs(roster) do if m.class == b.class and m.online ~= false and not m.dead then giver = m break end end
    end
    if mineSpell or giver then
      local sname = spellName(mineSpell or b.spells[1]) or b.name
      local lack = {}
      for _, m in ipairs(roster) do
        local c = m.unit and auraCache[m.unit]
        if c and m ~= giver and not m.dead and m.online ~= false and (not b.mana or m.manaUser) and not hasBuff(c, b) then
          if mineSpell then lack[#lack + 1] = m.name else per(m.name, fmt("manque %s (%s)", sname, giver.name)) end
        end
      end
      if #lack > 0 and not st.mounted then
        plan.cast[#plan.cast + 1] = { text = fmt("Lance %s sur %s", sname, andList(lack, 3)),
          tooltip = { "Buff de classe manquant", "Clique son portrait (cadre du groupe) puis lance le sort" } }
      end
    end
  end
  for _, m in ipairs(roster) do
    local c = m.unit and auraCache[m.unit]
    if c and #c.debuffs > 0 and not m.dead and m.online ~= false then
      local seen = {}
      for _, d in ipairs(c.debuffs) do
        if not seen[d.type] then
          seen[d.type] = true
          local can, other
          for _, opt in ipairs(M.DISPELS[d.type] or {}) do
            if opt[1] == myClass and knows(opt[2]) then can = spellName(knows(opt[2])) or opt[3] break end
          end
          if not can then
            for _, o in ipairs(roster) do
              for _, opt in ipairs(M.DISPELS[d.type] or {}) do
                if o ~= m and o.class == opt[1] and o.online ~= false and not o.dead then other = o break end
              end
              if other then break end
            end
          end
          local what = DISPEL_FR[d.type]
          if can then
            plan.cast[#plan.cast + 1] = { text = fmt("Dissipe %s : %s (%s)", m.name, what, can),
              tooltip = d.name and { what .. " : " .. tostring(d.name) } or nil }
          elseif other then
            per(m.name, fmt("%s à retirer (%s)", what, other.name))
          end
        end
      end
    end
  end
  return plan
end

function M:ReadyLine(list)
  local states = self:ReadyStates(list)
  local bits = {}
  for _, e in ipairs(states) do
    bits[#bits + 1] = e.name .. " " .. (e.ok and "oui" or (e.ok == false and e.why and e.why ~= "pas de réponse") and ("non (" .. e.why .. ")") or "?")
  end
  return "Prêt ? " .. tconcat(bits, " · ")
end

function M:Build()
  local t = now()
  S.lastBuild, dirty.view, dirty.live, dirty.urgent = t, false, false, false
  S.hadLines = true
  local db = self.db
  self:Refresh()
  local list = memberList()
  if S.indexDirty or not S.index then
    S.index, S.indexDirty = E.Index(list), false
    local board = E.Board(S.index, me.quests, me.name)
    MI.board, MI.list, MI.next = board, list, E.Next(board, me.name)
    self.board, self.next = board, MI.next
  end
  local lines = {}
  local function add(text, prio, extra)
    local l = extra or {}
    l.text, l.prio = text, prio or 1
    lines[#lines + 1] = l
    return l
  end
  local function into(list2) return function(text, prio, extra)
    local l = extra or {}
    l.text, l.prio = text, prio or 1
    list2[#list2 + 1] = l
    return l
  end end
  local function section(title, items)   -- un en-tête seulement s'il y a des lignes dessous
    if #items == 0 then return end
    add(title, 1, { header = true })
    for _, l in ipairs(items) do lines[#lines + 1] = l end
  end
  -- 1. « Prêt ? » demandé par moi
  local rc = S.rc
  if rc and rc.by == me.name and t - rc.at <= READY_TIMEOUT + 10 then
    add(self:ReadyLine(list), rc.done and 1 or 2, { tooltip = { "Vérification « prêt ? »", "Amis TreeAI : leur réponse ; les autres : vus par le jeu" } })
  end
  -- 2. annonces en cours (la plus importante est aussi LA ligne du groupe dans « Maintenant »)
  local nNews = 0
  local INPAGE = { join = true, intent = true, plan = true, vote = true, rdv = true }   -- déjà dites dans leur section
  for _, e in ipairs(newsSorted()) do
    if not INPAGE[e.kind] and nNews < 4 then
      nNews = nNews + 1
      add(e.text, e.prio, { kind = "group", onClick = e.opts and e.opts.onClick, tooltip = e.opts and e.opts.tooltip })
    end
  end
  -- 3. soutien : ce que je peux lancer (le 1er épinglé si rien de plus important) ; le reste va sur la carte de chacun
  local plan = (db.support and not inCombat()) and self:SupportPlan() or nil
  S.plan = plan
  S.castTop = (plan and plan.cast[1]) and true or false
  for i, c in ipairs(plan and plan.cast or {}) do add(c.text, 2, { kind = "group", tooltip = c.tooltip, _cast = i }) end
  -- 4. choix de l'objectif (ouvert par le bouton « Objectif »)
  if S.picking and t - S.picking < 60 then
    add("Choisis l'objectif du groupe (partagé avec les amis TreeAI) :", 1, { header = true })
    for _, c in ipairs(self:GoalChoices(list)) do
      local kind, value = c.kind, c.value
      add(c.text, 1, { kind = "group", onClick = function() M:SetGoal(kind, value) end, clickHint = "Clic : choisir cet objectif" })
    end
    add("Pas d'objectif", 1, { onClick = function() M:SetGoal("off") end, clickHint = "Clic : retirer l'objectif" })
    add("Annuler", 1, { onClick = function() M:Picker(false) end, clickHint = "Clic : fermer ce choix" })
  end
  -- 5. « Ce que veut le groupe » : l'intention déduite (une ligne), où va chacun, objectif, prochain arrêt commun, vote,
  -- trajets partagés, donjon prévu
  local shown = T.TabShown and T:TabShown("group") or false
  local JL = self.Join and self.Join.Sections and self.Join.Sections(list, shown, t) or nil
  local want = {}
  local addW = into(want)
  for _, l in ipairs(JL and JL.intent or {}) do want[#want + 1] = l end
  if db.goals and not (S.picking and t - S.picking < 60) then
    local g = self:Goal()
    local gl = E.GoalLine(g, list)
    if gl then
      if gl.done and not self.char.goalDone then
        self.char.goalDone = epoch()
        self:Toast("goaldone", gl.text, 2, { ttl = 30 })
      elseif gl.done and epoch() - (tonumber(self.char.goalDone) or epoch()) > GOAL_DONE_KEEP then
        self.char.goal, self.char.goalDone, gl = false, false, nil
      end
      if gl then addW(gl.text, gl.prio, { tooltip = gl.tip, onClick = function() M:Picker(true) end, clickHint = "Clic : changer l'objectif" }) end
    end
  end
  if db.goals then
    local nx = self.next
    if nx then
      local e = nx.mission
      local mineQ = me.quests[e.id] ~= nil
      addW(nx.text, nx.kind == "turnin" and 2 or 1, { questID = mineQ and e.id or nil,
        tooltip = { "Prochain arrêt commun", E.StatusLine(e), mineQ and "Clic : la boussole vise cette quête" or nil },
        onClick = mineQ and function() aimQuest(e.id, e.title) end or nil })
    end
  end
  for _, l in ipairs(JL and JL.group or {}) do want[#want + 1] = l end
  section("Ce que veut le groupe", want)
  -- 6. « Rejoindre » : au plus vite / rentable vers chacun, point de rencontre, rendez-vous
  section("Rejoindre", JL and JL.join or {})
  -- 7. écarts de niveau (avec les quêtes qui l'aident), puis les quêtes à partager / demander (une ligne par quête)
  local share = {}
  local addS = into(share)
  local covered = {}
  for _, l in ipairs(E.LevelGaps(list, me.quests, me.level, pushable)) do
    addS(l.text, l.prio, { tooltip = l.tooltip })
    local c = {}
    for _, id in ipairs(l.ids or {}) do c[id] = true end
    covered[l.who] = c
  end
  if db.quests then
    for _, l in ipairs(E.ShareLines(list, me.quests, me.level, pushable, covered)) do
      addS(l.text, l.prio, { tooltip = l.tooltip, questID = l.questID })
    end
  end
  section("Quêtes à partager", share)
  -- 8. « Ce que fait chacun » : une carte par membre (étape, zone, niveau, % de niveau), puis la course à l'XP
  local cards = {}
  local addC = into(cards)
  if db.cards then for _, m in ipairs(list) do if not m.me then self:CardLines(addC, m) end end end
  if db.race then
    local lb = E.Leaderboard(list)
    if #lb >= 2 then
      local bits = {}
      for i, e in ipairs(lb) do if i <= 4 then bits[#bits + 1] = (e.me and "toi" or e.name) .. " +" .. numText(e.xs) end end
      addC("XP de la soirée : " .. tconcat(bits, " · "), 1, { tooltip = { "XP gagnée depuis le début de la soirée",
        "Amis TreeAI seulement (exacte, comptée par leur jeu)" } })
    end
  end
  section("Ce que fait chacun", cards)
  body = lines
  self:Push()
end

function M:Push()
  if not T.SetTabLines then return end
  self:NowApply()   -- qui a LA ligne du groupe dans « Maintenant » (épinglée ou annonce)
  local out = {}
  if pullText then
    out[1] = { text = pullText, prio = 2, pin = S.pinNow or nil, kind = "warn",
      tooltip = { "Lu sur le jeu (cadres du groupe), même sans TreeAI chez eux", "Disparaît dès que tout le monde est prêt" } }
  end
  for i = 1, #body do
    local l = body[i]
    if l._cast then l.pin = (S.pinNow and not pullText and l._cast == 1) or nil end
    out[#out + 1] = l
  end
  if inGroup() and #roster > 0 then
    out[#out + 1] = { text = "Choisir l'objectif du groupe", label = "Objectif", control = true, prio = 1,
      tooltip = { "Objectif du groupe", "Niveau à atteindre ensemble ou zone à finir — partagé avec les amis TreeAI" },
      onClick = function() M:Picker(not S.picking) end }
    out[#out + 1] = { text = "Demander au groupe s'il est prêt", label = "Prêt ?", control = true, prio = 1,
      tooltip = { "Chef : l'appel « prêt » du jeu (tout le monde le voit)", "Sinon : la vérification TreeAI (PV, mana, familier, AFK)" },
      onClick = function() M:ReadyCheck() end }
    for _, c in ipairs(self.Join and self.Join.Controls and self.Join.Controls(memberList()) or {}) do out[#out + 1] = c end
  end
  T:SetTabLines("group", out, "Group")
end

-- ------------------------------------------------------------------ annonces
local SOUND_EVENTS = { join = true, level = true, ready = true, readyok = true, goal = true, goaldone = true, meet = true,
  mission = true, target = true, death = true }
M.SOUND_EVENTS = SOUND_EVENTS
MI.SOUND_EVENTS = SOUND_EVENTS
function M:Toast(key, text, prio, opts)
  if not self.db.toasts then return end
  opts = opts or {}
  local k0 = tostring(key):match("^[^:]+")
  self:Offer(key, "news", text, prio or 1, { ttl = opts.ttl or 15, cooldown = opts.cooldown or 60,
    onClick = opts.onClick, tooltip = opts.tooltip, sound = SOUND_EVENTS[k0] or nil,
    soundKind = SOUND_EVENTS[k0] and "group" or nil })
end

-- « Attends Bravo » au moment de rendre une quête qui ouvre une suite (QUEST_COMPLETE : lecture de GetQuestID)
function M:OnTurnIn()
  local id = tonumber(call(GetQuestID))
  if not id or #roster == 0 then return end
  local QM = ns.Quests or (T.GetModule and T:GetModule("Quests"))
  local chain = type(QM) == "table" and type(QM.QuestieNext) == "function" and call(QM.QuestieNext, id)
  if not chain then return nil end
  local who, title = {}, nil
  for _, m in ipairs(roster) do
    local qq = m.addon and m.quests[id]
    if qq and not qq.done then
      local h, n = E.Progress(qq)
      who[#who + 1] = fmt("%s (%d/%d)", m.name, h, n)
      title = title or qq.title
    end
  end
  if #who == 0 then return nil end
  title = title or (me.quests[id] and me.quests[id].title) or ("quête #" .. id)
  local text = fmt("Attends %s : %s ouvre une suite, rendez ensemble", andList(who, 3), q(title))
  self:Offer("turnin", "turnin", text, 2, { ttl = 20, cooldown = 30 })
  return text
end

function M:OnLootRoll(rollID)
  if #roster == 0 or call(IsInInstance) then return end   -- en donjon : le module Donjon conseille les jets
  local _, name = call(GetLootRollItemInfo, rollID)
  local link = call(GetLootRollItemLink, rollID)
  if not (name or link) then return end
  local iname, _, _, _, _, _, _, _, equipLoc, _, _, classID, subClassID = itemInfo(link or name)
  local hint = E.LootHint({ name = iname or name, classID = classID, subClassID = subClassID, equipLoc = equipLoc }, memberList())
  if hint then self:Offer("roll" .. tostring(rollID), "news", hint, 1, { ttl = 60, cooldown = 60 }) end
end

-- ------------------------------------------------------------------ session (résumé pour l'appli)
function M:Session()
  local c = self.char
  if not c then return nil end
  local s = c.session
  if type(s) ~= "table" or epoch() - (tonumber(s.last) or 0) > SESSION_GAP then
    s = { start = epoch(), last = epoch(), turned = 0, titles = {}, deaths = {}, levels = {}, events = {} }
    c.session = s
  end
  return s
end
function M:Event(s, text)
  s.last = epoch()
  table.insert(s.events, 1, { t = epoch(), text = text })
  while #s.events > 12 do tremove(s.events) end
end
function M:Record(kind, arg)
  if #roster == 0 then return end
  local s = self:Session()
  if not s then return end
  if kind == "turnin" then
    local qq = me.quests[tonumber(arg) or 0]
    s.turned = s.turned + 1
    if qq and qq.title and #s.titles < 20 then s.titles[#s.titles + 1] = qq.title end
    self:Event(s, "Tu as rendu " .. q(qq and qq.title or "une quête"))
  elseif kind == "death" then
    s.deaths[arg] = (s.deaths[arg] or 0) + 1
    self:Event(s, "Tu es mort")
  end
end

-- instantanés lus par TreeAI Coach (treeaicoach/wow/ai_context.py, live.py) : formats d'avant gardés
function M:SaveSummary()
  if not T.Summary then return end
  local ok, sm = pcall(T.Summary, T)
  if not ok or type(sm) ~= "table" then return end
  if #roster == 0 then return end
  self:Refresh()
  local list = memberList()
  local index = S.index or E.Index(list)
  -- summary.group (v2, ex-Squad)
  local g = { v = 2, updated = epoch(), size = #list, members = {}, advices = {}, common = {}, leader = SQ.Leader() }
  for _, m in ipairs(list) do
    local st = m.st
    local known = m.me or m.addon
    g.members[#g.members + 1] = { name = m.name, class = CLASS_FR[m.class or ""] or m.class, level = m.level,
      xp = known and st.x or nil, rested = known and st.r or nil, zone = m.me and mine.z or m.zoneName,
      hp = m.hp, mana = m.mana, dead = m.dead or nil, bags = known and st.b or nil,
      durability = (known and st.du and st.du >= 0) and st.du or nil, ammo = (known and st.am and st.am >= 0) and st.am or nil,
      leaveMin = (known and st.lm and st.lm >= 0) and st.lm or nil, ready = not (known and st.rd == 0),
      afk = m.afk or nil, professions = known and st.pr or nil, me = m.me or nil, treeai = known and true or nil }
  end
  for _, l in ipairs(body) do
    if #g.advices >= 8 then break end
    if not l.control and not l.header and l.prio and l.text then g.advices[#g.advices + 1] = { cat = "group", prio = l.prio, text = l.text } end
  end
  -- (GROUPJOIN) ce que le groupe semble vouloir faire : l'intention DÉDUITE seulement (jamais de texte du chat)
  local js = self.Join and self.Join.Summary and self.Join.Summary()
  if js then g.intent = js end
  for i, e in ipairs(index) do
    if i > 10 then break end
    g.common[#g.common + 1] = { id = e.id, title = e.title, holders = #e.holders, done = #e.done, group = e.group or nil }
  end
  sm.group = g
  -- summary.missions (v1, ex-Missions)
  local board = MI.board or {}
  local mi = { v = 1, updated = epoch(), size = #list, members = {}, board = {}, assign = {}, claims = {}, crafts = {} }
  local noAddon = 0
  for _, m in ipairs(list) do
    if m.me or m.addon then mi.members[#mi.members + 1] = { name = m.name, class = CLASS_FR[m.class or ""] or m.class, level = m.level }
    else noAddon = noAddon + 1 end
  end
  mi.noAddon = noAddon
  for i, e in ipairs(board) do
    if i > 10 then break end
    mi.board[#mi.board + 1] = { id = e.id, title = e.title, holders = #e.holders, done = #e.done, pct = e.pct,
      ready = e.allDone or nil, status = (not e.allDone) and E.StatusLine(e) or nil, missing = #e.missing }
  end
  if MI.next then mi.next = MI.next.text end
  local rg = E.Regroup(list)
  if rg then mi.travel = { rg.text } end
  local s = self.char and self.char.session
  if type(s) == "table" then
    local ses = { start = s.start, duration = epoch() - (s.start or epoch()), turned = s.turned, missions = 0, abandoned = 0,
      titles = {}, deaths = 0, levels = {}, events = {}, leaderboard = {}, loot = {} }
    for i, x in ipairs(s.titles or {}) do if i <= 10 then ses.titles[i] = x end end
    for _, c in pairs(s.deaths or {}) do ses.deaths = ses.deaths + c end
    for name, lv in pairs(s.levels or {}) do ses.levels[#ses.levels + 1] = { name = name, from = lv.from, to = lv.to } end
    for i, ev in ipairs(s.events or {}) do if i <= 8 then ses.events[i] = ev.text end end
    local gl = E.GoalLine(self:Goal(), list)
    if gl then ses.goal = { kind = self:Goal().k, text = gl.text, done = gl.done or nil } end
    for i, e in ipairs(E.Leaderboard(list)) do if i <= 5 then ses.leaderboard[i] = { name = e.name, xp = e.xs } end end
    mi.session = ses
  end
  sm.missions = mi
  -- summary.team (v1, ex-Team, sans noms)
  local tm = { v = 1, members = {} }
  for _, m in ipairs(roster) do
    local c = m.guid and gearCache[m.guid]
    tm.members[#tm.members + 1] = { class = m.class, level = m.level, role = ROLE_FR[m.role or ""], addon = m.addon and true or nil,
      dead = m.dead or nil, far = m.far or nil, otherZone = m.otherZone or nil, ilvl = c and c.ilvl or nil,
      missingSlots = c and #(c.missing or {}) or nil, greyWeapon = (c and c.weapon and c.weapon.quality == 0) or nil }
  end
  sm.team = tm
end

-- ------------------------------------------------------------------ façades : API des anciens modules
local function treeaiList()
  local list = {}
  for _, m in ipairs(memberList()) do
    if m.me or m.addon then
      local lm = m.st.lm
      list[#list + 1] = { name = m.name, st = m.st, quests = m.quests or {}, leaveMin = (lm and lm >= 0) and lm or nil,
        stale = nil, step = m.step }
    end
  end
  return list
end
function SQ.IsActive() return (M._active and M:IsEnabled()) and true or false end
function SQ.MemberList() return treeaiList() end
function SQ.Members()
  local out = {}
  for _, m in ipairs(treeaiList()) do out[#out + 1] = m.name end
  return out
end
function SQ.State(_, name)
  if name == nil or name == me.name then return me.st end
  local m = members[shortName(name) or ""]
  return (m and m.addon) and m.st or nil
end
function SQ.Quests(_, name)
  if name == nil or name == me.name then return me.quests end
  local m = members[shortName(name) or ""]
  return (m and m.addon) and m.quests or {}
end
function SQ.Common() return S.index or E.Index(memberList()) end
function SQ.Advices()
  local out = {}
  for _, l in ipairs(body) do if l.text then out[#out + 1] = { id = l.text, cat = "group", prio = l.prio, text = l.text } end end
  return out
end
function SQ.Leader()
  if call(UnitIsGroupLeader, "player") then return me.name end
  for _, m in ipairs(roster) do if call(UnitIsGroupLeader, m.unit) then return m.name end end
  return nil
end
function SQ.Size()   -- (Squad) membres qui ont TreeAI, moi compris
  local n = 1
  for _, m in ipairs(roster) do if m.addon then n = n + 1 end end
  return n
end
function SQ.IsMember(_, sender) return M:IsMember(sender) end
function SQ.PushGroupTab() M:Build() end
function SQ.Recompute() M:Build() end
function SQ.Tick() M:Tick() end
function SQ.CollectState() M:CollectMine(true) return mine end
function SQ.SetLeave(_, n) return M:SetLeave(n) end
function SQ.SetReady(_, a) return M:SetReady(a) end
SQ.DecodeState = L.DecodeState
SQ.Engine.LootHint = E.LootHint

function MI.ReadyAsk() return M:ReadyAsk() end
function MI.ReadyAnswer(_, yes, why) return M:ReadyAnswer(yes, why) end
function MI.SetGroupTarget(_, e)
  e = e or (MI.next and MI.next.mission)
  if not e then return false end
  return aimQuest(e.id, e.title) ~= nil
end

function TM.GearOf(guid) return guid and gearCache[guid] or nil end
M.GearOf = TM.GearOf

-- ------------------------------------------------------------------ commandes (réponses courtes, rien d'autre au chat)
function M:SetLeave(minutes)
  local c = self.char
  if not c then return end
  minutes = tonumber(minutes)
  if not minutes then
    local lm = leaveMinutes(c)
    self:Print(lm >= 0 and fmt("Départ annoncé dans %d min (/tai partir 0 pour annuler).", lm) or "Usage : /tai partir 20")
    return
  end
  if minutes <= 0 then c.leaveAt = 0; self:Print("Départ annulé.")
  else
    minutes = floor(mmin(minutes, 600))
    c.leaveAt = epoch() + minutes * 60
    self:Print(fmt("Le groupe saura que tu pars dans %d min.", minutes))
  end
end
function M:SetReady(arg)
  local c = self.char
  if not c then return end
  arg = tostring(arg or ""):lower()
  if arg == "on" or arg == "oui" then c.ready = true
  elseif arg == "off" or arg == "non" then c.ready = false
  else c.ready = not (c.ready ~= false) end
  self:Print(c.ready ~= false and "Tu es prêt." or "Le groupe voit que tu n'es pas prêt.")
end
local function openTab()
  if T.ShowPanel then call(T.ShowPanel, T) end
  if T.SelectTab then call(T.SelectTab, T, "group") end
  dirty.view, dirty.urgent = true, true
end
--- la page Groupe (la fenêtre TreeAI quand la carte remplace le panneau : Window enveloppe SelectTab)
function M:OpenPage()
  if T.SelectTab then call(T.SelectTab, T, "group") end
  dirty.view, dirty.urgent = true, true
end
function M:SlashGoal(rest)
  rest = tostring(rest or ""):lower():gsub("^%s+", ""):gsub("%s+$", "")
  local lv = rest:match("^niveau%s+(%d+)") or rest:match("^(%d+)$")
  if rest == "off" or rest == "aucun" then self:SetGoal("off")
  elseif lv then self:SetGoal("level", lv)
  elseif rest:match("^zone") then
    local z = rest:match("^zone%s+(.+)$")
    local choice
    for _, c in ipairs(self:GoalChoices(memberList())) do
      if c.kind == "zone" and (not z or c.value:lower():find(z, 1, true)) then choice = choice or c end
    end
    if choice then self:SetGoal("zone", choice.value) else self:Print("Aucune quête de cette zone dans ton journal.") end
  else
    self:Picker(true)
    openTab()
  end
end

-- ------------------------------------------------------------------ ticker (2 s) : tout le travail, hors combat
function M:Tick()
  local t = now()
  if not inGroup() or #roster == 0 then
    if #roster > 0 or next(members) then self:ScanRoster() end
    if S.hadLines or next(news) then
      S.hadLines, body, pullText = false, {}, nil
      for k in pairs(news) do news[k] = nil; self:Clear(k) end
      if self.Join and self.Join.Reset then self.Join.Reset() end
      if mine.j then self:SetJoin(nil) end
      if T.SetTabLines then T:SetTabLines("group", {}, "Group") end
    end
    return
  end
  if inCombat() then return end
  if S.picking and t - S.picking >= 60 then S.picking, dirty.urgent = nil, true end   -- choix d'objectif laissé ouvert
  if dirty.quests and t - S.lastScan >= QUEST_GAP then self:ScanQuests() end
  self:CollectMine()
  self:PollStep(t)
  if dirty.unitQ and t - S.lastUnitQ >= UNITQ_GAP then self:ScanUnitQuests() end
  self:Flush(t)
  self:TryInspect(t)
  if t - S.lastAura >= AURA_GAP then self:PollAuras(t) end
  self:ReadyTick(t)
  if self.Join and self.Join.Tick then self.Join.Tick(t, memberList()) end   -- (GROUPJOIN) trajet en cours, vote, positions
  self:NowApply()
  if pullOn and dirty.live then self:CheckPull() end
  local shown = T.TabShown and T:TabShown("group") or false
  local gap = t - S.lastBuild
  if (shown and not S.shown) or (dirty.urgent and gap >= 2) or (dirty.view and gap >= (shown and BUILD_SHOWN or BUILD_HIDDEN))
      or (shown and dirty.live and gap >= BUILD_SHOWN + 1) then
    self:Build()
  end
  S.shown = shown
end

-- ------------------------------------------------------------------ cycle de vie
-- (4.0) une fois : réglages des anciens modules repris (allumé si l'un d'eux l'était), leurs données effacées
function M:Migrate()
  local db = T.db
  if type(db) ~= "table" or type(self.db) ~= "table" or self.db.v4 then return end
  self.db.v4 = true
  local stores = { db.modules }
  local c = T.CharDB and T:CharKey() ~= "?" and T:CharDB()
  if type(c) == "table" and type(c.moduleSettings) == "table" then stores[#stores + 1] = c.moduleSettings end
  local wasOn = false
  for _, s in ipairs(stores) do
    if type(s) == "table" then
      for _, n in ipairs({ "Squad", "Missions", "Team" }) do
        local o = type(s[n]) == "table" and s[n]
        if o then
          if o.enabled == true then wasOn = true end
          if n == "Team" and o.gear == false then self.db.gear = false end
          if n == "Team" and o.buffs == false and o.cleanse == false then self.db.support = false end
          s[n] = nil
        end
      end
    end
  end
  if wasOn then self.db.enabled = true end
  for _, ch in pairs(type(db.chars) == "table" and db.chars or {}) do
    if type(ch) == "table" and type(ch.modules) == "table" then
      ch.modules.Squad, ch.modules.Missions, ch.modules.Team = nil, nil, nil
    end
  end
end

function M:OnInit()
  self:Migrate()
  if T.RegisterSlash then
    T:RegisterSlash("groupe", function() openTab() end, "groupe : l'onglet Groupe (tes amis, objectif, quêtes à partager)")
    T:RegisterSlash("mission", function() openTab() end, "mission : l'onglet Groupe")
    T:RegisterSlash("objectif", function(rest) M:SlashGoal(rest) end, "objectif [niveau 30 | zone | off] : l'objectif du groupe")
    T:RegisterSlash("partir", function(rest) M:SetLeave(rest) end, "partir N : prévenir le groupe que tu pars dans N min")
    T:RegisterSlash("pret", function(rest) M:SetReady(rest) end, "pret [on|off] : signaler si tu es prêt")
  end
end

function M:OnEnable()
  me.class = select(2, call(UnitClass, "player"))
  if T.RegisterComm then
    for _, kind in ipairs({ "GRP", "SQS", "SQQ", "SQD", "QTM" }) do
      T:RegisterComm(kind, M, function(_, payload, sender) M:OnComm(kind, payload, sender) end)
    end
  end
  self:RegisterEvent("GROUP_ROSTER_UPDATE", function() M:ScanRoster() end)
  self:RegisterEvent("PLAYER_ENTERING_WORLD", function()
    M:ScanRoster(); dirty.zone = true
    if M.Join and M.Join.OnWorld then call(M.Join.OnWorld) end   -- entrée de donjon apprise (ma dernière position dehors)
  end)
  -- (GROUPJOIN) chat de groupe LU (jamais écrit) : intentions (« on va au kraal », « rdv à Taurajo »…), en mémoire seulement
  local function partyChat(_, _, text, sender) if M.Join and #roster > 0 then M.Join.OnChat(text, sender) end end
  self:RegisterEvent("CHAT_MSG_PARTY", partyChat)
  self:RegisterEvent("CHAT_MSG_PARTY_LEADER", partyChat)
  self:RegisterEvent("ZONE_CHANGED_NEW_AREA", function() dirty.zone, dirty.view, dirty.live = true, true, true end)
  self:RegisterEvent("QUEST_LOG_UPDATE", function() dirty.quests = true end)
  self:RegisterEvent("UNIT_QUEST_LOG_CHANGED", function(_, _, u) if partyUnit(u) then dirty.unitQ = true end end)
  self:RegisterEvent("PLAYER_XP_UPDATE", function() M:TrackXP(); dirty.xp = true end)
  self:RegisterEvent("UPDATE_EXHAUSTION", function() dirty.xp = true end)
  self:RegisterEvent("PLAYER_LEVEL_UP", function() dirty.xp, dirty.view = true, true end)
  self:RegisterEvent("BAG_UPDATE_DELAYED", function() dirty.bags = true end)
  self:RegisterEvent("UPDATE_INVENTORY_DURABILITY", function() dirty.dura = true end)
  self:RegisterEvent("SKILL_LINES_CHANGED", function() dirty.prof = true end)
  self:RegisterEvent("SPELLS_CHANGED", function() knowsCache = {} end)
  local function live(_, _, u) if partyUnit(u) then dirty.live = true end end
  self:RegisterEvent("UNIT_HEALTH", live)
  self:RegisterEvent("UNIT_POWER_UPDATE", live)
  local function status(_, _, u) if partyUnit(u) then dirty.live, dirty.view = true, true end end
  for _, ev in ipairs({ "UNIT_CONNECTION", "UNIT_LEVEL", "PLAYER_FLAGS_CHANGED", "UNIT_FLAGS", "UNIT_PET" }) do
    self:RegisterEvent(ev, status)
  end
  self:RegisterEvent("PLAYER_TARGET_CHANGED", function() if #roster > 0 then M:PullSoon() end end)
  self:RegisterEvent("PLAYER_REGEN_DISABLED", function() if pullText then pullText = nil; M:Push() end end)
  self:RegisterEvent("PLAYER_REGEN_ENABLED", function() if #roster > 0 then dirty.view = true; M:PullSoon() end end)
  self:RegisterEvent("INSPECT_READY", function(_, _, guid) M:OnInspect(guid) end)
  self:RegisterEvent("UNIT_INVENTORY_CHANGED", function(_, _, u)
    if partyUnit(u) then local g = pub(call(UnitGUID, u)); if g and gearCache[g] then gearCache[g].t = -1e9 end end
  end)
  self:RegisterEvent("QUEST_COMPLETE", function() M:OnTurnIn() end)
  self:RegisterEvent("QUEST_TURNED_IN", function(_, _, id) M:Record("turnin", id) end)
  self:RegisterEvent("PLAYER_DEAD", function() M:Record("death", myName()) end)
  self:RegisterEvent("START_LOOT_ROLL", function(_, _, rollID) M:OnLootRoll(rollID) end)
  self:RegisterEvent("PLAYER_LOGOUT", function() M:SaveSummary() end)
  self:On("SUMMARY", function() M:SaveSummary() end)
  self:TrackXP()
  self:ScanRoster()
  if inGroup() and #roster > 0 then S.wantFull, S.req = true, { "*" } end
  for k in pairs(dirty) do dirty[k] = true end
  dirty.urgent = false
  if T.NewTicker and not self._ticker then
    self._ticker = T:NewTicker(TICK, function() if M:IsEnabled() then M:Tick() end end)
  end
end

function M:OnDisable()
  if inGroup() and T.Send and #roster > 0 then T:Send("GRP", PROTO .. ";B", "PARTY") end
  if self._ticker and self._ticker.Cancel then self._ticker:Cancel() end
  self._ticker = nil
  for _, m in pairs(members) do forgetAddon(m) end
  body, pullText, pullOn, S.rc, S.picking = {}, nil, false, nil, nil
  S.seq, sentVal, sentEntries, S.index = 0, {}, {}, nil
  for k in pairs(news) do news[k] = nil; self:Clear(k) end
  if self.Join and self.Join.Reset then self.Join.Reset() end
  mine.j, mine.je, mine.ps, S.posSent = nil, nil, nil, nil
  if T.SetTabLines then T:SetTabLines("group", {}, "Group") end
end

function M:OnOptionChanged() dirty.view, dirty.urgent = true, true end

-- tests
M._test = { gear = gearCache, auras = auraCache, dirty = dirty, members = members, body = function() return body end,
  pull = function() return pullText end, mine = mine, sent = function() return sentVal, sentEntries end,
  news = function() return newsSorted() end, nowKey = function()
    for k, e in pairs(news) do if e.posted then return k end end
    return S.pinNow and (pullText and "pull" or "cast") or nil
  end,
  reset = function()
    S.lastBuild, S.lastAura, S.lastUnitQ, S.lastScan, S.lastInspect, S.lastSend, S.lastFull = -1e9, -1e9, -1e9, -1e9, -1e9, -1e9, -1e9
    S.pendingInspect, S.tokens = nil, BUCKET
    for k in pairs(dirty) do dirty[k] = true end
  end }
