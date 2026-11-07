-- TreeAI — module Squad (agent SQUAD) : le « cerveau de groupe » des 4 amis qui ont tous TreeAI.
-- Chaque client partage un état compact (protocole versionné, C_ChatInfo.SendAddonMessage via
-- TreeAI:Send, canal PARTY/RAID uniquement), puis TOUS les clients calculent les MÊMES conseils
-- (moteur déterministe : mêmes entrées -> mêmes conseils ; égalités départagées par le nom le plus
-- petit = « chef » de tri). Rien n'est automatisé : des conseils écrits dans le panneau, un bouton
-- « Partager » (clic du joueur, hors combat). PvE seulement.
--
-- Protocole v2 (types de comm du cœur, découpage/anti-flood par le cœur) :
--   SQS  table d'état  { v = 2, c, l, x, r, z, m, py, px, pi, h, p, d, pt, ph, b, bt, du, hs, hb,
--                        fo, wa, am, pr, lm, rd, af, cb, qn, qm, xh }
--   SQQ  chaîne "2:" .. quêtes "id,niv,drapeaux,titre,objectifs;..." (drapeaux C fini, G groupe,
--        Dn donjon n ; objectifs "m3/8.i0/5" type m/i/o/e/r/x)
--   SQR  "2" : « renvoyez-moi vos infos »
-- Débit : à chaque changement + toutes les 15 s (état) / 60 s (quêtes), ≤ 1 message/s émis,
-- seau de jetons en réception par membre, uniquement depuis les membres actuels du groupe,
-- versions inconnues ignorées, toutes les valeurs reçues revalidées (bornes, longueur, caractères).
-- Lua 5.1, aucune globale créée. Les globales du jeu sont lues sans « _G. » (testable par setfenv).
local _, ns = ...
local T = TreeAI
if type(T) ~= "table" or type(T.RegisterModule) ~= "function" then return end

local PROTO = 2
local STATE_EVERY = 15
local QUEST_EVERY = 60
local MAX_QUESTS = 25
local FAR_YARDS = 250
local STALE = 120
local HEARTHSTONE = 6948

local floor, mmax, mmin, sqrt, ceil = math.floor, math.max, math.min, math.sqrt, math.ceil
local tconcat, tsort = table.concat, table.sort
local fmt = string.format

local S = T:RegisterModule("Squad", {
  title = "Groupe (amis TreeAI)",
  defaults = {
    enabled = true, section = true, mirror = false,
    quests = true, xp = true, regroup = true, pull = true, loot = true, logistics = true, dungeons = true,
  },
  charDefaults = { leaveAt = 0, ready = true },
  options = {
    { key = "section",   type = "toggle", label = "Section « Groupe » dans le panneau" },
    { key = "quests",    type = "toggle", label = "Conseils quêtes (communes, partage, rendu)" },
    { key = "xp",        type = "toggle", label = "Équilibre XP (écarts de niveau, repos)" },
    { key = "regroup",   type = "toggle", label = "Regroupement (loin, autre zone, mort)" },
    { key = "pull",      type = "toggle", label = "Prêts à puller (mana, PV, familier, AFK)" },
    { key = "loot",      type = "toggle", label = "Butin (objets de quête, besoin/cupidité)" },
    { key = "logistics", type = "toggle", label = "Logistique (sacs, réparation, pierre, départ)" },
    { key = "dungeons",  type = "toggle", label = "Donjons (niveau et quêtes)" },
    { key = "mirror",    type = "toggle", label = "Copier les 3 conseils dans la liste principale" },
  },
})
if not S then return end
T.Squad = S
S.PROTO = PROTO

-- ------------------------------------------------------------------ utilitaires
local function pass(ok, ...) if ok then return ... end return nil end
local function call(fn, ...)
  if type(fn) ~= "function" then return nil end
  return pass(pcall(fn, ...))
end
local function now() return call(GetTime) or 0 end
local function epoch() return call(time) or 0 end

local function num(v, lo, hi, def)
  v = tonumber(v)
  if not v or v ~= v then return def end
  if v < lo then v = lo elseif v > hi then v = hi end
  return v
end
local function int(v, lo, hi, def)
  v = num(v, lo, hi, nil)
  if not v then return def end
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
-- Texte reçu : sans codes d'échappement (|), sans séparateurs du protocole, borné.
local function str(v, maxlen)
  if type(v) ~= "string" and type(v) ~= "number" then return nil end
  v = tostring(v):gsub("[%c|~;,]", " "):gsub("%s+", " "):gsub("^ ", ""):gsub(" $", "")
  if v == "" then return nil end
  return utf8cut(v, maxlen or 40)
end
S.Clean = str

local function shortName(full)
  if type(full) ~= "string" or full == "" then return nil end
  return (full:gsub("%-.*$", ""))
end

local CLASS_FR = {
  WARRIOR = "Guerrier", PALADIN = "Paladin", HUNTER = "Chasseur", ROGUE = "Voleur", PRIEST = "Prêtre",
  SHAMAN = "Chaman", MAGE = "Mage", WARLOCK = "Démoniste", DRUID = "Druide",
}
local MANA = { PALADIN = true, HUNTER = true, PRIEST = true, SHAMAN = true, MAGE = true, WARLOCK = true, DRUID = true }
local PET = { HUNTER = true, WARLOCK = true }
S.CLASS_FR = CLASS_FR

