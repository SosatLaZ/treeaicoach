-- TreeAI — module Missions (agent MISSIONS) : le suivi des missions (quêtes) du groupe d'amis.
-- S'appuie sur Squad (états + journaux de quêtes partagés) et ajoute :
--   * tableau des missions : qui a quoi, progression de chacun par objectif (barres), qui est prêt à
--     rendre, qui attend encore des objets, % du groupe, « mission terminée » ; trié « finissable
--     ensemble maintenant » d'abord ;
--   * répartition : qui fait quoi (tuer ensemble = crédit partagé, qui loote d'abord, chacun clique,
--     groupes éloignés), « Charlie : 2 Crocs restants, Bravo : terminé » ; « je m'en charge » partagé
--     (`/tai prends <quête>` ou clic sur la ligne) ;
--   * route du groupe : prochaine mission (le plus de membres, la plus proche), cible de groupe posée
--     par le chef (clic) et transmise à la boussole (`TreeAI.Compass:SetTarget`, si présente) ;
--   * événements : « Bravo a fini … », « Tous prêts à rendre … », abandon, membre sans TreeAI (à
--     installer), montée de niveau, mort, loin / hors ligne / AFK, « attendez X » au moment de rendre
--     une quête qui ouvre une suite ;
--   * session : objectif du soir (« niveau 30 pour tous », « 15 quêtes », une quête), progression,
--     ETA d'après l'XP/h du groupe, récap (`/tai mission recap`) ; résumé
--     `TreeAIDB.chars[perso].summary.missions` (lu par treeaicoach/wow/ai_context.py).
-- Conseils seulement : aucune action à la place du joueur. Jamais de sélection dans le journal de
-- quêtes (SelectQuestLogEntry / SetSelectedQuest), jamais d'ouverture de panneau Blizzard, aucun
-- champ écrit sur un cadre Blizzard (cf. correctif 3.8.2). Lecture seule : C_QuestLog.GetInfo /
-- GetQuestObjectives / GetDistanceSqToQuest / GetQuestsOnMap, GetQuestLogLeaderBoard (classique).
--
-- Protocole propre (v1, en plus de Squad v2, qui n'est pas modifié ; via TreeAI:Send, PARTY/RAID) :
--   MSM  "1:" .. "id,dist,mapID,x,y,obj1/obj2/obj3;…"  infos de quêtes locales (distance en yards/10,
--        -1 inconnue ; point carte x,y en millièmes, -1 ; noms d'objectifs nettoyés, 18 octets max)
--   MSC  "1:" .. "id.j,id.j"  la liste COMPLÈTE de mes prises en charge (j = objectif, 0 = la quête)
--   MST  { v = 1, q, m, x, y, l }  cible du groupe (acceptée seulement du chef du groupe)
--   MSG  { v = 1, k = "level"|"quests"|"quest"|"off", n, l }  objectif de session (dernier gagne)
--   MSR  { v = 1, k = "ask"|"ans", id, r = 1|0, w }  vérification « prêt ? » maison (pas DoReadyCheck)
-- Aussi : butin du groupe (CHAT_MSG_LOOT), où est chacun (distance + direction), marquage conseillé
-- (SetRaidTarget sur clic, hors combat, une marque par clic), classement XP de la session, plan de
-- voyage, qui peut fabriquer quoi (métiers partagés par Squad, champ pr).
-- Réception : membres actuels du groupe seulement, seau de jetons par membre, version ≠ 1 ignorée,
-- tout revalidé (bornes, longueurs, caractères). Lua 5.1, aucune globale créée.
local _, ns = ...
local T = TreeAI
if type(T) ~= "table" or type(T.RegisterModule) ~= "function" then return end

local PROTO = 1
local INFO_EVERY = 60
local CLAIM_TTL = 1800
local GRACE_NOADDON = 45
local FAR_YARDS = 70        -- crédit de kill partagé
local CLUSTER_YARDS = 70
local SESSION_GAP = 3 * 3600
local MAX_Q = 25

local floor, mmax, mmin, sqrt = math.floor, math.max, math.min, math.sqrt
local tconcat, tsort, tinsert = table.concat, table.sort, table.insert
local fmt = string.format

local M = T:RegisterModule("Missions", {
  title = "Missions de groupe",
  defaults = {
    enabled = true, tab = true, toasts = true, joinLeave = true, progress = true, alerts = true,
    noAddon = true, waypoint = true, turnInWarn = true, chat = false,
  },
  charDefaults = { session = false, claims = false },
  options = {
    { key = "tab",        type = "toggle", label = "Onglet « Groupe » : tableau des missions" },
    { key = "toasts",     type = "toggle", label = "Annonces (fini, prêts à rendre, abandon…)" },
    { key = "progress",   type = "toggle", label = "Annoncer les quêtes finies par les amis" },
    { key = "alerts",     type = "toggle", label = "Morts, AFK, hors ligne, trop loin" },
    { key = "joinLeave",  type = "toggle", label = "Arrivées / départs du groupe, niveaux" },
    { key = "noAddon",    type = "toggle", label = "Signaler les membres sans TreeAI" },
    { key = "waypoint",   type = "toggle", label = "Suivre la cible du chef (boussole)" },
    { key = "turnInWarn", type = "toggle", label = "« Attendez … » avant de rendre" },
    { key = "chat",       type = "toggle", label = "Copier les annonces dans le chat" },
  },
})
if not M then return end
T.Missions = M
M.PROTO = PROTO

-- ------------------------------------------------------------------ utilitaires
local function pass(ok, ...) if ok then return ... end return nil end
local function call(fn, ...)
  if type(fn) ~= "function" then return nil end
  return pass(pcall(fn, ...))
end
local function now() return call(GetTime) or 0 end
local function epoch() return call(time) or 0 end

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
-- texte sûr : pas de codes |, pas de séparateurs du protocole, borné
local function clean(v, maxlen)
  if type(v) ~= "string" and type(v) ~= "number" then return nil end
  v = tostring(v):gsub("[%c|~;,:/%.]", " "):gsub("%s+", " "):gsub("^ ", ""):gsub(" $", "")
  if v == "" then return nil end
  return utf8cut(v, maxlen or 40)
end
M.Clean = clean
local function shortName(full)
  if type(full) ~= "string" or full == "" then return nil end
  return (full:gsub("%-.*$", ""))
end
local function q(s) return "« " .. tostring(s or "?") .. " »" end
local function low(s) return tostring(s or ""):lower() end

local function myName() return (call(UnitName, "player")) or "?" end
local function Squad() local S = T.Squad return (type(S) == "table" and S.MemberList) and S or nil end

-- XP par niveau (Classic 1→59) : estimations de l'objectif « niveau N » (Forever peut différer).
local XPT = { 400, 900, 1400, 2100, 2800, 3600, 4500, 5400, 6500, 7600, 8800, 10100, 11400, 12900, 14400, 16000,
  17700, 19400, 21300, 23200, 25200, 27300, 29400, 31700, 34000, 36400, 38900, 41400, 44300, 47400, 50800, 54500,
  58600, 62800, 67100, 71600, 76100, 80800, 85700, 90700, 95800, 101000, 106300, 111800, 117500, 123200, 129100,
  135100, 141200, 147500, 153900, 160400, 167100, 173900, 180800, 187900, 195000, 202300, 209800 }
local XPCUM = { [1] = 0 }
for i = 1, #XPT do XPCUM[i + 1] = XPCUM[i] + XPT[i] end
local MAXLVL = #XPT + 1
local function totalXP(level, pct)
  level = int(level, 1, MAXLVL, 1)
  if level >= MAXLVL then return XPCUM[MAXLVL] end
  return XPCUM[level] + (XPT[level] or 0) * (tonumber(pct) or 0) / 100
end
M.TotalXP = totalXP

local function duration(sec)
  sec = mmax(0, floor(tonumber(sec) or 0))
  local h, m = floor(sec / 3600), floor((sec % 3600) / 60)
  if h > 0 then return fmt("%d h %02d", h, m) end
  return fmt("%d min", mmax(1, m))
end
M.Duration = duration

-- ------------------------------------------------------------------ encodage (pur)
-- infos = liste { id, d (yards), m, x, y (0..1), names = {…} } -> chaîne
function M.EncodeInfo(list)
  local parts = {}
  for _, e in ipairs(list or {}) do
    local id = int(e.id, 1, 10000000, nil)
    if id and #parts < MAX_Q then
      local names = {}
      for j, n in ipairs(e.names or {}) do
        if j > 3 then break end
        names[j] = clean(n, 18) or ""
      end
      local x = tonumber(e.x) and int(e.x * 1000, 0, 1000, -1) or -1
      local y = tonumber(e.y) and int(e.y * 1000, 0, 1000, -1) or -1
      parts[#parts + 1] = fmt("%d,%d,%d,%d,%d,%s", id, tonumber(e.d) and int(e.d / 10, 0, 99999, -1) or -1,
        int(e.m, 0, 1000000, 0), x, y, tconcat(names, "/"))
    end
  end
  return tconcat(parts, ";")
end

-- chaîne -> [id] = { d = yards|nil, m, x, y (0..1|nil), names = {} }
function M.DecodeInfo(s)
  local out, n = {}, 0
  if type(s) ~= "string" or #s > 4000 then return out end
  for entry in s:gmatch("[^;]+") do
    local id, d, m, x, y, names = entry:match("^(%d+),(%-?%d+),(%d+),(%-?%d+),(%-?%d+),(.*)$")
    id = int(id, 1, 10000000, nil)
    if id then
      n = n + 1
      if n > MAX_Q then break end
      local e = { names = {} }
      d, x, y = int(d, -1, 99999, -1), int(x, -1, 1000, -1), int(y, -1, 1000, -1)
      e.d = d >= 0 and d * 10 or nil
      e.m = int(m, 0, 1000000, 0)
      if x >= 0 and y >= 0 and e.m > 0 then e.x, e.y = x / 1000, y / 1000 end
      local j = 0
      for nm in (names .. "/"):gmatch("([^/]*)/") do
        j = j + 1
        if j > 3 then break end
        e.names[j] = clean(nm, 18)
      end
      out[id] = e
    end
  end
  return out
end

-- prises en charge : liste { {q, j} } <-> "id.j,id.j"
function M.EncodeClaims(list)
  local t = {}
  for _, c in ipairs(list or {}) do
    local id, j = int(c.q, 1, 10000000, nil), int(c.j, 0, 6, 0)
    if id and #t < 10 then t[#t + 1] = id .. "." .. j end
  end
  return tconcat(t, ",")
end
function M.DecodeClaims(s)
  local out = {}
  if type(s) ~= "string" or #s > 200 then return out end
  for id, j in s:gmatch("(%d+)%.(%d+)") do
    id, j = int(id, 1, 10000000, nil), int(j, 0, 6, 0)
    if id and #out < 10 then out[#out + 1] = { q = id, j = j } end
  end
  return out
end

function M.DecodeTarget(p)
  if type(p) ~= "table" or tonumber(p.v) ~= PROTO then return nil end
  local x, y = tonumber(p.x), tonumber(p.y)
  if not x or not y or x < 0 or x > 1 or y < 0 or y > 1 then return nil end
  return { q = int(p.q, 0, 10000000, 0), m = int(p.m, 0, 1000000, 0), x = x, y = y, l = clean(p.l, 40) or "Cible du groupe" }
end

local GOAL_KIND = { level = true, quests = true, quest = true, off = true }
function M.DecodeGoal(p)
  if type(p) ~= "table" or tonumber(p.v) ~= PROTO or not GOAL_KIND[p.k] then return nil end
  local g = { k = p.k, n = int(p.n, 0, 10000000, 0), l = clean(p.l, 40) }
  if g.k == "level" then g.n = int(g.n, 2, MAXLVL, 2) end
  if g.k == "quests" then g.n = int(g.n, 1, 200, 1) end
  return g
end

-- nom d'objectif « Croc de loup : 3/8 » / « 3/8 Croc de loup » / « Croc tué (Terminé) » -> « Croc de loup »
function M.ObjName(text)
  if type(text) ~= "string" then return nil end
  local s = text:gsub("%b()", ""):gsub("%d+%s*/%s*%d+", ""):gsub("%s*:%s*$", ""):gsub("^%s*:%s*", "")
  return clean(s, 18)
end

-- ------------------------------------------------------------------ moteur (pur, déterministe)
local E = {}
M.Engine = E

local function sorted(members)
  local list = {}
  for _, m in ipairs(members or {}) do if type(m) == "table" and m.name and m.st then list[#list + 1] = m end end
  tsort(list, function(a, b) return a.name < b.name end)
  return list
end

local function pos(m)
  local st = m and m.st
  if st and st.py and st.px then return st.py, st.px, st.pi or -1 end
  return nil
end
local function dist(a, b)
  local ay, ax, ai = pos(a)
  local by, bx, bi = pos(b)
  if not ay or not by or ai ~= bi then return nil end
  return sqrt((ay - by) ^ 2 + (ax - bx) ^ 2)
end
E.Dist = dist

-- groupes de proximité : [nom] = n° de groupe (1 = celui du premier nom), nb de groupes
function E.Clusters(members)
  local list = sorted(members)
  local cid, n = {}, 0
  for _, m in ipairs(list) do
    if not cid[m.name] then
      n = n + 1
      cid[m.name] = n
      local stack = { m }
      while #stack > 0 do
        local a = table.remove(stack)
        for _, b in ipairs(list) do
          if not cid[b.name] then
            local d = dist(a, b)
            local same = d and d <= CLUSTER_YARDS
            if d == nil and not pos(a) and not pos(b) then same = a.st.z and a.st.z == b.st.z end
            if same then cid[b.name] = n; stack[#stack + 1] = b end
          end
        end
      end
    end
  end
  return cid, n
end

-- mission[id] = { id, title, level, group, dungeon, holders, done, need, missing, per[name] = {have,
--   need, done, obj = {{t, have, need, rem}}}, have, total, pct, allDone, objNames, dist, claims, tier,
--   shared (reste = seulement des monstres, crédit partagé), remaining }
-- ctx = { info = [nom] = DecodeInfo, claims = { [nom] = { {q, j} } }, me = nom }
function E.Board(members, ctx)
  ctx = ctx or {}
  local info, claims = ctx.info or {}, ctx.claims or {}
  local list = sorted(members)
  local byId, res = {}, {}
  for _, m in ipairs(list) do
    local ids = {}
    for id in pairs(m.quests or {}) do ids[#ids + 1] = id end
    tsort(ids)
    for _, id in ipairs(ids) do
      local qq = m.quests[id]
      local e = byId[id]
      if not e then
        e = { id = id, holders = {}, done = {}, need = {}, missing = {}, per = {}, have = 0, total = 0,
              objNames = {}, claims = {}, level = qq.level or 0, remaining = 0 }
        byId[id] = e
        res[#res + 1] = e
      end
      e.title = e.title or qq.title
      e.group = e.group or qq.group
      e.dungeon = e.dungeon or qq.dungeon
      e.holders[#e.holders + 1] = m.name
      local p = { have = 0, need = 0, done = qq.done and true or false, obj = {} }
      for j, o in ipairs(qq.obj or {}) do
        local need = o.need or 1
        local have = qq.done and need or mmin(o.have or 0, need)
        p.obj[j] = { t = o.t, have = have, need = need, rem = need - have }
        p.have, p.need = p.have + have, p.need + need
      end
      if p.need == 0 then p.need, p.have = 1, p.done and 1 or 0 end
      if p.done then p.have = p.need end
      e.per[m.name] = p
      e.have, e.total = e.have + p.have, e.total + p.need
      e.remaining = e.remaining + (p.need - p.have)
      if p.done then e.done[#e.done + 1] = m.name else e.need[#e.need + 1] = m.name end
    end
  end
  for _, e in ipairs(res) do
    e.title = e.title or ("quête #" .. e.id)
    local has = {}
    for _, n in ipairs(e.holders) do has[n] = true end
    for _, m in ipairs(list) do if not has[m.name] then e.missing[#e.missing + 1] = m.name end end
    e.pct = e.total > 0 and floor(e.have * 100 / e.total) or 0
    e.allDone = #e.need == 0
    -- noms d'objectifs : moi d'abord, puis par nom
    local order = {}
    if ctx.me and has[ctx.me] then order[1] = ctx.me end
    for _, n in ipairs(e.holders) do if n ~= ctx.me then order[#order + 1] = n end end
    local ds, dn = 0, 0
    for _, n in ipairs(order) do
      local inf = info[n] and info[n][e.id]
      if inf then
        for j = 1, 3 do if not e.objNames[j] and inf.names[j] then e.objNames[j] = inf.names[j] end end
        if inf.d then ds, dn = ds + inf.d, dn + 1 end
        if not e.poi and inf.x then e.poi = { m = inf.m, x = inf.x, y = inf.y, from = n } end
      end
    end
    e.dist = dn > 0 and floor(ds / dn) or nil
    -- reste uniquement des monstres (crédit partagé en groupe) ?
    local shared, any = true, false
    for _, n in ipairs(e.need) do
      for _, o in ipairs(e.per[n].obj) do
        if o.rem > 0 then any = true; if o.t ~= "m" then shared = false end end
      end
    end
    e.shared = any and shared
    for _, n in ipairs(e.holders) do
      for _, c in ipairs(claims[n] or {}) do
        if c.q == e.id then e.claims[#e.claims + 1] = { by = n, j = c.j } end
      end
    end
    e.multi = #e.holders >= 2
    e.tier = e.allDone and 0 or (e.shared and 1 or 2)
  end
  tsort(res, function(a, b)
    if a.multi ~= b.multi then return a.multi end
    if a.tier ~= b.tier then return a.tier < b.tier end
    if #a.holders ~= #b.holders then return #a.holders > #b.holders end
    if a.remaining ~= b.remaining then return a.remaining < b.remaining end
    local da, db = a.dist or 1e9, b.dist or 1e9
    if da ~= db then return da < db end
    return a.id < b.id
  end)
  return res
end

local function objLabel(e, j)
  return e.objNames[j] or ("objectif " .. j)
end

-- « Charlie : 2 Crocs restants » / « Bravo : terminé »
function E.MemberStatus(e, name)
  local p = e.per[name]
  if not p then return name .. " : pas la quête" end
  if p.done then return name .. " : terminé" end
  local bits, total = {}, 0
  for j, o in ipairs(p.obj) do
    if o.rem > 0 then
      total = total + o.rem
      bits[#bits + 1] = o.need > 1 and fmt("%d %s", o.rem, objLabel(e, j)) or objLabel(e, j)
      if #bits >= 2 then break end
    end
  end
  if #bits == 0 then return name .. " : presque" end
  return name .. " : " .. tconcat(bits, ", ") .. " restant" .. (total > 1 and "s" or "")
end

function E.StatusLine(e)
  local t = {}
  for _, n in ipairs(e.need) do t[#t + 1] = E.MemberStatus(e, n) end
  for _, n in ipairs(e.done) do t[#t + 1] = n .. " : terminé" end
  return tconcat(t, ", ")
end

local function claimFor(e, j)
  for _, c in ipairs(e.claims) do if c.j == j then return c.by end end
  for _, c in ipairs(e.claims) do if c.j == 0 then return c.by end end
  return nil
end

-- répartition : { {quest, obj, lead, text, claimed} } ; ctx.max (défaut 8)
function E.Assign(board, members, ctx)
  ctx = ctx or {}
  local cid = E.Clusters(members)
  local leads, out = {}, {}
  for _, e in ipairs(board or {}) do
    if #out >= (ctx.max or 8) then break end
    if e.multi and not e.allDone then
      local nobj = 0
      for _, n in ipairs(e.need) do nobj = mmax(nobj, #e.per[n].obj) end
      for j = 1, mmin(nobj, 6) do
        local needers, t = {}, nil
        for _, n in ipairs(e.need) do
          local o = e.per[n].obj[j]
          if o and o.rem > 0 then needers[#needers + 1] = { name = n, rem = o.rem }; t = t or o.t end
        end
        if #needers > 0 then
          tsort(needers, function(a, b)
            if a.rem ~= b.rem then return a.rem > b.rem end
            return a.name < b.name
          end)
          local label = objLabel(e, j)
          local claimed = claimFor(e, j)
          local lead = claimed
          if not lead then  -- le plus en retard, en évitant de surcharger quelqu'un qui mène déjà
            lead = needers[1].name
            for _, c in ipairs(needers) do
              if (leads[c.name] or 0) < (leads[lead] or 0) and c.rem * 2 >= needers[1].rem then lead = c.name end
            end
          end
          if t ~= "m" then leads[lead] = (leads[lead] or 0) + 1 end
          local rest = {}
          for _, c in ipairs(needers) do if c.name ~= lead then rest[#rest + 1] = fmt("%s %d", c.name, c.rem) end end
          local leadRem = 0
          for _, c in ipairs(needers) do if c.name == lead then leadRem = c.rem end end
          -- groupes éloignés : chacun avance de son côté
          local far = {}
          for _, c in ipairs(needers) do
            if cid[c.name] and cid[lead] and cid[c.name] ~= cid[lead] then far[#far + 1] = c.name end
          end
          local text
          if t == "m" then
            text = fmt("%s : tuez ensemble %s (crédit partagé) — reste %d pour %s%s", q(e.title), label,
              needers[1].rem, needers[1].name, #needers > 1 and (", " .. tconcat(rest, ", ")) or "")
          elseif t == "i" then
            text = fmt("%s : %s loote %s d'abord (%d)%s", q(e.title), lead, label, leadRem,
              #rest > 0 and (", puis " .. tconcat(rest, ", ")) or "")
          elseif t == "o" then
            text = fmt("%s : chacun clique %s — %s %d%s", q(e.title), label, lead, leadRem,
              #rest > 0 and (", " .. tconcat(rest, ", ")) or "")
          else
            text = fmt("%s : %s doit faire « %s » lui-même", q(e.title), lead, label)
          end
          if claimed then text = text .. fmt(" [%s s'en charge]", claimed) end
          if #far > 0 and t ~= "m" then text = text .. fmt(" ; %s (loin) : de ton côté", tconcat(far, ", ")) end
          out[#out + 1] = { quest = e.id, obj = j, lead = lead, text = text, claimed = claimed and true or false, t = t }
          if #out >= (ctx.max or 8) then break end
        end
      end
    end
  end
  return out
end

-- prochaine mission du groupe : rendre ensemble, sinon celle du plus de membres, la plus proche
function E.Next(board)
  local best
  for _, e in ipairs(board or {}) do
    if e.multi and e.allDone then
      return { mission = e, kind = "turnin", text = fmt("Rendez ensemble %s (%d/%d prêts)", q(e.title), #e.done, #e.holders) }
    end
  end
  for _, e in ipairs(board or {}) do
    if not e.allDone then
      if not best then best = e
      else
        local h1, h2 = #e.holders, #best.holders
        local d1, d2 = e.dist or 1e9, best.dist or 1e9
        if h1 > h2 or (h1 == h2 and (d1 < d2 or (d1 == d2 and e.pct > best.pct))) then best = e end
      end
    end
  end
  if not best then return nil end
  local why = fmt("%d membre%s", #best.holders, #best.holders > 1 and "s" or "")
  if best.dist then why = why .. fmt(", ~%d m", best.dist) end
  return { mission = best, kind = "do", text = fmt("Prochaine mission : %s (%s, %d %%)", q(best.title), why, best.pct) }
end

-- ------------------------------------------------------------------ événements (diff pur)
-- snap = { first = bool, m = { [nom] = { l, d, af, far, off, addon, me, quests = { [id] = {done, title} } } },
--          ready = { [id] = titre }, held = { [id] = { n = porteurs, title } } }
function E.Snapshot(members, roster, ctx)
  ctx = ctx or {}
  local s = { m = {}, ready = {}, held = {} }
  local me = ctx.me
  local meM
  for _, m in ipairs(sorted(members)) do if m.name == me then meM = m end end
  for _, m in ipairs(sorted(members)) do
    local st = m.st
    local e = { l = st.l, d = st.d, af = st.af, addon = true, me = m.name == me or nil, quests = {}, stale = m.stale }
    if meM and m.name ~= me then
      local d = dist(meM, m)
      e.far = (d and d > FAR_YARDS) or (not d and st.z and meM.st.z and st.z ~= meM.st.z) or false
      e.farDist = d and floor(d) or nil
    end
    for id, qq in pairs(m.quests or {}) do
      e.quests[id] = { done = qq.done and true or false, title = qq.title }
      local h = s.held[id] or { n = 0, done = 0, title = qq.title }
      h.n, h.done = h.n + 1, h.done + (qq.done and 1 or 0)
      s.held[id] = h
    end
    s.m[m.name] = e
  end
  for name, r in pairs(roster or {}) do
    local e = s.m[name]
    if e then e.off = r.online == false or nil
    else
      s.m[name] = { l = r.level, d = r.dead and 1 or 0, af = r.afk and 1 or 0, off = r.online == false or nil,
                    addon = false, noAddon = r.noAddon or nil, quests = false }
    end
  end
  for id, h in pairs(s.held) do if h.n >= 2 and h.done == h.n then s.ready[id] = h.title end end
  return s
end

-- -> liste { kind, who, quest, title, text, prio, me }
function E.Diff(prev, cur)
  local ev = {}
  if not prev or not cur then return ev end
  local function add(kind, who, text, prio, extra)
    local e = { kind = kind, who = who, text = text, prio = prio or 1 }
    for k, v in pairs(extra or {}) do e[k] = v end
    ev[#ev + 1] = e
  end
  local names = {}
  for n in pairs(cur.m) do names[#names + 1] = n end
  for n in pairs(prev.m) do if not cur.m[n] then names[#names + 1] = n end end
  tsort(names)
  for _, n in ipairs(names) do
    local a, b = prev.m[n], cur.m[n]
    if not a and b then
      if not b.me then
        add("join", n, b.addon and fmt("%s a rejoint le groupe (TreeAI ok)", n) or fmt("%s a rejoint le groupe", n), 1)
      end
    elseif a and not b then
      add("leave", n, fmt("%s a quitté le groupe", n), 1)
    elseif a and b then
      local me = b.me
      if not a.noAddon and b.noAddon then
        add("noaddon", n, fmt("%s n'a pas TreeAI : dis-lui d'installer TreeAI Coach (suivi des missions)", n), 2)
      end
      if a.noAddon and b.addon and not me then add("addon", n, fmt("%s a maintenant TreeAI", n), 1) end
      if (b.l or 0) > (a.l or 0) and a.l then add("level", n, fmt("%s passe niveau %d !", n, b.l), 1, { me = me, level = b.l, from = a.l }) end
      if (a.d or 0) == 0 and (b.d or 0) > 0 then add("death", n, fmt("%s est mort", n), 2, { me = me })
      elseif (a.d or 0) > 0 and (b.d or 0) == 0 then add("alive", n, fmt("%s est de retour", n), 1, { me = me }) end
      if (a.af or 0) == 0 and (b.af or 0) == 1 and not me then add("afk", n, fmt("%s est AFK", n), 1) end
      if not a.off and b.off then add("offline", n, fmt("%s est hors ligne", n), 2)
      elseif a.off and not b.off then add("online", n, fmt("%s est de retour en ligne", n), 1) end
      if not a.far and b.far and not me then
        add("far", n, b.farDist and fmt("%s est loin : hors portée du crédit de kill", n) or fmt("%s est dans une autre zone", n), 1)
      end
      if type(a.quests) == "table" and type(b.quests) == "table" and not b.stale then
        local ids = {}
        for id in pairs(a.quests) do ids[#ids + 1] = id end
        for id in pairs(b.quests) do if not a.quests[id] then ids[#ids + 1] = id end end
        tsort(ids)
        for _, id in ipairs(ids) do
          local qa, qb = a.quests[id], b.quests[id]
          if qa and not qb then
            if qa.done then add("turnin", n, fmt("%s a rendu %s", n, q(qa.title)), 1, { quest = id, title = qa.title, me = me })
            else add("abandon", n, fmt("%s a abandonné %s", n, q(qa.title)), 2, { quest = id, title = qa.title, me = me }) end
          elseif qb and not qa then
            local h = cur.held[id]
            if h and h.n >= 2 and not me then add("pickup", n, fmt("%s a pris %s", n, q(qb.title)), 1, { quest = id, title = qb.title }) end
          elseif qa and qb and not qa.done and qb.done then
            add("done", n, fmt("%s a fini %s", n, q(qb.title)), 1, { quest = id, title = qb.title, me = me })
          end
        end
      end
    end
  end
  local ids = {}
  for id in pairs(cur.ready) do if not prev.ready[id] then ids[#ids + 1] = id end end
  tsort(ids)
  for _, id in ipairs(ids) do
    add("ready", nil, fmt("Tous prêts à rendre %s", q(cur.ready[id])), 2, { quest = id, title = cur.ready[id] })
  end
  ids = {}
  for id, h in pairs(prev.held) do if h.n >= 2 and not cur.held[id] then ids[#ids + 1] = id end end
  tsort(ids)
  for _, id in ipairs(ids) do
    local turned = 0
    for _, e in ipairs(ev) do if e.kind == "turnin" and e.quest == id then turned = turned + 1 end end
    if turned > 0 then
      add("mission", nil, fmt("Mission terminée : %s", q(prev.held[id].title)), 2, { quest = id, title = prev.held[id].title })
    end
  end
  return ev
end

-- ------------------------------------------------------------------ objectif de session (pur)
-- goal = { k, n, l, start = { [nom] = {l, x} }, at = epoch } ; stats = { turned = n } ; t = epoch
function E.Goal(goal, members, stats, t)
  if type(goal) ~= "table" or goal.k == "off" then return nil end
  local list = sorted(members)
  stats = stats or {}
  local res = { kind = goal.k }
  if goal.k == "level" then
    local target = totalXP(goal.n, 0)
    local sum, n, eta, slow, done = 0, 0, 0, nil, 0
    local xs, xn = 0, 0
    for _, m in ipairs(list) do if (m.st.xh or 0) > 0 then xs, xn = xs + m.st.xh, xn + 1 end end
    local avg = xn > 0 and xs / xn or 0
    for _, m in ipairs(list) do
      local cur = totalXP(m.st.l, m.st.x)
      local s0 = goal.start and goal.start[m.name]
      local base = s0 and totalXP(s0.l, s0.x) or cur
      local span = target - base
      local p = (span <= 0 or cur >= target) and 1 or mmax(0, (cur - base) / span)
      if cur >= target then done = done + 1 end
      sum, n = sum + p, n + 1
      local rate = (m.st.xh or 0) > 0 and m.st.xh or avg
      if cur < target then
        local sec = rate > 0 and (target - cur) * 3600 / rate or nil
        if not sec then eta = nil elseif eta and sec > eta then eta, slow = sec, m.name end
      end
    end
    res.pct = n > 0 and floor(sum * 100 / n) or 0
    res.done = n > 0 and done == n
    res.eta = eta
    res.slow = slow
    res.text = fmt("Objectif : niveau %d pour tous — %d/%d, %d %%", goal.n, done, n, res.pct)
  elseif goal.k == "quests" then
    local turned = stats.turned or 0
    res.pct = mmin(100, floor(turned * 100 / mmax(1, goal.n)))
    res.done = turned >= goal.n
    local el = (t or 0) - (goal.at or t or 0)
    if turned > 0 and el > 60 and not res.done then res.eta = (goal.n - turned) * el / turned end
    res.text = fmt("Objectif : %d quêtes rendues — %d/%d", goal.n, turned, goal.n)
  elseif goal.k == "quest" then
    local holders, done = 0, 0
    for _, m in ipairs(list) do
      local qq = m.quests and m.quests[goal.n]
      if qq then holders = holders + 1; if qq.done then done = done + 1 end end
    end
    res.done = (stats.turnedIds or {})[goal.n] and holders == 0 or false
    res.pct = res.done and 100 or (holders > 0 and floor(done * 100 / holders) or 0)
    res.text = fmt("Objectif : %s — %s", q(goal.l or ("quête #" .. goal.n)),
      res.done and "rendue !" or fmt("%d/%d prêts", done, holders))
  end
  if res.done then res.text = res.text .. " — atteint !"
  elseif res.eta then
    res.text = res.text .. " · ~" .. duration(res.eta) .. (res.slow and (" (" .. res.slow .. ")") or "")
  end
  return res
end

-- ------------------------------------------------------------------ état local
local info = {}          -- [nom] = DecodeInfo(...) (moi compris)
local claims = {}        -- [nom] = { {q, j} }
local buckets = {}
local roster = {}        -- [nom] = unité
local joined = {}        -- [nom] = GetTime() d'arrivée (pour « sans TreeAI »)
local myInfoEnc, infoDirty, lastScan, lastInfoSent = "", true, -1e9, -1e9
local pend = { info = false, claims = false, target = nil, goal = nil }
local nextSend = 0
local prevSnap
M._info, M._claims = info, claims

local function allow(name)
  local t = now()
  local b = buckets[name]
  if not b then b = { tok = 6, t = t }; buckets[name] = b end
  b.tok = mmin(6, b.tok + (t - b.t) / 2)
  b.t = t
  if b.tok < 1 then return false end
  b.tok = b.tok - 1
  return true
end

function M:RefreshRoster()
  for k in pairs(roster) do roster[k] = nil end
  local raid = call(IsInRaid)
  local prefix, n = raid and "raid" or "party", raid and 40 or 4
  local t = now()
  local me = myName()
  for i = 1, n do
    local u = prefix .. i
    if call(UnitExists, u) then
      local nm = shortName(call(UnitName, u))
      if nm and nm ~= me then
        roster[nm] = u
        joined[nm] = joined[nm] or t
      end
    end
  end
  for nm in pairs(joined) do if not roster[nm] then joined[nm] = nil end end
end

function M:IsMember(sender)
  local name = shortName(sender)
  if not name or name == myName() then return false, name end
  if not roster[name] then self:RefreshRoster() end
  return roster[name] ~= nil, name
end

function M:IsLeader(name)
  if name == nil or name == myName() then
    if type(UnitIsGroupLeader) == "function" then return call(UnitIsGroupLeader, "player") and true or false end
  else
    local u = roster[name]
    if u and type(UnitIsGroupLeader) == "function" then return call(UnitIsGroupLeader, u) and true or false end
  end
  local S = Squad()
  return S and S:Leader() == (name or myName()) or false
end

-- ------------------------------------------------------------------ lecture du journal (lecture seule)
function M:ScanInfo()
  local S = Squad()
  if not S or type(S.ScanQuests) ~= "function" then return {} end
  local ok, qs = pcall(S.ScanQuests, S)
  if not ok or type(qs) ~= "table" then return {} end
  local QL = C_QuestLog
  local mapID = (C_Map and C_Map.GetBestMapForUnit and call(C_Map.GetBestMapForUnit, "player")) or 0
  local pois = {}
  if QL and QL.GetQuestsOnMap and mapID > 0 then
    for _, p in ipairs(call(QL.GetQuestsOnMap, mapID) or {}) do
      if type(p) == "table" and p.questID and tonumber(p.x) and tonumber(p.y) then pois[p.questID] = p end
    end
  end
  local out = {}
  for _, qq in ipairs(qs) do
    if #out >= MAX_Q then break end
    local e = { id = qq.id, names = {} }
    if QL and QL.GetQuestObjectives then
      for j, o in ipairs(call(QL.GetQuestObjectives, qq.id) or {}) do
        if j > 3 then break end
        e.names[j] = M.ObjName(type(o) == "table" and o.text) or ""
      end
    elseif type(GetQuestLogLeaderBoard) == "function" and qq.idx then
      for j = 1, 3 do
        local text = call(GetQuestLogLeaderBoard, j, qq.idx)
        if not text then break end
        e.names[j] = M.ObjName(text) or ""
      end
    end
    if QL and QL.GetDistanceSqToQuest then
      local d2 = call(QL.GetDistanceSqToQuest, qq.id)
      if tonumber(d2) and d2 >= 0 then e.d = sqrt(d2) end
    end
    local p = pois[qq.id]
    if p then e.m, e.x, e.y = mapID, p.x, p.y end
    out[#out + 1] = e
  end
  return out
end

-- ------------------------------------------------------------------ comms
function M:OnComm(kind, payload, sender)
  local ok, name = self:IsMember(sender)
  if not ok or not allow(name) then return end
  if kind == "MSM" or kind == "MSC" then
    if type(payload) ~= "string" then return end
    local ver, body = payload:match("^(%d+):(.*)$")
    if tonumber(ver) ~= PROTO then return end
    if kind == "MSM" then info[name] = M.DecodeInfo(body)
    else
      local list = M.DecodeClaims(body)
      for _, c in ipairs(list) do c.at = now() end
      claims[name] = list
    end
  elseif kind == "MST" then
    local tg = M.DecodeTarget(payload)
    if not tg or not self:IsLeader(name) then return end
    tg.by = name
    self:ApplyTarget(tg, true)
  elseif kind == "MSG" then
    local g = M.DecodeGoal(payload)
    if not g then return end
    self:ApplyGoal(g, name)
  elseif kind == "MSR" then
    self:OnReadyMsg(payload, name)
  else
    return
  end
  self._dirty = true
end

local function inGroup() return T.DefaultChannel and T:DefaultChannel() ~= nil or false end

function M:MyClaims()
  local list, t = {}, now()
  for _, c in ipairs(claims[myName()] or {}) do
    if t - (c.at or t) <= CLAIM_TTL then list[#list + 1] = c end
  end
  claims[myName()] = list
  return list
end

function M:Pump(t)
  if t < nextSend or not (T.Send and inGroup()) then return end
  local kind, payload
  if pend.ready then kind, payload = "MSR", pend.ready; pend.ready = nil
  elseif pend.readyAns then kind, payload = "MSR", pend.readyAns; pend.readyAns = nil
  elseif pend.target then kind, payload = "MST", pend.target; pend.target = nil
  elseif pend.goal then kind, payload = "MSG", pend.goal; pend.goal = nil
  elseif pend.claims then kind, payload = "MSC", PROTO .. ":" .. M.EncodeClaims(self:MyClaims()); pend.claims = false
  elseif pend.info then kind, payload = "MSM", PROTO .. ":" .. myInfoEnc; pend.info = false; lastInfoSent = t
  end
  if not kind then return end
  T:Send(kind, payload)
  local size = type(payload) == "string" and #payload or 120
  nextSend = t + mmax(2, math.ceil(size / 220))
end

-- ------------------------------------------------------------------ prises en charge
function M:FindMission(arg)
  local board = self.board or {}
  arg = tostring(arg or ""):gsub("^%s+", ""):gsub("%s+$", "")
  if arg == "" then return nil end
  local n = tonumber(arg)
  if n then
    if board[n] and n <= 20 then return board[n] end
    for _, e in ipairs(board) do if e.id == n then return e end end
  end
  local a = low(arg)
  for _, e in ipairs(board) do if low(e.title) == a then return e end end
  for _, e in ipairs(board) do if low(e.title):find(a, 1, true) then return e end end
  return nil
end

-- bascule : prendre / lâcher (j = objectif, 0 = toute la quête)
function M:Claim(e, j)
  if not e then return false end
  j = int(j, 0, 6, 0)
  local mine = self:MyClaims()
  for i, c in ipairs(mine) do
    if c.q == e.id and c.j == j then
      table.remove(mine, i)
      pend.claims = true
      self._dirty = true
      T:Print(fmt("Tu lâches %s.", q(e.title)))
      return false
    end
  end
  if #mine >= 10 then table.remove(mine, 1) end
  mine[#mine + 1] = { q = e.id, j = j, at = now() }
  pend.claims = true
  self._dirty = true
  T:Print(fmt("Tu t'occupes de %s%s : le groupe est prévenu.", q(e.title), j > 0 and (" (" .. objLabel(e, j) .. ")") or ""))
  return true
end

function M:SlashClaim(rest)
  rest = tostring(rest or "")
  if rest == "" then
    local any = false
    for _, e in ipairs(self.board or {}) do
      for _, c in ipairs(e.claims) do
        any = true
        T:Print(fmt("%s : %s%s", c.by, e.title, c.j > 0 and (" (" .. objLabel(e, c.j) .. ")") or ""))
      end
    end
    if not any then T:Print("Personne n'a rien pris. Usage : /tai prends <quête|n°> [objectif]") end
    return
  end
  if rest == "off" or rest == "rien" then
    claims[myName()] = {}
    pend.claims = true
    self._dirty = true
    T:Print("Tu ne prends plus rien en charge.")
    return
  end
  local name, j = rest:match("^(.-)%s+(%d)$")
  if not name then name, j = rest, 0 end
  local e = self:FindMission(name) or self:FindMission(rest)
  if e and name ~= rest and not self:FindMission(name) then j = 0 end
  if not e then T:Print("Mission introuvable : " .. rest .. " (voir /tai mission)") return end
  self:Claim(e, j)
end

-- ------------------------------------------------------------------ cible du groupe (boussole)
function M:ApplyTarget(tg, remote)
  self.target = tg
  self._dirty = true
  if remote and self.db and self.db.waypoint == false then return end
  local C = T.Compass
  if type(C) == "table" and type(C.SetTarget) == "function" then
    pcall(C.SetTarget, C, { x = tg.x, y = tg.y, mapID = tg.m, label = tg.l, source = "group", questID = tg.q })
  end
  if remote then self:Toast("target", fmt("Cible du groupe (%s) : %s", tg.by or "chef", tg.l), 2) end
end

function M:SetGroupTarget(e)
  e = e or (self.next and self.next.mission)
  if not e then T:Print("Aucune mission à cibler.") return false end
  local poi = e.poi
  if not poi then
    T:Print(fmt("Pas de point sur la carte pour %s (ouvre la carte de sa zone).", q(e.title)))
    return false
  end
  local tg = { v = PROTO, q = e.id, m = poi.m, x = poi.x, y = poi.y, l = clean(e.title, 40) or "Mission" }
  local leader = self:IsLeader(nil)
  self:ApplyTarget({ q = tg.q, m = tg.m, x = tg.x, y = tg.y, l = tg.l, by = myName() }, false)
  if leader and inGroup() then
    pend.target = tg
    T:Print(fmt("Cible du groupe : %s (envoyée aux amis).", q(e.title)))
  else
    T:Print(fmt("Cible : %s (seul le chef du groupe la partage).", q(e.title)))
  end
  return true
end

-- ------------------------------------------------------------------ session / objectif
local function newSession()
  return { start = epoch(), last = epoch(), goal = false, startLv = {}, turned = 0, turnedIds = {}, titles = {},
           byMember = {}, deaths = {}, abandoned = 0, missions = 0, levels = {}, events = {} }
end

function M:Session()
  if not self.char then return nil end
  local s = self.char.session
  if type(s) ~= "table" or epoch() - (s.last or 0) > SESSION_GAP then
    s = newSession()
    self.char.session = s
  end
  return s
end

local function startLevels(s, list)
  for _, m in ipairs(list or {}) do
    if not s.startLv[m.name] then s.startLv[m.name] = { l = m.st.l, x = m.st.x } end
  end
end

function M:ApplyGoal(g, by)
  local s = self:Session()
  if not s then return end
  if g.k == "off" then s.goal = false
  else
    local start = {}
    for _, m in ipairs(self.list or {}) do start[m.name] = { l = m.st.l, x = m.st.x } end
    s.goal = { k = g.k, n = g.n, l = g.l, start = start, at = epoch(), by = by, turned0 = s.turned }
  end
  self._dirty = true
  if by and by ~= myName() then
    self:Toast("goal", g.k == "off" and fmt("%s a retiré l'objectif de session", by)
      or fmt("Objectif de %s : %s", by, M.GoalLabel(g)), 2)
  end
end

function M.GoalLabel(g)
  if g.k == "level" then return fmt("niveau %d pour tous", g.n) end
  if g.k == "quests" then return fmt("%d quêtes rendues", g.n) end
  if g.k == "quest" then return "rendre " .. q(g.l or ("#" .. g.n)) end
  return "aucun"
end

function M:SlashGoal(rest)
  rest = low(rest):gsub("^%s+", ""):gsub("%s+$", "")
  local g
  local lv = rest:match("^niveau%s+(%d+)") or rest:match("^lvl%s+(%d+)") or rest:match("^(%d+)$")
  local nq = rest:match("^qu[eê]tes?%s+(%d+)") or rest:match("^(%d+)%s+qu[eê]tes?")
  if rest == "off" or rest == "aucun" then g = { v = PROTO, k = "off", n = 0 }
  elseif lv then g = { v = PROTO, k = "level", n = int(lv, 2, MAXLVL, 2) }
  elseif nq then g = { v = PROTO, k = "quests", n = int(nq, 1, 200, 1) }
  elseif rest ~= "" then
    local e = self:FindMission(rest)
    if not e then T:Print("Usage : /tai mission objectif niveau 30 | quetes 15 | <quête> | off") return end
    g = { v = PROTO, k = "quest", n = e.id, l = clean(e.title, 40) }
  else
    local s = self:Session()
    local gs = s and s.goal and E.Goal(s.goal, self.list, s, epoch())
    T:Print(gs and gs.text or "Pas d'objectif. Ex. : /tai mission objectif niveau 30")
    return
  end
  local dg = M.DecodeGoal(g)
  self:ApplyGoal(dg, nil)
  pend.goal = g
  T:Print(g.k == "off" and "Objectif de session retiré." or ("Objectif du groupe : " .. M.GoalLabel(dg) .. "."))
end

-- statistiques de session tirées des événements
function M:Record(ev, s)
  if not s then return end
  if ev.kind == "turnin" then
    if not s.turnedIds[ev.quest] then
      s.turnedIds[ev.quest] = true
      s.turned = s.turned + 1
      if #s.titles < 30 then s.titles[#s.titles + 1] = ev.title end
    end
    s.byMember[ev.who] = (s.byMember[ev.who] or 0) + 1
  elseif ev.kind == "death" then s.deaths[ev.who] = (s.deaths[ev.who] or 0) + 1
  elseif ev.kind == "abandon" then s.abandoned = s.abandoned + 1
  elseif ev.kind == "mission" then s.missions = s.missions + 1
  elseif ev.kind == "level" then s.levels[ev.who] = ev.level
  end
  if ev.kind ~= "pickup" and ev.kind ~= "far" then
    tinsert(s.events, 1, { t = epoch(), text = ev.text })
    while #s.events > 20 do table.remove(s.events) end
  end
end

-- ------------------------------------------------------------------ annonces
local TOAST_FOR = {
  done = "progress", ready = "toasts", mission = "toasts", turnin = "toasts", abandon = "toasts", pickup = "progress",
  death = "alerts", alive = "alerts", afk = "alerts", offline = "alerts", online = "alerts", far = "alerts",
  join = "joinLeave", leave = "joinLeave", level = "joinLeave", addon = "joinLeave", noaddon = "noAddon",
}
-- événements de groupe qui font un petit son (TreeAI:Sound("group"), cooldown + jamais en combat)
M.SOUND_EVENTS = { join = true, ready = true, readyok = true, mission = true, death = true, level = true, target = true }
function M:Toast(key, text, prio, opts)
  if not (self.db and self.db.toasts) then return end
  opts = opts or {}
  local k0 = tostring(key):match("^[^:]+")
  self:Suggest("ev:" .. key, text, prio or 1, { ttl = opts.ttl or 15, cooldown = opts.cooldown or 60,
    chat = self.db.chat or nil, onClick = opts.onClick, tab = "group",
    sound = M.SOUND_EVENTS[k0] or nil, soundKind = M.SOUND_EVENTS[k0] and "group" or nil })
end

local INVITE = "[TreeAI] Installe TreeAI Coach (addon WoW gratuit) : on verra tes quêtes, ta progression et les missions du groupe."
function M:Invite(name)
  -- message privé envoyé UNIQUEMENT sur clic du joueur
  local CI = _G.C_ChatInfo   -- Forever : C_ChatInfo.SendChatMessage (SendChatMessage global = repli déprécié)
  local send = (CI and type(CI.SendChatMessage) == "function" and CI.SendChatMessage) or _G.SendChatMessage
  if type(send) ~= "function" or not name then return end
  self._invited = self._invited or {}
  if self._invited[name] then T:Print(name .. " a déjà été prévenu.") return end
  self._invited[name] = true
  pcall(send, INVITE, "WHISPER", nil, name)
end

function M:HandleEvents(evs, s)
  for _, ev in ipairs(evs) do
    self:Record(ev, s)
    local cat = TOAST_FOR[ev.kind]
    local mine = ev.me and (ev.kind == "done" or ev.kind == "level" or ev.kind == "death" or ev.kind == "alive"
      or ev.kind == "turnin" or ev.kind == "abandon")
    if cat and not mine and (cat == "toasts" or self.db[cat] ~= false) then
      local who = ev.who
      local onClick
      if ev.kind == "noaddon" then onClick = function() M:Invite(who) end end
      self:Toast(ev.kind .. ":" .. tostring(ev.who or "") .. ":" .. tostring(ev.quest or ""), ev.text, ev.prio,
        { onClick = onClick, ttl = ev.kind == "noaddon" and 60 or 15 })
    end
  end
end

-- « Attendez Charlie » au moment de rendre (QUEST_COMPLETE : lecture de GetQuestID, aucun cadre touché)
function M:OnTurnIn(id)
  if not (self.db and self.db.turnInWarn) then return end
  id = tonumber(id)
  if not id then return end
  local e
  for _, x in ipairs(self.board or {}) do if x.id == id then e = x end end
  if not e or #e.need == 0 then return end
  local QM = (ns and ns.Quests) or T.Quests
  local chain = type(QM) == "table" and type(QM.QuestieNext) == "function" and call(QM.QuestieNext, id)
  local who = {}
  for i, n in ipairs(e.need) do
    if i > 3 then break end
    who[#who + 1] = fmt("%s (%d/%d)", n, e.per[n].have, e.per[n].need)
  end
  local text = chain and fmt("Attendez %s : %s ouvre une suite, rendez ensemble", tconcat(who, ", "), q(e.title))
    or fmt("Attendez %s avant de rendre %s", tconcat(who, ", "), q(e.title))
  self:Suggest("turnin", text, chain and 3 or 2, { ttl = 20, cooldown = 30, tab = "group" })
  return text
end

-- ------------------------------------------------------------------ 41 « Prêt ? » (vérif. maison, sans DoReadyCheck)
-- MSR { v = 1, k = "ask"|"ans", id, r = 1|0, w = raison } ; états : ? attente, prêt, pas prêt, AFK, hors ligne, sans TreeAI
local READY_TIMEOUT = 30
function M.SelfReadiness(st)
  if not st then return true, nil end
  if (st.d or 0) > 0 then return false, "mort" end
  if (st.h or 100) < 50 then return false, fmt("PV %d %%", st.h) end
  if (st.p or -1) >= 0 and st.p < 40 then return false, fmt("mana %d %%", st.p) end
  if (st.pt or -1) == 2 then return false, "familier absent" end
  if (st.rd or 1) == 0 then return false, "pas prêt" end
  return true, nil
end

function E.ReadyState(rc, members, rosterInfo)
  -- -> { {name, state = "ok"|"no"|"wait"|"afk"|"off"|"noaddon", why}, … }, tous prêts ?, en attente
  local out, all, waiting = {}, true, {}
  local seen = {}
  for _, m in ipairs(sorted(members)) do
    seen[m.name] = true
    local a = rc and rc.answers and rc.answers[m.name]
    local e = { name = m.name }
    if (m.st.af or 0) == 1 then e.state = "afk"
    elseif a then e.state = a.r and "ok" or "no"; e.why = a.w
    else e.state = "wait" end
    if e.state ~= "ok" then all = false end
    if e.state == "wait" or e.state == "afk" then waiting[#waiting + 1] = m.name end
    out[#out + 1] = e
  end
  local names = {}
  for n in pairs(rosterInfo or {}) do if not seen[n] then names[#names + 1] = n end end
  tsort(names)
  for _, n in ipairs(names) do
    local r = rosterInfo[n]
    out[#out + 1] = { name = n, state = r.online == false and "off" or "noaddon" }
    all = false
  end
  return out, all, waiting
end

local READY_WORD = { ok = "|cff33ff66prêt|r", no = "|cffff5544pas prêt|r", wait = "|cffffd133?|r", afk = "|cffff9933AFK|r",
  off = "|cff888888hors ligne|r", noaddon = "|cff888888sans TreeAI|r" }
M.READY_WORD = READY_WORD

function M:ReadyAsk()
  if not inGroup() then T:Print("Pas de groupe.") return end
  local id = (self._rcSeq or 0) + 1
  self._rcSeq = id
  local me = myName()
  self.readyCheck = { id = id, by = me, at = now(), answers = {} }
  local ok, why = M.SelfReadiness(self.list and (function() for _, m in ipairs(self.list) do if m.name == me then return m.st end end end)())
  self.readyCheck.answers[me] = { r = ok, w = why }
  pend.ready = { v = PROTO, k = "ask", id = id }
  self._dirty = true
  T:Print("« Prêt ? » envoyé au groupe.")
end

function M:ReadyAnswer(yes, why)
  local rc = self.readyCheck
  if not rc or now() - rc.at > READY_TIMEOUT then T:Print("Aucune vérification « prêt ? » en cours.") return end
  rc.answers[myName()] = { r = yes and true or false, w = why }
  pend.readyAns = { v = PROTO, k = "ans", id = rc.id, r = yes and 1 or 0, w = why }
  self:Clear("ready")
  self._dirty = true
end

function M:OnReadyMsg(p, name)
  if type(p) ~= "table" or tonumber(p.v) ~= PROTO then return end
  local id = int(p.id, 1, 1000000, nil)
  if not id then return end
  if p.k == "ask" then
    self.readyCheck = { id = id, by = name, at = now(), answers = { [name] = { r = true } } }
    local me
    for _, m in ipairs(self.list or {}) do if m.name == myName() then me = m.st end end
    local ok, why = M.SelfReadiness(me)
    self:Suggest("ready", fmt("%s demande : prêt ?%s  [clic = prêt]", name, ok and "" or (" (toi : " .. why .. ")")), 3,
      { ttl = READY_TIMEOUT, cooldown = 0, tab = "group", onClick = function() M:ReadyAnswer(true) end })
  elseif p.k == "ans" then
    local rc = self.readyCheck
    if rc and rc.id == id then rc.answers[name] = { r = tonumber(p.r) == 1, w = clean(p.w, 20) } end
  end
end

function M:ReadyTick()
  local rc = self.readyCheck
  if not rc or rc.done then return end
  local states, all, waiting = E.ReadyState(rc, self.list, self._rost)
  if all then
    rc.done = true
    self:Toast("readyok", "Tout le monde est prêt : go !", 2, { ttl = 10 })
  elseif now() - rc.at > READY_TIMEOUT then
    rc.done = true
    local no = {}
    for _, e in ipairs(states) do
      if e.state ~= "ok" then no[#no + 1] = e.name .. (e.why and (" (" .. e.why .. ")") or (e.state == "wait" and " (pas de réponse)" or "")) end
    end
    self:Toast("readyno", "Pas prêts : " .. tconcat(no, ", "), 2, { ttl = 15 })
  end
  self._dirty = true
end

-- ------------------------------------------------------------------ 42 butin du groupe (CHAT_MSG_LOOT)
local function fmtToPattern(f)
  if type(f) ~= "string" then return nil end
  f = f:gsub("([%(%)%.%%%+%-%*%?%[%]%^%$])", "%%%1")
  f = f:gsub("%%%%s", "(.+)"):gsub("%%%%d", "(%%d+)"):gsub("%%%%%d%%%$s", "(.+)")
  return "^" .. f .. "$"
end
M.FmtToPattern = fmtToPattern
local LOOT_FR = { other = "^(.+) reçoit le butin : (.+)%.$", self = "^Vous recevez le butin : (.+)%.$" }

-- -> qui (nil = moi), lien/nom, quantité
function M.ParseLoot(msg)
  if type(msg) ~= "string" then return nil end
  local pats = {}
  local sm, s1 = fmtToPattern(LOOT_ITEM_SELF_MULTIPLE), fmtToPattern(LOOT_ITEM_SELF)
  local om, o1 = fmtToPattern(LOOT_ITEM_MULTIPLE), fmtToPattern(LOOT_ITEM)
  if sm then pats[#pats + 1] = { sm, "self", true } end
  if s1 then pats[#pats + 1] = { s1, "self" } end
  if om then pats[#pats + 1] = { om, "other", true } end
  if o1 then pats[#pats + 1] = { o1, "other" } end
  pats[#pats + 1] = { LOOT_FR.self, "self" }
  pats[#pats + 1] = { LOOT_FR.other, "other" }
  for _, p in ipairs(pats) do
    local a, b, c = msg:match(p[1])
    if a then
      if p[2] == "self" then return nil, a, tonumber(b) or 1 end
      return shortName(a), b, tonumber(c) or 1
    end
  end
  return false
end

local QUALITY_BY_COLOR = { ["9d9d9d"] = 0, ffffff = 1, ["1eff00"] = 2, ["0070dd"] = 3, a335ee = 4, ff8000 = 5 }
function M.ItemFromLink(link)
  if type(link) ~= "string" then return nil end
  local color = link:match("|c%x%x(%x%x%x%x%x%x)")
  local name = link:match("%[(.-)%]") or clean(link, 40)
  local q = tonumber(link:match("|cnIQ(%d):")) or (color and QUALITY_BY_COLOR[color:lower()])
  if q == nil and T.API and T.API.ItemInfo then q = select(3, call(T.API.ItemInfo, link)) end
  return clean(name, 40), tonumber(q) or 1
end

function M:OnLoot(msg)
  local who, link, n = M.ParseLoot(msg)
  if who == false or not link then return end
  who = who or myName()
  if who ~= myName() and not roster[who] then return end
  local name, qual = M.ItemFromLink(link)
  local s = self:Session()
  if not s or not name then return end
  s.loot = type(s.loot) == "table" and s.loot or {}
  local L = s.loot[who] or { n = 0, q2 = 0, q3 = 0, q4 = 0 }
  L.n = L.n + (n or 1)
  if qual >= 2 then
    L["q" .. mmin(qual, 4)] = (L["q" .. mmin(qual, 4)] or 0) + 1
    if not L.bestQ or qual > L.bestQ then L.best, L.bestQ = name, qual end
    if qual >= 3 and who ~= myName() then self:Toast("loot:" .. who, fmt("%s a obtenu %s", who, name), 1, { ttl = 10 }) end
  end
  s.loot[who] = L
  self._dirty = true
end

function E.LootLines(loot)
  local names = {}
  for n in pairs(loot or {}) do names[#names + 1] = n end
  tsort(names, function(a, b)
    local la, lb = loot[a], loot[b]
    local sa, sb = (la.q4 or 0) * 100 + (la.q3 or 0) * 10 + (la.q2 or 0), (lb.q4 or 0) * 100 + (lb.q3 or 0) * 10 + (lb.q2 or 0)
    if sa ~= sb then return sa > sb end
    return a < b
  end)
  local out = {}
  for _, n in ipairs(names) do
    local L = loot[n]
    local bits = {}
    if (L.q4 or 0) > 0 then bits[#bits + 1] = L.q4 .. " épique" .. (L.q4 > 1 and "s" or "") end
    if (L.q3 or 0) > 0 then bits[#bits + 1] = L.q3 .. " bleu" .. (L.q3 > 1 and "s" or "") end
    if (L.q2 or 0) > 0 then bits[#bits + 1] = L.q2 .. " vert" .. (L.q2 > 1 and "s" or "") end
    out[#out + 1] = fmt("%s : %d objets%s%s", n, L.n, #bits > 0 and (", " .. tconcat(bits, ", ")) or "",
      L.best and (" (meilleur : " .. L.best .. ")") or "")
  end
  return out
end

-- ------------------------------------------------------------------ 43 où est chacun (distance + direction)
local DIRS = { "devant", "devant-gauche", "à gauche", "derrière-gauche", "derrière", "derrière-droite", "à droite", "devant-droite" }
local CARD = { "N", "NO", "O", "SO", "S", "SE", "E", "NE" }
local ARROW = { "^", "<^", "<", "<v", "v", "v>", ">", "^>" }
-- UnitPosition : 1er = Y (ouest +), 2e = X (nord +) ; GetPlayerFacing : 0 = nord, sens anti-horaire.
function E.Direction(me, other, facing)
  local my, mx, mi = pos(me)
  local oy, ox, oi = pos(other)
  if not my or not oy or mi ~= oi then return nil end
  local dWest, dNorth = oy - my, ox - mx
  local d = sqrt(dWest * dWest + dNorth * dNorth)
  if d < 5 then return { d = d, word = "à côté", arrow = "o" } end
  local ang = math.atan2(dWest, dNorth)  -- anti-horaire depuis le nord
  local rel = facing and (ang - facing) or ang
  local twoPi = 2 * math.pi
  rel = rel % twoPi
  local idx = floor((rel + math.pi / 8) / (math.pi / 4)) % 8 + 1
  if facing then return { d = d, word = DIRS[idx], arrow = ARROW[idx] } end
  return { d = d, word = "au " .. CARD[idx], arrow = ARROW[idx] }
end

function M:WhereLines()
  local out = {}
  local me
  for _, m in ipairs(self.list or {}) do if m.name == myName() then me = m end end
  local facing = tonumber(call(GetPlayerFacing))
  for _, m in ipairs(self.list or {}) do
    if m.name ~= myName() then
      local dir = me and E.Direction(me, m, facing)
      local where
      if (m.st.d or 0) == 2 then where = "fantôme"
      elseif dir then where = fmt("%s %d m %s", dir.arrow, floor(dir.d * 0.9144 + 0.5), dir.word)
      else where = m.st.z and ("à " .. m.st.z) or "?" end
      out[#out + 1] = { name = m.name, text = fmt("%s : %s", m.name, where), far = dir and dir.d > FAR_YARDS or (not dir) }
    end
  end
  for name, r in pairs(self._rost or {}) do
    local isAddon = false
    for _, m in ipairs(self.list or {}) do if m.name == name then isAddon = true end end
    if not isAddon then
      local d = roster[name] and T.API and T.API.Distance and call(T.API.Distance, roster[name])
      out[#out + 1] = { name = name, text = fmt("%s : %s", name, r.online == false and "hors ligne"
        or (d and fmt("~%d m (sans TreeAI)", floor(d * 0.9144 + 0.5)) or "sans TreeAI")) }
    end
  end
  tsort(out, function(a, b) return a.name < b.name end)
  return out
end

-- ------------------------------------------------------------------ 44 marquage conseillé (un clic = une marque)
local MARK_NAME = { [8] = "crâne", [7] = "croix", [5] = "lune", [6] = "carré" }
function M.SuggestMark(info)
  -- info = { elite, rare, level, myLevel, mark, caster } -> icône conseillée
  if not info then return nil end
  if info.elite or info.rare or (info.level and info.myLevel and info.level >= info.myLevel + 2) then return 8 end
  if info.caster then return 5 end
  return 7
end
function M:TargetInfo()
  if not (call(UnitExists, "target") and call(UnitCanAttack, "player", "target")) or call(UnitIsDead, "target")
    or call(UnitIsPlayer, "target") then return nil end
  local cls = call(UnitClassification, "target")
  local lvl = tonumber(call(UnitLevel, "target"))
  return { elite = cls == "elite" or cls == "rareelite" or cls == "worldboss", rare = cls == "rare" or cls == "rareelite",
           level = (lvl and lvl > 0) and lvl or 99, myLevel = tonumber(call(UnitLevel, "player")) or 1,
           mark = tonumber(call(GetRaidTargetIndex, "target")), caster = (call(UnitPowerType, "target") == 0),
           name = clean(call(UnitName, "target"), 30) }
end
function M:MarkTarget()
  if T.InCombat and T:InCombat() then T:Print("Marquage : hors combat seulement.") return false end
  local info = self:TargetInfo()
  if not info then T:Print("Cible un monstre d'abord.") return false end
  local icon = M.SuggestMark(info)
  if info.mark == icon then return false end
  if type(SetRaidTarget) ~= "function" then return false end
  pcall(SetRaidTarget, "target", icon)  -- une seule marque, sur le clic du joueur
  self._dirty = true
  return true
end

-- ------------------------------------------------------------------ 45 classement XP de la session
function E.Leaderboard(members, startLv)
  local out = {}
  for _, m in ipairs(sorted(members)) do
    local s0 = startLv and startLv[m.name]
    local gained = s0 and mmax(0, floor(totalXP(m.st.l, m.st.x) - totalXP(s0.l, s0.x))) or 0
    out[#out + 1] = { name = m.name, xp = gained, xph = m.st.xh or 0, level = m.st.l }
  end
  tsort(out, function(a, b) if a.xp ~= b.xp then return a.xp > b.xp end return a.name < b.name end)
  return out
end
local function kfmt(n)
  n = tonumber(n) or 0
  if n >= 1000 then return (fmt("%.1fk", n / 1000):gsub("%.0k", "k")) end
  return tostring(floor(n))
end
M.KFmt = kfmt

-- ------------------------------------------------------------------ 46 plan de voyage du groupe
-- dest = { zone = nom } ; -> { dest, steps = { {name, text} } }
function E.TravelPlan(members, dest, opts)
  opts = opts or {}
  local list = sorted(members)
  if #list < 2 then return nil end
  local zone = dest and dest.zone
  if not zone then
    local counts, best, bn = {}, nil, 0
    for _, m in ipairs(list) do if m.st.z then counts[m.st.z] = (counts[m.st.z] or 0) + 1 end end
    for z, n in pairs(counts) do if n > bn or (n == bn and best and z < best) then best, bn = z, n end end
    zone = best
  end
  if not zone then return nil end
  local plan = { dest = zone, steps = {} }
  local zl = low(zone)
  for _, m in ipairs(list) do
    local st = m.st
    if st.z and st.z ~= zone then
      local text
      local bindHere = st.hb and (low(st.hb) == zl or zl:find(low(st.hb), 1, true) or low(st.hb):find(zl, 1, true))
      if (st.d or 0) > 0 then text = fmt("%s (%s) : d'abord ressusciter", m.name, st.z)
      elseif bindHere and st.hs == 0 then text = fmt("%s (%s) : pierre de foyer prête vers %s", m.name, st.z, st.hb)
      elseif bindHere and (st.hs or -1) > 0 then text = fmt("%s (%s) : pierre dans %s, sinon route", m.name, st.z, duration(st.hs))
      else text = fmt("%s (%s) : en route vers %s (%s)", m.name, st.z, zone, st.hb and ("pierre : " .. st.hb) or "pas de pierre") end
      plan.steps[#plan.steps + 1] = { name = m.name, text = text }
    end
    if m.leaveMin then plan.steps[#plan.steps + 1] = { name = m.name, text = fmt("%s part dans %d min", m.name, m.leaveMin) } end
  end
  plan.text = #plan.steps == 0 and fmt("Tout le monde est à %s", zone) or fmt("Rendez-vous : %s", zone)
  return plan
end

-- ------------------------------------------------------------------ 47 qui peut fabriquer quoi pour qui
-- st.pr = "Couture 120 Premiers soins 95" (Squad) ; recettes repères Classic (rangs approximatifs)
local CRAFTS = {
  { key = { "premiers soins", "first aid", "secourisme" }, tiers = { { 1, "bandages en lin" }, { 80, "bandages en laine" },
    { 150, "bandages en soie" }, { 210, "bandages en tisse-mage" }, { 260, "bandages en étoffe runique" } }, need = "all" },
  { key = { "couture", "tailoring" }, tiers = { { 45, "sacs en lin (6)" }, { 80, "sacs en laine (8)" }, { 150, "sacs en soie (10)" },
    { 225, "sacs en tisse-mage (12)" } }, need = "bags" },
  { key = { "alchimie", "alchemy" }, tiers = { { 1, "potions de soins mineures" }, { 55, "potions de soins inférieures" },
    { 110, "potions de soins" }, { 155, "potions de soins supérieures" } }, need = "all" },
  { key = { "cuisine", "cooking" }, tiers = { { 1, "nourriture" }, { 75, "nourriture + bonus d'endurance" } }, need = "food" },
  { key = { "ingénierie", "ingenierie", "engineering" }, tiers = { { 1, "balles légères" }, { 75, "balles lourdes" },
    { 125, "balles solides" } }, need = "ammo" },
  { key = { "forge", "blacksmithing" }, tiers = { { 1, "pierres à aiguiser" }, { 65, "pierres à aiguiser grossières" },
    { 125, "pierres à aiguiser solides" } }, need = "melee" },
  { key = { "travail du cuir", "leatherworking" }, tiers = { { 1, "kits d'armure légers" }, { 30, "carquois / gibernes" } }, need = "quiver" },
  { key = { "enchantement", "enchanting" }, tiers = { { 1, "enchantements de bracelets" }, { 100, "enchantements de plastron" } }, need = "all" },
}
function M.ParseProfessions(s)
  local out = {}
  if type(s) ~= "string" then return out end
  for name, rank in s:gmatch("([^%d]+)%s(%d+)") do
    name = name:gsub("^%s+", ""):gsub("%s+$", "")
    if name ~= "" then out[#out + 1] = { name = name, rank = tonumber(rank) or 0 } end
  end
  return out
end
local MELEE = { WARRIOR = true, ROGUE = true, PALADIN = true, SHAMAN = true, HUNTER = true }
local function needs(kind, st)
  if kind == "all" then return true end
  if kind == "bags" then return (st.bt or 0) > 0 and (st.bt or 0) < 60 or (st.b or 99) <= 2 end
  if kind == "food" then return (st.fo or 0) < 5 end
  if kind == "ammo" then return st.c == "HUNTER" and (st.am or -1) >= 0 and st.am < 1000 end
  if kind == "melee" then return MELEE[st.c] or false end
  if kind == "quiver" then return st.c == "HUNTER" end
  return false
end
function E.Crafters(members)
  local out = {}
  local list = sorted(members)
  for _, m in ipairs(list) do
    for _, p in ipairs(M.ParseProfessions(m.st.pr)) do
      local pl = low(p.name)
      for _, c in ipairs(CRAFTS) do
        local match = false
        for _, k in ipairs(c.key) do if pl:find(k, 1, true) then match = true end end
        if match then
          local best
          for _, t in ipairs(c.tiers) do if p.rank >= t[1] then best = t[2] end end
          if best then
            local forWho = {}
            for _, o in ipairs(list) do if needs(c.need, o.st) and (c.need == "all" or o.name ~= m.name) then forWho[#forWho + 1] = o.name end end
            out[#out + 1] = { by = m.name, prof = p.name, rank = p.rank, item = best, targeted = c.need ~= "all" and #forWho > 0,
              forWho = forWho, text = fmt("%s (%s %d) : %s%s", m.name, p.name, p.rank, best,
                (#forWho > 0 and c.need ~= "all") and (" pour " .. tconcat(forWho, ", ")) or "") }
          end
        end
      end
    end
  end
  tsort(out, function(a, b)
    if a.targeted ~= b.targeted then return a.targeted end
    if a.by ~= b.by then return a.by < b.by end
    return a.prof < b.prof
  end)
  return out
end

-- ------------------------------------------------------------------ rendu (onglet « Groupe »)
local BAR = "Interface\\Buttons\\WHITE8X8"
function M.Bar(pct, w)
  w = w or 40
  pct = mmax(0, mmin(100, tonumber(pct) or 0))
  local fill = floor(w * pct / 100 + 0.5)
  local s = ""
  if fill > 0 then s = fmt("|T%s:8:%d:0:0:8:8:0:8:0:8:64:200:96|t", BAR, fill) end
  if fill < w then s = s .. fmt("|T%s:8:%d:0:0:8:8:0:8:0:8:70:70:70|t", BAR, w - fill) end
  return s
end
local function asciiBar(pct, w)
  w = w or 10
  local fill = floor(w * mmax(0, mmin(100, pct or 0)) / 100 + 0.5)
  return "[" .. string.rep("=", fill) .. string.rep("-", w - fill) .. "]"
end
M.AsciiBar = asciiBar

function M:BuildLines()
  local lines = {}
  local board, nx = self.board or {}, self.next
  local s = self.char and self.char.session
  local multi = 0
  for _, e in ipairs(board) do if e.multi then multi = multi + 1 end end
  -- règle d'affichage : pas de ligne de compteur (les chiffres vont dans l'infobulle du bouton « Prêt ? ») ;
  -- les actions sans contexte sont des boutons de l'onglet (control), les lignes commencent par un verbe
  local counts = fmt("Missions : %d en commun · %d au total", multi, #board)
  if type(s) == "table" and s.goal then
    local gs = E.Goal(s.goal, self.list, s, epoch())
    if gs then
      lines[#lines + 1] = { text = M.Bar(gs.pct, 30) .. " " .. gs.text, prio = gs.done and 1 or 2,
        tooltip = "Objectif de session (/tai mission objectif …)" }
    end
  end
  if nx then
    local e = nx.mission
    lines[#lines + 1] = { text = "|cffffd133" .. nx.text .. "|r", prio = 2,
      tooltip = e.poi and "Clic : cible de groupe (le chef la partage, boussole)" or "Pas de point sur la carte",
      onClick = function() M:SetGroupTarget(e) end }
  end
  local rc = self.readyCheck
  if rc and now() - rc.at <= READY_TIMEOUT + 15 then
    local states = E.ReadyState(rc, self.list, self._rost)
    local bits = {}
    for _, e in ipairs(states) do bits[#bits + 1] = e.name .. " " .. READY_WORD[e.state] end
    lines[#lines + 1] = { text = "Prêt ? " .. tconcat(bits, " · "), prio = rc.done and 1 or 2,
      tooltip = "Clic : je suis prêt", onClick = function() M:ReadyAnswer(true) end }
  else
    lines[#lines + 1] = { text = "Demande au groupe s'il est prêt", label = "Prêt ?", control = true, prio = 1,
      tooltip = { "Clic : vérification « prêt ? » (PV, mana, familier, AFK)", counts }, onClick = function() M:ReadyAsk() end }
  end
  local ti = self:TargetInfo()
  if ti and not (T.InCombat and T:InCombat()) then
    local icon = M.SuggestMark(ti)
    if ti.mark ~= icon then
      lines[#lines + 1] = { text = fmt("Pose la marque %s sur %s (clic)", MARK_NAME[icon] or "conseillée", ti.name or "la cible"),
        prio = 1, tooltip = "Clic : pose la marque (hors combat, une par clic)", onClick = function() M:MarkTarget() end }
    end
  end
  local shown = 0
  for _, e in ipairs(board) do
    if shown >= 6 then break end
    if e.multi or shown < 3 then
      shown = shown + 1
      local head = e.allDone and (e.multi and "|cff33ff66Prêts à rendre|r " or "|cff33ff66À rendre|r ") or ""
      local claim = #e.claims > 0 and fmt(" |cff66ccff(%s)|r", e.claims[1].by) or ""
      local tip = { e.title }
      for _, n in ipairs(e.holders) do
        local p = e.per[n]
        tip[#tip + 1] = fmt("%s %s %d/%d", n, asciiBar(floor(p.have * 100 / p.need)), p.have, p.need)
      end
      if #e.missing > 0 then tip[#tip + 1] = "Sans la quête : " .. tconcat(e.missing, ", ") end
      tip[#tip + 1] = "Clic : je m'en charge / je lâche"
      local mission = e
      lines[#lines + 1] = {
        text = fmt("%s %s%s %d/%d · %d %%%s", M.Bar(e.pct, 36), head, e.title, #e.done, #e.holders, e.pct, claim),
        prio = e.allDone and 2 or 1, tooltip = tconcat(tip, "\n"), onClick = function() M:Claim(mission, 0) end,
      }
      if not e.allDone and e.multi then lines[#lines + 1] = { text = "   |cffbbbbbb" .. E.StatusLine(e) .. "|r", prio = 1 } end
    end
  end
  for i, a in ipairs(self.assign or {}) do
    if i > 3 then break end
    lines[#lines + 1] = { text = "|cffd9d9d9• " .. a.text .. "|r", prio = 1 }
  end
  local where = self:WhereLines()
  if #where > 0 then
    local t = {}
    for _, w in ipairs(where) do t[#t + 1] = w.text end
    lines[#lines + 1] = { text = "|cffbbbbbbOù : " .. tconcat(t, " · ") .. "|r", prio = 1, tooltip = t }
  end
  local sess = self.char and self.char.session
  if type(sess) == "table" then
    local lb = E.Leaderboard(self.list, sess.startLv)
    local t = {}
    for i, e in ipairs(lb) do if i <= 4 then t[#t + 1] = fmt("%s +%s", e.name, kfmt(e.xp)) end end
    if #t > 1 then lines[#lines + 1] = { text = "|cffbbbbbbXP session : " .. tconcat(t, ", ") .. "|r", prio = 1 } end
    local loot = E.LootLines(sess.loot)
    if #loot > 0 then lines[#lines + 1] = { text = "|cffbbbbbbButin : " .. loot[1] .. "|r", prio = 1, tooltip = loot } end
  end
  local plan = self.plan
  if plan and #plan.steps > 0 then
    local t = { plan.text }
    for _, st in ipairs(plan.steps) do t[#t + 1] = st.text end
    lines[#lines + 1] = { text = "|cffffd133Voyage : " .. plan.text .. "|r (" .. #plan.steps .. " étape" .. (#plan.steps > 1 and "s" or "") .. ")",
      prio = 1, tooltip = t }
  end
  for i, c in ipairs(self.crafts or {}) do
    if i > 2 or not c.targeted then break end
    lines[#lines + 1] = { text = "|cffbbbbbbArtisan : " .. c.text .. "|r", prio = 1 }
  end
  for _, n in ipairs(self.noAddon or {}) do
    local who = n
    lines[#lines + 1] = { text = fmt("|cffff9933%s n'a pas TreeAI|r |cff66ccff[prévenir]|r", n), prio = 2,
      tooltip = "Clic : lui envoyer un message privé pour installer TreeAI Coach", onClick = function() M:Invite(who) end }
  end
  if #board == 0 then lines[#lines + 1] = { text = "Aucune quête partagée pour l'instant.", prio = 1 } end
  return lines
end

function M:Render(force)
  if not (self.db and self.db.tab) then return end
  local t = now()
  if not force and t - (self._lastRender or -1e9) < 0.5 then return end
  local lines = self:BuildLines()
  local sig = {}
  for i, l in ipairs(lines) do sig[i] = l.text end
  sig = tconcat(sig, "\n")
  if sig == self._lastSig and not force then return end
  self._lastSig, self._lastRender = sig, t
  self.lines = lines
  if type(T.SetTabLines) == "function" then
    if not self._tab and type(T.RegisterTab) == "function" then self._tab = true; pcall(T.RegisterTab, T, "group", "Groupe", 30) end
    pcall(T.SetTabLines, T, "group", lines, "missions")
  else  -- repli : les 2 lignes clés dans la liste principale
    local nx = self.next
    if nx then self:Suggest("next", nx.text, nx.kind == "turnin" and 2 or 1, { ttl = 30, cooldown = 300, tab = "group" })
    else self:Clear("next") end
  end
end

-- ------------------------------------------------------------------ boucle
function M:MemberList()
  local S = Squad()
  if not S then return {} end
  return S._list or S:MemberList()
end

function M:Tick()
  local S = Squad()
  local t = now()
  if not S or not inGroup() then
    if self.board and #self.board > 0 then
      self.board, self.assign, self.next, self.list = {}, {}, nil, {}
      if type(T.SetTabLines) == "function" then pcall(T.SetTabLines, T, "group", {}, "missions") end
    end
    prevSnap = nil
    return
  end
  if infoDirty or t - lastScan > 10 then
    infoDirty, lastScan = false, t
    local ok, list = pcall(self.ScanInfo, self)
    if ok and type(list) == "table" then
      local enc = M.EncodeInfo(list)
      if enc ~= myInfoEnc then pend.info = true; self._dirty = true end
      myInfoEnc = enc
      info[myName()] = M.DecodeInfo(enc)
    end
  end
  if t - lastInfoSent >= INFO_EVERY then
    pend.info = true
    if #(claims[myName()] or {}) > 0 then pend.claims = true end
  end
  self:Pump(t)
  -- prises expirées / quête rendue
  for n, list in pairs(claims) do
    for i = #list, 1, -1 do if t - (list[i].at or t) > CLAIM_TTL then table.remove(list, i) end end
    if n ~= myName() and not roster[n] then claims[n] = nil end
  end
  for n in pairs(info) do if n ~= myName() and not roster[n] then info[n] = nil end end
  local list = self:MemberList()
  self.list = list
  local me = myName()
  self.board = E.Board(list, { info = info, claims = claims, me = me })
  self.assign = E.Assign(self.board, list, {})
  self.next = E.Next(self.board)
  local dest
  if self.next and self.next.mission.poi and C_Map and C_Map.GetMapInfo then
    local mi = call(C_Map.GetMapInfo, self.next.mission.poi.m)
    if type(mi) == "table" and mi.name then dest = { zone = mi.name } end
  end
  self.plan = E.TravelPlan(list, dest)
  self.crafts = E.Crafters(list)
  -- membres sans TreeAI / hors ligne
  local has = {}
  for _, m in ipairs(list) do has[m.name] = true end
  local rost, noAddon = {}, {}
  for name, u in pairs(roster) do
    local r = { online = call(UnitIsConnected, u) ~= false and true or false, level = call(UnitLevel, u),
                dead = call(UnitIsDeadOrGhost, u) and true or false, afk = call(UnitIsAFK, u) and true or false }
    if not has[name] and r.online and t - (joined[name] or t) >= GRACE_NOADDON then
      r.noAddon = true
      noAddon[#noAddon + 1] = name
    end
    rost[name] = r
  end
  tsort(noAddon)
  self.noAddon = noAddon
  self._rost = rost
  self:ReadyTick()
  local s = self:Session()
  if s then
    s.last = epoch()
    startLevels(s, list)
  end
  local snap = E.Snapshot(list, rost, { me = me })
  if prevSnap then self:HandleEvents(E.Diff(prevSnap, snap), s) end
  prevSnap = snap
  self:Render(self._dirty)
  self._dirty = false
end

-- ------------------------------------------------------------------ récap / rapport
function M:Recap(print)
  local s = self:Session()
  if not s then return {} end
  local out = {}
  local function add(x) out[#out + 1] = x end
  add(fmt("Récap de session (%s) :", duration(epoch() - (s.start or epoch()))))
  add(fmt("%d quête%s rendue%s%s, %d mission%s terminée%s ensemble, %d abandon%s.", s.turned, s.turned > 1 and "s" or "",
    s.turned > 1 and "s" or "", s.turned > 0 and (" (" .. tconcat(s.titles, ", ", 1, mmin(#s.titles, 5)) .. (#s.titles > 5 and ", …" or "") .. ")") or "",
    s.missions, s.missions > 1 and "s" or "", s.missions > 1 and "s" or "", s.abandoned, s.abandoned > 1 and "s" or ""))
  local lv = {}
  for _, m in ipairs(self.list or {}) do
    local s0 = s.startLv[m.name]
    if s0 and m.st.l > s0.l then lv[#lv + 1] = fmt("%s %d→%d", m.name, s0.l, m.st.l)
    elseif s0 then lv[#lv + 1] = fmt("%s %d (+%d %%)", m.name, m.st.l, mmax(0, m.st.x - s0.x)) end
  end
  if #lv > 0 then add("Niveaux : " .. tconcat(lv, ", ")) end
  local bm = {}
  for n, c in pairs(s.byMember) do bm[#bm + 1] = fmt("%s %d", n, c) end
  tsort(bm)
  if #bm > 0 then add("Rendus par joueur : " .. tconcat(bm, ", ")) end
  local dd, nd = {}, 0
  for n, c in pairs(s.deaths) do dd[#dd + 1] = fmt("%s %d", n, c); nd = nd + c end
  tsort(dd)
  add(nd > 0 and ("Morts : " .. tconcat(dd, ", ")) or "Aucune mort.")
  local lb = E.Leaderboard(self.list, s.startLv)
  if #lb > 1 then
    local t = {}
    for i, e in ipairs(lb) do t[#t + 1] = fmt("%d. %s +%s XP", i, e.name, kfmt(e.xp)) end
    add("Classement XP : " .. tconcat(t, ", "))
  end
  for i, l in ipairs(E.LootLines(s.loot)) do if i <= 4 then add("Butin " .. l) end end
  if s.goal then
    local gs = E.Goal(s.goal, self.list, s, epoch())
    if gs then add(gs.text) end
  end
  if print ~= false then for _, l in ipairs(out) do T:Print(l) end end
  return out
end

function M:Report()
  local board = self.board or {}
  if not inGroup() or #(self.list or {}) < 2 then
    T:Print("Missions : il faut un groupe avec des amis qui ont TreeAI.")
    for _, n in ipairs(self.noAddon or {}) do T:Print(n .. " n'a pas TreeAI (installe TreeAI Coach).") end
    return
  end
  if self.next then T:Print("> " .. self.next.text) end
  for i, e in ipairs(board) do
    if i > 8 then break end
    T:Print(fmt("%d. %s %s %d/%d prêts, %d %%%s", i, asciiBar(e.pct), e.title, #e.done, #e.holders, e.pct,
      #e.missing > 0 and (" — sans : " .. tconcat(e.missing, ", ")) or ""))
    if not e.allDone then T:Print("   " .. E.StatusLine(e)) end
  end
  for i, a in ipairs(self.assign or {}) do
    if i > 4 then break end
    T:Print("• " .. a.text)
  end
  local s = self.char and self.char.session
  if type(s) == "table" and s.goal then
    local gs = E.Goal(s.goal, self.list, s, epoch())
    if gs then T:Print(gs.text) end
  end
  for _, n in ipairs(self.noAddon or {}) do T:Print(n .. " n'a pas TreeAI (installe TreeAI Coach).") end
end

function M:SlashMission(rest)
  rest = tostring(rest or "")
  local cmd, arg = rest:match("^(%S*)%s*(.-)$")
  cmd = low(cmd)
  if cmd == "" or cmd == "tableau" then self:Report()
  elseif cmd == "recap" or cmd == "récap" then self:Recap()
  elseif cmd == "cible" then self:SetGroupTarget(arg ~= "" and self:FindMission(arg) or nil)
  elseif cmd == "objectif" or cmd == "but" then self:SlashGoal(arg)
  elseif cmd == "prends" then self:SlashClaim(arg)
  elseif cmd == "start" or cmd == "debut" or cmd == "début" then
    if self.char then self.char.session = newSession(); startLevels(self.char.session, self.list) end
    T:Print("Nouvelle session de groupe : bonne soirée !")
  elseif cmd == "pret" or cmd == "prêt" then
    local a = low(arg):match("^(%S*)")
    if a == "oui" or a == "on" then self:ReadyAnswer(true)
    elseif a == "non" or a == "off" then self:ReadyAnswer(false, clean(arg:match("^%S+%s+(.+)$"), 20))
    else self:ReadyAsk() end
  elseif cmd == "ou" or cmd == "où" then
    for _, w in ipairs(self:WhereLines()) do T:Print(w.text) end
  elseif cmd == "marque" then self:MarkTarget()
  elseif cmd == "xp" then
    local s = self:Session()
    for i, e in ipairs(E.Leaderboard(self.list, s and s.startLv)) do
      T:Print(fmt("%d. %s +%s XP (niv. %d, %s/h)", i, e.name, kfmt(e.xp), e.level, kfmt(e.xph)))
    end
  elseif cmd == "voyage" then
    local plan = self.plan
    if not plan then T:Print("Pas de plan (il faut un groupe).") return end
    T:Print(plan.text)
    for _, st in ipairs(plan.steps) do T:Print("• " .. st.text) end
  elseif cmd == "metiers" or cmd == "métiers" then
    local c = self.crafts or {}
    if #c == 0 then T:Print("Aucun métier partagé (il faut TreeAI chez les amis).") end
    for _, x in ipairs(c) do T:Print("• " .. x.text) end
  elseif cmd == "butin" then
    local s = self:Session()
    local l = E.LootLines(s and s.loot)
    if #l == 0 then T:Print("Pas encore de butin noté cette session.") end
    for _, x in ipairs(l) do T:Print("• " .. x) end
  elseif cmd == "fin" or cmd == "stop" then
    self:Recap()
    if self.char then self.char.session = false end
  else
    T:Print("/tai mission [recap | cible [quête] | objectif niveau 30|quetes 15|<quête>|off | prends <quête> | pret [oui|non] | ou | marque | xp | voyage | metiers | butin | start | fin]")
  end
end

-- ------------------------------------------------------------------ résumé pour TreeAI Coach
function M:SaveSummary()
  if not T.Summary then return end
  local ok, sm = pcall(T.Summary, T)
  if not ok or type(sm) ~= "table" then return end
  local s = self.char and self.char.session
  local list = self.list or {}
  if #list < 2 and type(s) ~= "table" then return end
  local out = { v = PROTO, updated = epoch(), size = #list, members = {}, board = {}, assign = {}, claims = {}, noAddon = #(self.noAddon or {}) }
  for _, m in ipairs(list) do
    out.members[#out.members + 1] = { name = m.name, class = (T.Squad and T.Squad.CLASS_FR and T.Squad.CLASS_FR[m.st.c]) or m.st.c, level = m.st.l }
  end
  for i, e in ipairs(self.board or {}) do
    if i > 10 then break end
    out.board[#out.board + 1] = { id = e.id, title = e.title, holders = #e.holders, done = #e.done, pct = e.pct,
      ready = e.allDone or nil, status = (not e.allDone) and E.StatusLine(e) or nil, missing = #e.missing }
    for _, c in ipairs(e.claims) do out.claims[#out.claims + 1] = { quest = e.title, by = c.by } end
  end
  for i, a in ipairs(self.assign or {}) do
    if i > 6 then break end
    out.assign[#out.assign + 1] = a.text
  end
  if self.next then out.next = self.next.text end
  if self.plan and #self.plan.steps > 0 then
    out.travel = { self.plan.text }
    for i, st in ipairs(self.plan.steps) do if i <= 4 then out.travel[#out.travel + 1] = st.text end end
  end
  out.crafts = {}
  for i, c in ipairs(self.crafts or {}) do if i <= 6 then out.crafts[i] = c.text end end
  if type(s) == "table" then
    local ses = { start = s.start, duration = epoch() - (s.start or epoch()), turned = s.turned, missions = s.missions,
                  abandoned = s.abandoned, titles = {}, deaths = 0, levels = {}, events = {} }
    for i, x in ipairs(s.titles or {}) do if i <= 10 then ses.titles[i] = x end end
    for _, c in pairs(s.deaths or {}) do ses.deaths = ses.deaths + c end
    for _, m in ipairs(list) do
      local s0 = s.startLv[m.name]
      if s0 then ses.levels[#ses.levels + 1] = { name = m.name, from = s0.l, to = m.st.l } end
    end
    for i, ev in ipairs(s.events or {}) do if i <= 8 then ses.events[i] = ev.text end end
    if s.goal then
      local gs = E.Goal(s.goal, list, s, epoch())
      if gs then ses.goal = { kind = gs.kind, text = gs.text, pct = gs.pct, eta = gs.eta and floor(gs.eta) or nil, done = gs.done or nil } end
    end
    local lb = E.Leaderboard(list, s.startLv)
    ses.leaderboard = {}
    for i, e in ipairs(lb) do if i <= 5 then ses.leaderboard[i] = { name = e.name, xp = e.xp, xph = e.xph } end end
    ses.loot = {}
    for i, l in ipairs(E.LootLines(s.loot)) do if i <= 5 then ses.loot[i] = l end end
    out.session = ses
  end
  sm.missions = out
end

-- ------------------------------------------------------------------ cycle de vie
function M:OnInit()
  if T.RegisterSlash then
    T:RegisterSlash("mission", function(rest) M:SlashMission(rest) end,
      "mission [recap|cible|objectif …|start|fin] : missions du groupe, répartition, objectif du soir")
    T:RegisterSlash("prends", function(rest) M:SlashClaim(rest) end, "prends <quête> [obj] : je m'en charge (prévient le groupe)")
  end
end

function M:OnEnable()
  if T.RegisterComm then
    for _, kind in ipairs({ "MSM", "MSC", "MST", "MSG", "MSR" }) do
      T:RegisterComm(kind, M, function(_, payload, sender) M:OnComm(kind, payload, sender) end)
    end
  end
  self:RegisterEvent("GROUP_ROSTER_UPDATE", function()
    M:RefreshRoster()
    pend.info, pend.claims = true, true
    if M.target and M:IsLeader(nil) then
      local tg = M.target
      pend.target = { v = PROTO, q = tg.q, m = tg.m, x = tg.x, y = tg.y, l = tg.l }
    end
    M._dirty = true
  end)
  self:RegisterEvent("QUEST_LOG_UPDATE", function() infoDirty = true end)
  self:RegisterEvent("QUEST_COMPLETE", function() M:OnTurnIn(call(GetQuestID)) end)
  self:RegisterEvent("CHAT_MSG_LOOT", function(_, _, msg) M:OnLoot(msg) end)
  self:RegisterEvent("PLAYER_TARGET_CHANGED", function() M._dirty = true end)
  self:RegisterEvent("PLAYER_LOGOUT", function() M:SaveSummary() end)
  self:On("SUMMARY", function() M:SaveSummary() end)
  self:RefreshRoster()
  if T.NewTicker and not self._ticker then
    self._ticker = T:NewTicker(1, function() if M:IsEnabled() then M:Tick() end end)
  end
end

function M:OnDisable()
  if self._ticker and self._ticker.Cancel then self._ticker:Cancel() end
  self._ticker = nil
  if type(T.SetTabLines) == "function" then pcall(T.SetTabLines, T, "group", {}, "missions") end
end