-- Donjons (noms FR + mots-clés FR/EN de l'en-tête du journal de quêtes, tranche de niveau Classic).
local DUNGEONS = {
  { name = "Gouffre de Ragefeu",          min = 13, max = 18, kw = { "ragefeu", "ragefire" } },
  { name = "Les Mortemines",              min = 17, max = 26, kw = { "mortemines", "deadmines" } },
  { name = "Cavernes des Lamentations",   min = 17, max = 24, kw = { "lamentations", "wailing" } },
  { name = "Donjon d'Ombrecroc",          min = 22, max = 30, kw = { "ombrecroc", "shadowfang" } },
  { name = "Profondeurs de Brassenoire",  min = 24, max = 32, kw = { "brassenoire", "blackfathom" } },
  { name = "La Prison",                   min = 24, max = 32, kw = { "prison", "stockade" } },
  { name = "Gnomeregan",                  min = 29, max = 38, kw = { "gnomeregan" } },
  { name = "Kraal de Tranchebauge",       min = 29, max = 38, kw = { "kraal", "razorfen kraul" } },
  { name = "Monastère Écarlate",          min = 30, max = 45, kw = { "monastère", "monastere", "scarlet" } },
  { name = "Souilles de Tranchebauge",    min = 37, max = 46, kw = { "souilles", "razorfen downs" } },
  { name = "Uldaman",                     min = 41, max = 51, kw = { "uldaman" } },
  { name = "Zul'Farrak",                  min = 44, max = 54, kw = { "zul'farrak", "zul farrak" } },
  { name = "Maraudon",                    min = 46, max = 55, kw = { "maraudon" } },
  { name = "Temple d'Atal'Hakkar",        min = 50, max = 60, kw = { "atal'hakkar", "sunken temple", "temple englouti" } },
  { name = "Profondeurs de Rochenoire",   min = 52, max = 60, kw = { "profondeurs de rochenoire", "blackrock depths" } },
  { name = "Pic Rochenoire",              min = 55, max = 60, kw = { "pic rochenoire", "blackrock spire" } },
  { name = "Hache-Tripes",                min = 55, max = 60, kw = { "hache-tripes", "dire maul" } },
  { name = "Scholomance",                 min = 58, max = 60, kw = { "scholomance" } },
  { name = "Stratholme",                  min = 58, max = 60, kw = { "stratholme" } },
}
S.DUNGEONS = DUNGEONS

function S.DungeonOf(header)
  if type(header) ~= "string" then return nil end
  local h = header:lower()
  for i, d in ipairs(DUNGEONS) do
    for _, k in ipairs(d.kw) do
      if h:find(k, 1, true) then return i end
    end
  end
  return nil
end

-- ------------------------------------------------------------------ encodage (pur)
local STATE_SPEC = {  -- clé = { min, max } entiers ; absents -> défaut
  l = { 1, 80, 1 }, x = { 0, 100, 0 }, r = { 0, 150, 0 }, m = { 0, 1000000, 0 },
  py = { -100000, 100000, nil }, px = { -100000, 100000, nil }, pi = { -1, 100000, -1 },
  h = { 0, 100, 100 }, p = { -1, 100, -1 }, d = { 0, 2, 0 }, pt = { -1, 2, -1 }, ph = { 0, 3, 0 },
  b = { 0, 200, 0 }, bt = { 0, 400, 0 }, du = { -1, 100, -1 }, hs = { -1, 7200, -1 },
  fo = { 0, 999, 0 }, wa = { 0, 999, 0 }, am = { -1, 99999, -1 }, lm = { -1, 600, -1 },
  rd = { 0, 1, 1 }, af = { 0, 1, 0 }, cb = { 0, 1, 0 }, qn = { 0, 50, 0 }, qm = { 0, 50, 25 },
  xh = { 0, 10000000, 0 },
}
local STATE_STR = { z = 40, hb = 30, pr = 60 }

-- Revalide un état reçu (ou local). nil si version inconnue / pas une table.
function S.DecodeState(p)
  if type(p) ~= "table" or tonumber(p.v) ~= PROTO then return nil end
  local st = { v = PROTO }
  st.c = (type(p.c) == "string" and CLASS_FR[p.c]) and p.c or "?"
  for k, spec in pairs(STATE_SPEC) do st[k] = int(p[k], spec[1], spec[2], spec[3]) end
  for k, n in pairs(STATE_STR) do st[k] = str(p[k], n) end
  return st
end

local OBJ_TYPE = { item = "i", monster = "m", object = "o", event = "e", reputation = "r" }

-- quests = liste { id, level, title, done, group, dungeon, obj = { {t, have, need} } } -> chaîne
function S.EncodeQuests(quests)
  local parts = {}
  for _, q in ipairs(quests or {}) do
    local id = int(q.id, 1, 10000000, nil)
    if id and #parts < MAX_QUESTS then
      local flags = (q.done and "C" or "") .. (q.group and "G" or "") .. (q.dungeon and ("D" .. int(q.dungeon, 1, 99, 1)) or "")
      local o = {}
      for j, ob in ipairs(q.obj or {}) do
        if j > 6 then break end
        local need = int(ob.need, 1, 999, 1)
        local have = int(ob.have, 0, 999, ob.finished and need or 0)
        local t = OBJ_TYPE[ob.t] or (type(ob.t) == "string" and #ob.t == 1 and ob.t:match("^[mioerx]$")) or "x"
        o[#o + 1] = t .. mmin(have, need) .. "/" .. need
      end
      parts[#parts + 1] = fmt("%d,%d,%s,%s,%s", id, int(q.level, 0, 80, 0), flags, str(q.title, 30) or "", tconcat(o, "."))
    end
  end
  return tconcat(parts, ";")
end

-- chaîne -> map [id] = { id, level, title, done, group, dungeon, obj = { {t, have, need} } }
function S.DecodeQuests(payload)
  local out, count = {}, 0
  if type(payload) ~= "string" or #payload > 6000 then return out end
  for entry in payload:gmatch("[^;]+") do
    local id, lvl, flags, title, objs = entry:match("^(%d+),(%d*),(%w*),([^,]*),(.*)$")
    id = int(id, 1, 10000000, nil)
    if id then
      count = count + 1
      if count > MAX_QUESTS then break end
      local q = { id = id, level = int(lvl, 0, 80, 0), title = str(title, 30), obj = {} }
      q.done = flags:find("C", 1, true) and true or false
      q.group = flags:find("G", 1, true) and true or false
      q.dungeon = int(flags:match("D(%d+)"), 1, #DUNGEONS, nil)
      for t, h, n in objs:gmatch("([mioerx])(%d+)/(%d+)") do
        if #q.obj >= 6 then break end
        local need = int(n, 1, 999, 1)
        q.obj[#q.obj + 1] = { t = t, have = int(h, 0, need, 0), need = need }
      end
      out[id] = q
    end
  end
  return out
end

-- ------------------------------------------------------------------ moteur de conseils (pur)
local E = {}
S.Engine = E

local function joinNames(list, maxn)
  local t = {}
  for i, v in ipairs(list) do
    if i > (maxn or 3) then t[#t + 1] = "…" break end
    t[#t + 1] = v
  end
  return tconcat(t, ", ")
end

local function progress(q)
  local h, n = 0, 0
  for _, o in ipairs(q and q.obj or {}) do h = h + mmin(o.have or 0, o.need or 0); n = n + (o.need or 0) end
  if n == 0 then n = 1; h = (q and q.done) and 1 or 0 end
  if q and q.done then h = n end
  return h, n
end
E.Progress = progress

local function sortedMembers(members)
  local list = {}
  for _, m in ipairs(members or {}) do if type(m) == "table" and m.name and m.st then list[#list + 1] = m end end
  tsort(list, function(a, b) return a.name < b.name end)
  return list
end
E.Sorted = sortedMembers

function E.Leader(members)
  local l = sortedMembers(members)[1]
  return l and l.name or nil
end

-- Index des quêtes de tout le groupe : liste triée (porteurs desc, id asc).
function E.QuestIndex(members)
  local list = sortedMembers(members)
  local byId, res = {}, {}
  for _, m in ipairs(list) do
    local ids = {}
    for id in pairs(m.quests or {}) do ids[#ids + 1] = id end
    tsort(ids)
    for _, id in ipairs(ids) do
      local q = m.quests[id]
      local e = byId[id]
      if not e then
        e = { id = id, holders = {}, done = {}, notDone = {}, missing = {}, level = q.level or 0 }
        byId[id] = e
        res[#res + 1] = e
      end
      e.title = e.title or q.title
      e.group = e.group or q.group
      e.dungeon = e.dungeon or q.dungeon
      e.holders[#e.holders + 1] = m.name
      if q.done then e.done[#e.done + 1] = m.name else e.notDone[#e.notDone + 1] = m.name end
    end
  end
  for _, e in ipairs(res) do
    e.title = e.title or ("quête #" .. e.id)
    local has = {}
    for _, n in ipairs(e.holders) do has[n] = true end
    for _, m in ipairs(list) do if not has[m.name] then e.missing[#e.missing + 1] = m.name end end
  end
  tsort(res, function(a, b)
    if #a.holders ~= #b.holders then return #a.holders > #b.holders end
    return a.id < b.id
  end)
  return res
end

local CAPS = { quests = 4, xp = 2, regroup = 3, pull = 3, loot = 2, logistics = 3, dungeons = 1 }

-- members = { { name, st = état décodé, quests = map, leaveMin = minutes|nil }, ... }
-- ctx = { pulling = bool, opts = { quests = bool, ... }, all = bool (sans plafond par catégorie) }
--   -> liste triée d'avis
-- { id, cat, prio, score, text, action = { kind = "share", quest, by } }
function E.Advise(members, ctx)
  ctx = ctx or {}
  local opts = ctx.opts or {}
  local function on(k) return opts[k] ~= false end
  local list = sortedMembers(members)
  local N = #list
  local out = {}
  if N < 2 then return out end
  local byName = {}
  for _, m in ipairs(list) do byName[m.name] = m end
  local function add(id, cat, prio, score, text, action)
    out[#out + 1] = { id = id, cat = cat, prio = prio, score = score, text = text, action = action }
  end
  local idx = E.QuestIndex(list)

  -- ---------------- quêtes
  if on("quests") then
    local commonDone = false
    for _, e in ipairs(idx) do
      if #e.holders == N and not commonDone then
        commonDone = true
        if #e.done == N then
          add("q_all_" .. e.id, "quests", 2, 70, fmt("« %s » : tous ont fini, allez rendre", e.title))
        else
          add("q_common_" .. e.id, "quests", 1, 60, fmt("Tous les %d : « %s » (%d/%d ont fini)", N, e.title, #e.done, N))
        end
      end
    end
    -- partage : quête portée par au moins la moitié du groupe
    for _, e in ipairs(idx) do
      if #e.holders * 2 >= N and #e.missing > 0 then
        local by = e.holders[1]
        for _, name in ipairs(e.missing) do
          local m = byName[name]
          local full = m.st.qm > 0 and m.st.qn >= m.st.qm
          local tooLow = e.level > 0 and m.st.l < e.level - 4
          if not full and not tooLow then
            add("q_share_" .. e.id .. "_" .. name, "quests", 1, 50 + #e.holders * 5,
              fmt("%s n'a pas « %s » : %s, partage-la", name, e.title, by), { kind = "share", quest = e.id, by = by })
          end
        end
      end
    end
    -- synchro du rendu
    local best
    for _, e in ipairs(idx) do
      if #e.done > 0 and #e.notDone > 0 and (not best or #e.done > #best.done) then best = e end
    end
    if best then
      local lag, lh, ln, lr
      for _, name in ipairs(best.notDone) do
        local h, n = progress(byName[name].quests[best.id])
        if not lr or h / n < lr then lag, lh, ln, lr = name, h, n, h / n end
      end
      add("q_wait_" .. best.id, "quests", #best.done >= #best.notDone and 2 or 1, 55 + #best.done * 5,
        fmt("Attendez %s (%d/%d) avant de rendre « %s »", lag, lh, ln, best.title))
    end
    -- quêtes de groupe / élite
    if N >= 3 then
      for _, e in ipairs(idx) do
        if e.group and #e.notDone > 0 and not e.dungeon then
          add("q_group_" .. e.id, "quests", 1, 40 + #e.holders,
            fmt("« %s » (groupe) : faisable à %d, faites-la ensemble", e.title, N))
          break
        end
      end
    end
    -- ordre conseillé
    local order = {}
    for _, e in ipairs(idx) do
      if #e.holders >= 2 and #e.notDone > 0 and #order < 3 then order[#order + 1] = fmt("%s (%d)", e.title, #e.holders) end
    end
    if #order >= 2 then add("q_order", "quests", 1, 35, "Ordre conseillé : " .. tconcat(order, ", ")) end
    -- à faire à part
    local solo = {}
    for _, e in ipairs(idx) do
      if #e.holders == 1 and not e.done[1] and not e.group and #solo < 2 then
        solo[#solo + 1] = fmt("%s — « %s »", e.holders[1], e.title)
      end
    end
    if #solo > 0 then add("q_solo", "quests", 1, 20, "À faire à part : " .. tconcat(solo, ", ")) end
  end

  -- ---------------- XP
  if on("xp") then
    local maxL = 0
    for _, m in ipairs(list) do maxL = mmax(maxL, m.st.l) end
    local laggers = {}
    for _, m in ipairs(list) do if maxL - m.st.l >= 2 then laggers[#laggers + 1] = m end end
    tsort(laggers, function(a, b) if a.st.l ~= b.st.l then return a.st.l < b.st.l end return a.name < b.name end)
    for _, m in ipairs(laggers) do
      local gap = maxL - m.st.l
      add("x_gap_" .. m.name, "xp", gap >= 5 and 2 or 1, 30 + gap * 3,
        fmt("%s est %d niveaux derrière%s", m.name, gap,
          gap >= 5 and " : l'XP de groupe en souffre" or (m.st.r >= 20 and " (en repos, il rattrape)" or "")))
    end
    local rested = {}
    for _, m in ipairs(list) do if m.st.r >= 10 then rested[#rested + 1] = m end end
    tsort(rested, function(a, b) if a.st.r ~= b.st.r then return a.st.r > b.st.r end return a.name < b.name end)
    if #rested > 0 then
      local t = {}
      for i, m in ipairs(rested) do if i <= 3 then t[#t + 1] = fmt("%s %d %%", m.name, m.st.r) end end
      add("x_rested", "xp", 1, 15, "En repos (XP x2) : " .. tconcat(t, ", "))
    end
    local xs, xn = 0, 0
    for _, m in ipairs(list) do if m.st.xh > 0 then xs = xs + m.st.xh; xn = xn + 1 end end
    if xn > 0 then
      add("x_rate", "xp", 1, 10, fmt("XP/h du groupe : ~%.1fk par joueur", xs / xn / 1000))
    end
  end

  -- ---------------- regroupement
  local zoneOf = {}
  if on("regroup") then
    for _, m in ipairs(list) do
      if m.st.d == 2 then
        add("r_ghost_" .. m.name, "regroup", 2, 90, fmt("%s est en fantôme : attendez-le (course au cadavre)", m.name))
      elseif m.st.d == 1 then
        add("r_dead_" .. m.name, "regroup", 2, 88, fmt("%s est mort : rez ou libérez l'esprit", m.name))
      end
    end
    local counts, zones = {}, {}
    for _, m in ipairs(list) do
      if m.st.z then
        if not counts[m.st.z] then counts[m.st.z] = 0; zones[#zones + 1] = m.st.z end
        counts[m.st.z] = counts[m.st.z] + 1
      end
    end
    tsort(zones, function(a, b) if counts[a] ~= counts[b] then return counts[a] > counts[b] end return a < b end)
    local main = zones[1]
    zoneOf.main = main
    if main and counts[main] >= 2 then
      for _, m in ipairs(list) do
        if m.st.z and m.st.z ~= main and m.st.d ~= 2 then
          add("r_zone_" .. m.name, "regroup", 2, 80, fmt("Regroupez-vous : %s est à %s", m.name, m.st.z))
        end
      end
      -- loin du centre des autres (même zone, même instance, positions connues)
      local pts = {}
      for _, m in ipairs(list) do
        if m.st.z == main and m.st.py and m.st.px and m.st.d ~= 2 then pts[#pts + 1] = m end
      end
      if #pts >= 2 then
        local insts = {}
        for _, m in ipairs(pts) do insts[m.st.pi] = (insts[m.st.pi] or 0) + 1 end
        for _, m in ipairs(pts) do
          local cy, cx, n = 0, 0, 0
          for _, o in ipairs(pts) do
            if o ~= m and o.st.pi == m.st.pi then cy = cy + o.st.py; cx = cx + o.st.px; n = n + 1 end
          end
          if n > 0 then
            local dy, dx = m.st.py - cy / n, m.st.px - cx / n
            local d = sqrt(dy * dy + dx * dx)
            if d > FAR_YARDS then
              add("r_far_" .. m.name, "regroup", 1, 70,
                fmt("%s est loin (~%d m) : regroupez-vous", m.name, floor(d * 0.9144 / 10 + 0.5) * 10))
            end
          end
        end
      end
    end
  end

  -- ---------------- prêts à puller
  if on("pull") then
    local anyCombat = false
    for _, m in ipairs(list) do if m.st.cb == 1 then anyCombat = true end end
    if not anyCombat then
      local pulling = ctx.pulling and true or false
      local manaCut, hpCut = pulling and 35 or 25, pulling and 50 or 35
      local low = {}
      for _, m in ipairs(list) do
        if m.st.d == 0 then
          if m.st.p >= 0 and m.st.p < manaCut and MANA[m.st.c] then low[#low + 1] = fmt("%s %d %% mana", m.name, m.st.p)
          elseif m.st.h < hpCut then low[#low + 1] = fmt("%s %d %% PV", m.name, m.st.h) end
        end
      end
      if #low > 0 then add("p_drink", "pull", pulling and 2 or 1, pulling and 85 or 45, "Pause boisson : " .. joinNames(low)) end
      for _, m in ipairs(list) do
        if PET[m.st.c] and m.st.pt == 2 and m.st.d == 0 then
          add("p_pet_" .. m.name, "pull", pulling and 2 or 1, pulling and 84 or 40, fmt("%s : familier absent ou mort", m.name))
        end
      end
      local wait = {}
      for _, m in ipairs(list) do
        if m.st.af == 1 then wait[#wait + 1] = m.name .. " est AFK"
        elseif m.st.rd == 0 then wait[#wait + 1] = m.name .. " n'est pas prêt" end
      end
      if #wait > 0 then add("p_wait", "pull", pulling and 2 or 1, pulling and 86 or 30, "Attendez : " .. joinNames(wait)) end
    end
  end

  -- ---------------- butin (objets de quête)
  if on("loot") then
    for _, e in ipairs(idx) do
      if #e.holders >= 2 and #e.notDone > 0 then
        for j = 1, 6 do
          local needy, nh, nn, nr, best = nil, 0, 0, nil, nil
          for _, name in ipairs(e.holders) do
            local o = byName[name].quests[e.id].obj[j]
            if o and o.t == "i" then
              local r = o.have / o.need
              if not best or r > best then best = r end
              if r < 1 and (not nr or r < nr) then needy, nh, nn, nr = name, o.have, o.need, r end
            end
          end
          if needy and best > nr then
            add("l_item_" .. e.id .. "_" .. j, "loot", 1, 50, fmt("Laissez %s looter : « %s » (%d/%d)", needy, e.title, nh, nn))
            break
          end
        end
      end
    end
  end

  -- ---------------- logistique
  if on("logistics") then
    local bags, repair, ammo, food = {}, {}, {}, {}
    for _, m in ipairs(list) do
      if m.st.bt > 0 and m.st.b <= 2 then bags[#bags + 1] = m.name end
      if m.st.du >= 0 and m.st.du < 30 then repair[#repair + 1] = fmt("%s %d %%", m.name, m.st.du) end
      if m.st.c == "HUNTER" and m.st.am >= 0 and m.st.am < 200 then ammo[#ammo + 1] = m.name end
      if m.st.l >= 5 and (m.st.fo == 0 or (MANA[m.st.c] and m.st.c ~= "HUNTER" and m.st.wa == 0)) then food[#food + 1] = m.name end
    end
    local affected = {}
    for _, l in ipairs({ bags, ammo, food }) do for _, n in ipairs(l) do affected[n] = true end end
    for _, m in ipairs(list) do if m.st.du >= 0 and m.st.du < 30 then affected[m.name] = true end end
    local na = 0
    for _ in pairs(affected) do na = na + 1 end
    if na > 0 then
      local text
      if #repair == 0 and #ammo == 0 and #food == 0 then
        text = fmt("Passage marchand : %d joueur%s %s les sacs pleins", #bags, #bags > 1 and "s" or "", #bags > 1 and "ont" or "a")
      else
        local parts = {}
        if #bags > 0 then parts[#parts + 1] = fmt("sacs pleins (%d)", #bags) end
        if #repair > 0 then parts[#parts + 1] = "réparation (" .. joinNames(repair, 2) .. ")" end
        if #ammo > 0 then parts[#parts + 1] = "munitions (" .. joinNames(ammo, 2) .. ")" end
        if #food > 0 then parts[#parts + 1] = "nourriture/eau (" .. joinNames(food, 2) .. ")" end
        text = "Passage marchand : " .. tconcat(parts, ", ")
      end
      local critical = false
      for _, m in ipairs(list) do if m.st.du >= 0 and m.st.du < 15 then critical = true end end
      add("g_vendor", "logistics", (na * 2 >= N or critical) and 2 or 1, 60, text)
    end
    -- départs
    for _, m in ipairs(list) do
      local lm = m.leaveMin
      if lm and lm >= 0 and lm <= 30 then
        local best, br, bh
        local ids = {}
        for id in pairs(m.quests or {}) do ids[#ids + 1] = id end
        tsort(ids)
        local holders = {}
        for _, e in ipairs(idx) do holders[e.id] = #e.holders end
        for _, id in ipairs(ids) do
          local q = m.quests[id]
          if not q.done then
            local h, n = progress(q)
            local r = h / n
            if not best or r > br or (r == br and (holders[id] or 0) > bh) then best, br, bh = q, r, holders[id] or 0 end
          end
        end
        local text
        if lm <= 5 then
          text = fmt("%s part dans %d min : rendez les quêtes maintenant", m.name, lm)
        elseif best then
          text = fmt("%s part dans %d min : finissez « %s »", m.name, lm, best.title or ("quête #" .. best.id))
        else
          text = fmt("%s part dans %d min", m.name, lm)
        end
        add("g_leave_" .. m.name, "logistics", lm <= 10 and 2 or 1, 75, text)
      end
    end
    -- pierres de foyer
    local holders, ready, binds, bindList = 0, 0, {}, {}
    for _, m in ipairs(list) do
      if m.st.hs >= 0 then
        holders = holders + 1
        if m.st.hs == 0 then ready = ready + 1 end
        if m.st.hb then
          if not binds[m.st.hb] then binds[m.st.hb] = 0; bindList[#bindList + 1] = m.st.hb end
          binds[m.st.hb] = binds[m.st.hb] + 1
        end
      end
    end
    if ready > 0 then
      tsort(bindList, function(a, b) if binds[a] ~= binds[b] then return binds[a] > binds[b] end return a < b end)
      local text = fmt("%d/%d ont la pierre prête", ready, N)
      if bindList[1] then text = text .. ", bind à " .. bindList[1] .. (#bindList > 1 and " (binds différents)" or "") end
      add("g_hearth", "logistics", 1, 12, text)
    end
  end

  -- ---------------- donjons
  if on("dungeons") then
    local sum, minL = 0, 99
    for _, m in ipairs(list) do sum = sum + m.st.l; minL = mmin(minL, m.st.l) end
    local avg = sum / N
    for di, d in ipairs(DUNGEONS) do
      local have = {}
      for _, m in ipairs(list) do
        for _, q in pairs(m.quests or {}) do
          if q.dungeon == di then have[#have + 1] = m.name break end
        end
      end
      if #have > 0 and avg >= d.min - 1 and avg <= d.max then
        local low = {}
        for _, m in ipairs(list) do if m.st.l < d.min - 2 then low[#low + 1] = m.name end end
        add("d_" .. di, "dungeons", 1, 25 + #have,
          fmt("Prêts pour %s : %d/%d ont les quêtes%s", d.name, #have, N,
            #low > 0 and (" (" .. joinNames(low) .. " trop bas)") or ""))
      end
    end
  end

  -- tri + plafonds par catégorie
  tsort(out, function(a, b)
    if a.prio ~= b.prio then return a.prio > b.prio end
    if a.score ~= b.score then return a.score > b.score end
    return a.id < b.id
  end)
  local res, per = {}, {}
  for _, a in ipairs(out) do
    per[a.cat] = (per[a.cat] or 0) + 1
    if ctx.all or per[a.cat] <= (CAPS[a.cat] or 3) then res[#res + 1] = a end
  end
  return res
end

-- ------------------------------------------------------------------ conseil de jet (pur, texte seulement)
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

-- plus haut type d'armure portable (1 tissu .. 4 plaques), Classic : maille/plaques au niveau 40
local function bestArmor(class, level)
  if class == "WARRIOR" or class == "PALADIN" then return level >= 40 and 4 or 3 end
  if class == "HUNTER" or class == "SHAMAN" then return level >= 40 and 3 or 2 end
  if class == "ROGUE" or class == "DRUID" then return 2 end
  return 1
end

-- item = { name, classID, subClassID, equipLoc } ; members comme E.Advise -> texte ou nil
function E.LootHint(item, members)
  if type(item) ~= "table" then return nil end
  local list = sortedMembers(members)
  if #list < 2 then return nil end
  local cid, sid = tonumber(item.classID), tonumber(item.subClassID)
  local name = str(item.name, 40) or "objet"
  local need = {}
  if cid == 2 and sid and WEAPON[sid] then
    local pref = RANGED_FIRST[sid]
    for _, m in ipairs(list) do if WEAPON[sid][m.st.c] and (not pref or m.st.c == pref) then need[#need + 1] = m.name end end
    if pref and #need == 0 then
      for _, m in ipairs(list) do if WEAPON[sid][m.st.c] then need[#need + 1] = m.name end end
    end
  elseif cid == 4 and sid then
    if item.equipLoc == "INVTYPE_CLOAK" or sid == 0 then
      return fmt("Jet « %s » : utile à tous, Besoin seulement si c'est une amélioration", name)
    elseif ARMOR_NAME[sid] then
      for _, m in ipairs(list) do if bestArmor(m.st.c, m.st.l) == sid then need[#need + 1] = m.name end end
      if #need == 0 then
        for _, m in ipairs(list) do if bestArmor(m.st.c, m.st.l) >= sid then need[#need + 1] = m.name end end
      end
    elseif sid == 6 then
      for _, m in ipairs(list) do if m.st.c == "WARRIOR" or m.st.c == "PALADIN" or m.st.c == "SHAMAN" then need[#need + 1] = m.name end end
    else
      return nil
    end
  else
    return nil
  end
  if #need == 0 then return fmt("Jet « %s » : personne ne l'utilise, Cupidité pour tous", name) end
  local kind = (cid == 4 and ARMOR_NAME[sid]) and (" (" .. ARMOR_NAME[sid] .. ")") or ""
  return fmt("Jet « %s » : Besoin %s%s ; les autres Cupidité", name, joinNames(need), kind)
end

-- ------------------------------------------------------------------ collecte locale (API gardées)
local API = T.API or {}

local function scanProfessions()
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
  return str(tconcat(out, " "), 60)
end

local function countItems(list)
  local n = 0
  if type(list) ~= "table" or not API.ItemCount then return 0 end
  for _, it in ipairs(list) do
    local id = type(it) == "table" and it[1] or it
    if tonumber(id) then n = n + (call(API.ItemCount, id) or 0) end
  end
  return n
end

local function durability()
  if type(GetInventoryItemDurability) ~= "function" then return -1 end
  local worst
  for slot = 1, 18 do
    local cur, mx = call(GetInventoryItemDurability, slot)
    if cur and mx and mx > 0 then
      local r = cur / mx
      if not worst or r < worst then worst = r end
    end
  end
  return worst and floor(worst * 100 + 0.5) or -1
end

local function hearth()
  local have = API.ItemCount and (call(API.ItemCount, HEARTHSTONE) or 0) or 0
  if have <= 0 then return -1, nil end
  local start, dur
  if C_Container and C_Container.GetItemCooldown then start, dur = call(C_Container.GetItemCooldown, HEARTHSTONE) end
  if not start and type(GetItemCooldown) == "function" then start, dur = call(GetItemCooldown, HEARTHSTONE) end
  local left = 0
  if start and dur and start > 0 and dur > 0 then left = mmax(0, ceil(start + dur - now())) end
  return left, str(call(GetBindLocation), 30)
end

local function maxQuests()
  if C_QuestLog and C_QuestLog.GetMaxNumQuestsCanAccept then
    local n = call(C_QuestLog.GetMaxNumQuestsCanAccept)
    if tonumber(n) then return n end
  end
  return 20
end

local function parseCount(text)
  if type(text) ~= "string" then return nil end
  local h, n = text:match("(%d+)%s*/%s*(%d+)")
  return tonumber(h), tonumber(n)
end

-- Journal de quêtes -> liste { id, level, title, done, group, dungeon, idx, obj }
function S:ScanQuests()
  local out, header = {}, nil
  if C_QuestLog and C_QuestLog.GetNumQuestLogEntries and C_QuestLog.GetInfo then
    for i = 1, mmin(call(C_QuestLog.GetNumQuestLogEntries) or 0, 80) do
      local info = call(C_QuestLog.GetInfo, i)
      if type(info) == "table" then
        if info.isHeader then header = info.title
        elseif info.questID and not info.isHidden then
          local id = info.questID
          local q = { id = id, title = info.title, level = info.level, idx = i, obj = {} }
          q.done = call(C_QuestLog.IsComplete, id) and true or false
          q.group = (tonumber(info.suggestedGroup) or 0) > 1
          local tag = C_QuestLog.GetQuestTagInfo and call(C_QuestLog.GetQuestTagInfo, id)
          if type(tag) == "table" and (tag.isElite or tag.tagID == 1) then q.group = true end
          q.dungeon = S.DungeonOf(header)
          for _, o in ipairs(C_QuestLog.GetQuestObjectives and call(C_QuestLog.GetQuestObjectives, id) or {}) do
            q.obj[#q.obj + 1] = { t = o.type, have = o.numFulfilled, need = o.numRequired, finished = o.finished }
          end
          out[#out + 1] = q
        end
      end
    end
  elseif type(GetNumQuestLogEntries) == "function" and type(GetQuestLogTitle) == "function" then
    for i = 1, mmin(call(GetNumQuestLogEntries) or 0, 80) do
      local title, lvl, sugg, isHeader, _, isComplete, _, qid = call(GetQuestLogTitle, i)
      if title and isHeader then header = title
      elseif title and qid then
        local q = { id = qid, title = title, level = lvl, idx = i, obj = {} }
        q.done = (isComplete == 1 or isComplete == true)
        q.group = (tonumber(sugg) or 0) > 1
        q.dungeon = S.DungeonOf(header)
        for j = 1, mmin(call(GetNumQuestLeaderBoards, i) or 0, 6) do
          local text, kind, finished = call(GetQuestLogLeaderBoard, j, i)
          local h, n = parseCount(text)
          q.obj[#q.obj + 1] = { t = kind, have = h or (finished and 1 or 0), need = n or 1, finished = finished }
        end
        out[#out + 1] = q
      end
    end
  end
  return out
end

-- XP/h locale : Boost si présent, sinon gain depuis le début de la session.
local xpSession = { t0 = nil, gained = 0, last = nil, lastMax = nil }
local function xpPerHour()
  local B = T.Boost or (T.GetModule and T:GetModule("Boost"))
  if type(B) == "table" then
    for _, f in ipairs({ "XPPerHour", "XpPerHour", "GetXPPerHour" }) do
      if type(B[f]) == "function" then
        local v = tonumber((call(B[f], B)))
        if v then return v end
      end
    end
  end
  if not xpSession.t0 then return 0 end
  local el = now() - xpSession.t0
  if el < 300 then return 0 end
  return xpSession.gained * 3600 / el
end

local function trackXP()
  local xp, mx = call(UnitXP, "player"), call(UnitXPMax, "player")
  if not xp or not mx then return end
  if not xpSession.t0 then xpSession.t0 = now() end
  if xpSession.last then
    if xp >= xpSession.last then xpSession.gained = xpSession.gained + (xp - xpSession.last)
    elseif xpSession.lastMax then xpSession.gained = xpSession.gained + (xpSession.lastMax - xpSession.last) + xp end
  end
  xpSession.last, xpSession.lastMax = xp, mx
end

local function leaveMinutes(char)
  local at = char and tonumber(char.leaveAt) or 0
  if at <= 0 then return -1 end
  local left = at - epoch()
  if left <= 0 then return 0 end
  return ceil(left / 60)
end

function S:CollectState()
  local st = { v = PROTO }
  st.c = select(2, call(UnitClass, "player")) or "?"
  st.l = call(UnitLevel, "player") or 1
  local xp, mx = call(UnitXP, "player"), call(UnitXPMax, "player")
  if xp and mx and mx > 0 then
    st.x = floor(xp * 100 / mx)
    local rest = call(GetXPExhaustion)
    st.r = rest and floor(rest * 100 / mx) or 0
  end
  st.z = call(GetRealZoneText)
  st.m = (C_Map and C_Map.GetBestMapForUnit and call(C_Map.GetBestMapForUnit, "player")) or 0
  local y, x, _, inst = call(UnitPosition, "player")
  if tonumber(y) and tonumber(x) then st.py, st.px, st.pi = floor(y / 5 + 0.5) * 5, floor(x / 5 + 0.5) * 5, inst or -1 end
  local hp, hpm = call(UnitHealth, "player"), call(UnitHealthMax, "player")
  if hp and hpm and hpm > 0 then st.h = floor(hp * 100 / hpm) end
  local ptype = call(UnitPowerType, "player")
  if ptype == 0 then
    local p, pm = call(UnitPower, "player", 0), call(UnitPowerMax, "player", 0)
    st.p = (p and pm and pm > 0) and floor(p * 100 / pm) or -1
  else
    st.p = -1
  end
  st.d = call(UnitIsGhost, "player") and 2 or (call(UnitIsDeadOrGhost, "player") and 1 or 0)
  if PET[st.c] then
    st.pt = (call(UnitExists, "pet") and not call(UnitIsDead, "pet")) and 1 or 2
    st.ph = tonumber((call(GetPetHappiness))) or 0
  end
  local free, total = call(API.FreeSlots)
  st.b, st.bt = free or 0, total or 0
  st.du = durability()
  st.hs, st.hb = hearth()
  local items = T.Data and T.Data.Macros and T.Data.Macros.items or {}
  st.fo, st.wa = countItems(items.food), countItems(items.water)
  if st.c == "HUNTER" then st.am = tonumber((call(GetInventoryItemCount, "player", 0))) or -1 end
  st.pr = scanProfessions()
  st.lm = leaveMinutes(self.char)
  st.rd = (self.char and self.char.ready == false) and 0 or 1
  st.af = call(UnitIsAFK, "player") and 1 or 0
  st.cb = (T.InCombat and T:InCombat()) and 1 or 0
  st.qn = self._questCount or 0
  st.qm = maxQuests()
  st.xh = floor(xpPerHour())
  return st
end

local function signature(st)
  return fmt("%s|%d|%s|%d|%d|%d|%d|%d|%d|%d|%d|%d|%d|%d|%d", st.c, st.l, tostring(st.z), st.d or 0, st.pt or -1,
    floor((st.h or 100) / 10), floor(((st.p or -1) + 1) / 10), st.b or 0, st.rd or 1, st.af or 0, st.lm or -1,
    st.cb or 0, floor((st.du or -1) / 10), st.hs == 0 and 1 or 0, st.x or 0)
end

-- ------------------------------------------------------------------ membres / réception
local members = {}     -- [nom court] = { name, st, quests, seen, leaveAt }
local roster = {}      -- [nom court] = unité
local buckets = {}
S._members = members

local function myName() return (call(UnitName, "player")) or "?" end

function S:RefreshRoster()
  for k in pairs(roster) do roster[k] = nil end
  local raid = call(IsInRaid)
  local prefix, n = raid and "raid" or "party", raid and 40 or 4
  for i = 1, n do
    local u = prefix .. i
    if call(UnitExists, u) then
      local nm = call(UnitName, u)
      if nm then roster[shortName(nm)] = u end
    end
  end
  for name in pairs(members) do
    if not roster[name] then members[name] = nil end
  end
end

function S:IsMember(sender)
  local name = shortName(sender)
  if not name or name == myName() then return false, name end
  if not roster[name] then self:RefreshRoster() end
  return roster[name] ~= nil, name
end

local function allow(name)
  local t = now()
  local b = buckets[name]
  if not b then b = { tok = 8, t = t }; buckets[name] = b end
  b.tok = mmin(8, b.tok + (t - b.t))
  b.t = t
  if b.tok < 1 then return false end
  b.tok = b.tok - 1
  return true
end

-- Compat : alimente Group.members (forme de l'agent F) lu par Quests.lua.
local function mirrorGroup(name, m)
  local G = (ns and ns.Group) or (T.GetModule and T:GetModule("Group"))
  if type(G) ~= "table" or type(G.members) ~= "table" then return end
  local g = G.members[name] or { quests = {} }
  local st = m.st
  if st then
    g.level, g.class, g.mapID, g.zone = st.l, st.c, st.m, st.z or ""
    g.y, g.x, g.inst = st.py, st.px, st.pi
  end
  if m.quests then
    local q = {}
    for id, e in pairs(m.quests) do
      local obj = {}
      for _, o in ipairs(e.obj) do obj[#obj + 1] = { have = o.have, need = o.need } end
      q[id] = { done = e.done or nil, obj = obj }
    end
    g.quests = q
  end
  g.seen = now()
  G.members[name] = g
end

local pending = { state = false, quests = false, request = false }

function S:OnComm(msgType, payload, sender)
  local ok, name = self:IsMember(sender)
  if not ok or not allow(name) then return end
  local m = members[name] or { name = name, quests = {} }
  if msgType == "SQS" then
    local st = S.DecodeState(payload)
    if not st then return end
    m.st = st
    m.leaveAt = st.lm >= 0 and (now() + st.lm * 60) or nil
  elseif msgType == "SQQ" then
    if type(payload) ~= "string" then return end
    local ver, body = payload:match("^(%d+):(.*)$")
    if tonumber(ver) ~= PROTO then return end
    m.quests = S.DecodeQuests(body)
  elseif msgType == "SQR" then
    if tonumber(payload) ~= PROTO then return end
    pending.state, pending.quests = true, true
    return
  else
    return
  end
  m.seen = now()
  members[name] = m
  mirrorGroup(name, m)
  self._dirty = true
end

-- ------------------------------------------------------------------ envoi (≤ 1 message/s)
local nextSendAt, lastStateSent, lastQuestSent, lastSig = 0, -1e9, -1e9, nil
local myQuests, myQuestEnc, questsDirty, lastScan = {}, "", true, -1e9

local function chunks(payload)
  local s = type(payload) == "table" and (T.Serialize and T:Serialize(payload) or "") or tostring(payload or "")
  return mmax(1, ceil((#s + 1) / 220))
end

function S:Pump(t)
  if t < nextSendAt or not (T.Send and T.DefaultChannel and T:DefaultChannel()) then return end
  local kind, payload
  if pending.request then kind, payload = "SQR", tostring(PROTO); pending.request = false
  elseif pending.state then kind, payload = "SQS", self._state; pending.state = false; lastStateSent = t
  elseif pending.quests then kind, payload = "SQQ", PROTO .. ":" .. myQuestEnc; pending.quests = false; lastQuestSent = t
  end
  if not kind or payload == nil then return end
  T:Send(kind, payload)
  nextSendAt = t + chunks(payload)
end

function S:InGroup()
  return T.DefaultChannel and T:DefaultChannel() ~= nil or false
end

-- Liste des membres pour le moteur (moi compris, sous la même forme que les autres).
function S:MemberList()
  local t = now()
  local me = { name = myName(), st = S.DecodeState(self._state), quests = S.DecodeQuests(myQuestEnc) }
  me.leaveMin = me.st and me.st.lm >= 0 and me.st.lm or nil
  local list = { me }
  for name, m in pairs(members) do
    if m.st and t - (m.seen or 0) <= STALE * 5 then
      local lm = m.leaveAt and mmax(0, ceil((m.leaveAt - t) / 60)) or nil
      list[#list + 1] = { name = name, st = m.st, quests = m.quests or {}, leaveMin = lm, stale = t - (m.seen or 0) > STALE }
    end
  end
  tsort(list, function(a, b) return a.name < b.name end)
  return list
end

local function pulling()
  if T.InCombat and T:InCombat() then return false end
  return call(UnitExists, "target") and call(UnitCanAttack, "player", "target") and not call(UnitIsPlayer, "target")
    and not call(UnitIsDead, "target") and true or false
end

function S:Recompute()
  self._dirty = false
  if not self._state then return end
  local list = self:MemberList()
  self._list = list
  self.advices = E.Advise(list, { pulling = pulling(), opts = self.db })
  if self.db and self.db.mirror then
    for i = 1, 3 do
      local a = self.advices[i]
      if a then self:Suggest("top" .. i, a.text, a.prio, { ttl = 20, cooldown = 120 }) else self:Clear("top" .. i) end
    end
  end
  if T.Fire then T:Fire("SQUAD_UPDATE", self.advices) end
  self:RefreshSection()
end

function S:Tick()
  local t = now()
  if not self:InGroup() then
    if next(members) then for k in pairs(members) do members[k] = nil end end
    self.advices = {}
    self:RefreshSection()
    return
  end
  if questsDirty or t - lastScan > 10 then
    questsDirty, lastScan = false, t
    local ok, qs = pcall(self.ScanQuests, self)
    if ok and type(qs) == "table" then
      myQuests = qs
      self._questCount = #qs
      local enc = S.EncodeQuests(qs)
      if enc ~= myQuestEnc and t - lastQuestSent >= 5 then pending.quests = true end
      if enc ~= myQuestEnc then self._dirty = true end
      myQuestEnc = enc
    end
  end
  if t - lastQuestSent >= QUEST_EVERY then pending.quests = true end
  local st = self:CollectState()
  self._state = st
  local sig = signature(st)
  if sig ~= lastSig then lastSig = sig; pending.state = true; self._dirty = true end
  if t - lastStateSent >= STATE_EVERY then pending.state = true end
  self:Pump(t)
  if self._dirty or t - (self._lastAdvise or 0) >= 3 then
    self._lastAdvise = t
    self:Recompute()
  end
end

-- ------------------------------------------------------------------ API publique (Quests / Boost / IA)
function S:Members()
  local out = {}
  for _, m in ipairs(self._list or self:MemberList()) do out[#out + 1] = m.name end
  return out
end
function S:State(name)
  if name == nil or name == myName() then return S.DecodeState(self._state) end
  local m = members[name]
  return m and m.st or nil
end
function S:Quests(name)
  if name == nil or name == myName() then return S.DecodeQuests(myQuestEnc) end
  local m = members[name]
  return m and m.quests or {}
end
function S:Common() return E.QuestIndex(self._list or self:MemberList()) end
function S:Advices() return self.advices or {} end
function S:Leader() return E.Leader(self._list or self:MemberList()) end
function S:Size() return #(self._list or {}) end
function S:IsActive() return self._active and self:IsEnabled() and true or false end

-- Partage d'une quête : uniquement sur clic du joueur, jamais en combat.
function S:ShareQuest(id)
  if T:InCombat() then T:Print("Partage : hors combat seulement."); return false end
  local idx
  for _, q in ipairs(myQuests) do if q.id == id then idx = q.idx end end
  if not idx then T:Print("Quête introuvable dans ton journal."); return false end
  if C_QuestLog and C_QuestLog.SetSelectedQuest then call(C_QuestLog.SetSelectedQuest, id)
  elseif type(SelectQuestLogEntry) == "function" then call(SelectQuestLogEntry, idx) end
  local pushable = true
  if C_QuestLog and C_QuestLog.IsPushableQuest then pushable = call(C_QuestLog.IsPushableQuest, id) ~= false
  elseif type(GetQuestLogPushable) == "function" then pushable = call(GetQuestLogPushable) and true or false end
  if not pushable then T:Print("Cette quête ne peut pas être partagée."); return false end
  if type(QuestLogPushQuest) ~= "function" then return false end
  call(QuestLogPushQuest)
  return true
end

-- ------------------------------------------------------------------ instantané pour TreeAI (SavedVariables)
function S:SaveSnapshot()
  if not (T.Summary and T.db) then return end
  local ok, s = pcall(T.Summary, T)
  if not ok or type(s) ~= "table" then return end
  local list = self._list or {}
  if #list < 2 then return end
  local g = { v = PROTO, updated = epoch(), size = #list, leader = E.Leader(list), members = {}, advices = {}, common = {} }
  local xs, xn = 0, 0
  for _, m in ipairs(list) do
    local st = m.st
    g.members[#g.members + 1] = {
      name = m.name, class = CLASS_FR[st.c] or st.c, level = st.l, xp = st.x, rested = st.r, zone = st.z,
      hp = st.h, mana = st.p >= 0 and st.p or nil, dead = st.d > 0 or nil, bags = st.b, durability = st.du >= 0 and st.du or nil,
      ammo = st.am >= 0 and st.am or nil, leaveMin = m.leaveMin, ready = st.rd == 1, afk = st.af == 1 or nil,
      professions = st.pr, me = (m.name == myName()) or nil,
    }
    if st.xh > 0 then xs, xn = xs + st.xh, xn + 1 end
  end
  if xn > 0 then g.xph = floor(xs / xn) end
  for i, a in ipairs(self.advices or {}) do
    if i > 8 then break end
    g.advices[#g.advices + 1] = { cat = a.cat, prio = a.prio, text = a.text }
  end
  for i, e in ipairs(E.QuestIndex(list)) do
    if i > 10 then break end
    g.common[#g.common + 1] = { id = e.id, title = e.title, holders = #e.holders, done = #e.done, group = e.group or nil }
  end
  s.group = g
end

-- ------------------------------------------------------------------ section « Groupe » du panneau
local section, rows = nil, {}
local CLASS_COLOR = {
  WARRIOR = "c79c6e", PALADIN = "f58cba", HUNTER = "abd473", ROGUE = "fff569", PRIEST = "ffffff",
  SHAMAN = "0070de", MAGE = "69ccf0", WARLOCK = "9482c9", DRUID = "ff7d0a",
}
local MAX_ROWS = 1 + 5 + 3

local function makeSection()
  if section or type(CreateFrame) ~= "function" then return section end
  local f = call(CreateFrame, "Frame", nil, UIParent)
  if not f then return nil end
  section = f
  f:SetSize(260, 20)
  for i = 1, MAX_ROWS do
    local b = CreateFrame("Button", nil, f)
    b:SetHeight(14)
    b:SetPoint("TOPLEFT", f, "TOPLEFT", 8, -4 - (i - 1) * 14)
    b:SetPoint("TOPRIGHT", f, "TOPRIGHT", -8, -4 - (i - 1) * 14)
    b.text = b:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
    b.text:SetPoint("LEFT", b, "LEFT", 0, 0)
    b.text:SetPoint("RIGHT", b, "RIGHT", 0, 0)
    b.text:SetJustifyH("LEFT")
    if b.text.SetWordWrap then b.text:SetWordWrap(false) end
    b:SetScript("OnClick", function(self)
      local a = self.action
      if a and a.kind == "share" then S:ShareQuest(a.quest) end
    end)
    b:Hide()
    rows[i] = b
  end
  f:Hide()
  return f
end

local function short(s, n)
  s = tostring(s or "?")
  return #s > n and (utf8cut(s, n - 1) .. "…") or s
end

function S:RefreshSection()
  local panel = T.GetPanel and T:GetPanel()
  local list = self._list or {}
  if not (self.db and self.db.section) or not panel or not panel:IsShown() or #list < 2 or not self:InGroup() then
    if section then section:Hide() end
    return
  end
  if not makeSection() then return end
  section:ClearAllPoints()
  section:SetPoint("TOPLEFT", panel, "BOTTOMLEFT", 0, 2)
  section:SetPoint("TOPRIGHT", panel, "BOTTOMRIGHT", 0, 2)
  local lines = {}
  local xs, xn = 0, 0
  for _, m in ipairs(list) do if m.st.xh > 0 then xs, xn = xs + m.st.xh, xn + 1 end end
  lines[1] = { text = "|cff33cc66Groupe|r" .. (xn > 0 and fmt("  XP/h ~%.1fk", xs / xn / 1000) or "") }
  local me = myName()
  for i, m in ipairs(list) do
    if i > 5 then break end
    local st = m.st
    local life = st.d == 2 and "|cffff5544fantôme|r" or (st.d == 1 and "|cffff5544mort|r")
      or (st.p >= 0 and fmt("%d/%d%%", st.h, st.p) or fmt("%d%%", st.h))
    lines[#lines + 1] = { text = fmt("|cff%s%s|r %d · %s · %s · sacs %d%s", CLASS_COLOR[st.c] or "cccccc", short(m.name, 10), st.l,
      short(st.z or "?", 14), life, st.b, m.stale and " |cff888888(?)|r" or "") }
  end
  for i, a in ipairs(self.advices or {}) do
    if i > 3 then break end
    local color = a.prio >= 3 and "ff4d40" or (a.prio == 2 and "ffd133" or "d9d9d9")
    local action = (a.action and a.action.kind == "share" and a.action.by == me) and a.action or nil
    lines[#lines + 1] = { text = fmt("|cff%s• %s|r%s", color, a.text, action and " |cff66ccff[Partager]|r" or ""), action = action }
  end
  for i = 1, MAX_ROWS do
    local b, l = rows[i], lines[i]
    if l then b.text:SetText(l.text); b.action = l.action; b:Show() else b.action = nil; b:Hide() end
  end
  section:SetHeight(8 + 14 * #lines)
  section:Show()
end

-- ------------------------------------------------------------------ commandes
function S:Report()
  local list = self._list or {}
  if #list < 2 then T:Print("Groupe : aucun ami avec TreeAI détecté (il faut l'addon chez eux)."); return end
  for _, m in ipairs(list) do
    local st = m.st
    T:Print(fmt("%s — %s %d (%d %%), %s, PV %d%s, sacs %d%s%s", m.name, CLASS_FR[st.c] or st.c, st.l, st.x,
      st.z or "?", st.h, st.p >= 0 and fmt(", mana %d", st.p) or "", st.b,
      m.leaveMin and fmt(", part dans %d min", m.leaveMin) or "", st.rd == 0 and ", pas prêt" or ""))
  end
  local adv = self.advices or {}
  if #adv == 0 then T:Print("Rien à signaler pour le groupe.") end
  for i, a in ipairs(adv) do
    if i > 8 then break end
    T:Print("• " .. a.text)
  end
end

function S:SetLeave(minutes)
  minutes = tonumber(minutes)
  if not self.char then return end
  if not minutes then
    local lm = leaveMinutes(self.char)
    T:Print(lm >= 0 and fmt("Départ annoncé dans %d min (/tai partir 0 pour annuler).", lm) or "Usage : /tai partir 20")
    return
  end
  if minutes <= 0 then self.char.leaveAt = 0; T:Print("Départ annulé.")
  else
    self.char.leaveAt = epoch() + floor(mmin(minutes, 600)) * 60
    T:Print(fmt("Le groupe saura que tu pars dans %d min.", floor(mmin(minutes, 600))))
  end
  lastSig = nil
end

function S:SetReady(arg)
  if not self.char then return end
  arg = tostring(arg or ""):lower()
  if arg == "on" or arg == "oui" then self.char.ready = true
  elseif arg == "off" or arg == "non" then self.char.ready = false
  else self.char.ready = not (self.char.ready ~= false) end
  T:Print(self.char.ready ~= false and "Tu es prêt." or "Le groupe voit que tu n'es pas prêt.")
  lastSig = nil
end

-- ------------------------------------------------------------------ cycle de vie
function S:OnInit()
  if T.RegisterSlash then
    T:RegisterSlash("groupe", function() S:Report() end, "état du groupe et conseils à plusieurs")
    T:RegisterSlash("partir", function(rest) S:SetLeave(rest) end, "partir N : prévenir le groupe que tu pars dans N min")
    T:RegisterSlash("pret", function(rest) S:SetReady(rest) end, "pret [on|off] : signaler si tu es prêt")
  end
end

function S:OnEnable()
  if T.RegisterComm then
    for _, kind in ipairs({ "SQS", "SQQ", "SQR" }) do
      T:RegisterComm(kind, S, function(_, payload, sender) S:OnComm(kind, payload, sender) end)
    end
  end
  self:RegisterEvent("GROUP_ROSTER_UPDATE", function()
    S:RefreshRoster()
    pending.request, pending.state, pending.quests = true, true, true
    S._dirty = true
  end)
  self:RegisterEvent("QUEST_LOG_UPDATE", function() questsDirty = true end)
  self:RegisterEvent("PLAYER_XP_UPDATE", function() trackXP() end)
  self:RegisterEvent("PLAYER_TARGET_CHANGED", function() if S._state then S:Recompute() end end)
  self:RegisterEvent("START_LOOT_ROLL", function(_, _, rollID) S:OnLootRoll(rollID) end)
  self:RegisterEvent("PLAYER_LOGOUT", function() S:SaveSnapshot() end)
  self:On("SUMMARY", function() S:SaveSnapshot() end)
  trackXP()
  self:RefreshRoster()
  pending.request = true
  if T.NewTicker and not self._ticker then
    self._ticker = T:NewTicker(1, function() if S:IsEnabled() then S:Tick() end end)
  end
end

function S:OnDisable()
  if self._ticker and self._ticker.Cancel then self._ticker:Cancel() end
  self._ticker = nil
  if section then section:Hide() end
end

-- Conseil de jet de dés (texte seulement, jamais de jet automatique).
function S:OnLootRoll(rollID)
  if not (self.db and self.db.loot) or #(self._list or {}) < 2 then return end
  local _, name = call(GetLootRollItemInfo, rollID)
  local link = call(GetLootRollItemLink, rollID)
  if not (name or link) then return end
  local iname, _, _, _, _, _, _, _, equipLoc, _, _, classID, subClassID = call(API.ItemInfo, link or name)
  local hint = E.LootHint({ name = iname or name, classID = classID, subClassID = subClassID, equipLoc = equipLoc }, self._list)
  if hint then self:Suggest("roll" .. tostring(rollID), hint, 1, { ttl = 60, cooldown = 60 }) end
end
