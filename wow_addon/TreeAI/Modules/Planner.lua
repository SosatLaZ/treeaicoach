-- TreeAI — module Planner (agent PLAN-LUA). Suit le trajet de quêtes calculé par TreeAI Coach.
--  * Plan : global `TreeAIPlan` de l'addon de données séparé `TreeAI_Plan` (écrit par l'appli, chargé au
--    /reload ; contrat treeaicoach/wow/planner/SCHEMA.md §3). Sans plan : plan glouton fait ici (étape faisable
--    la plus rentable / la plus proche, modèle de coût research/4) à partir de l'export (Modules/Export.lua).
--  * Sélecteur de routes (XP/h, minutes, verdict + raison), carte d'étape + 2 étapes suivantes, badge de verdict
--    (OK / « On va galérer » / « Trop long » / « Pas faisable » / « Changez de zone »).
--  * Progression lue dans C_QuestLog (jamais d'infobulle) ; réparation locale (insertion + Or-opt sur les
--    20 prochaines étapes) dans une coroutine ≤ 2 ms/image, HORS COMBAT seulement ; hystérésis 10 % et 2 min.
--  * Groupe : id du plan + étape + empreinte synchronisés (message « PLN », court, ≤ 1/15 s, jamais en combat),
--    point de rendez-vous, « attendez X » avant de rendre.
--  * Navigation sur CLIC seulement : TreeAI.Compass → Shortest Path Forever → TomTom → repère Blizzard
--    (C_Map.SetUserWaypoint + C_SuperTrack). Jamais de sélection dans le journal de quêtes Blizzard, aucun
--    panneau Blizzard ouvert, rien d'automatique (ToS).
-- Résumé : TreeAIDB.chars[perso].summary.plan. Lua 5.1, aucune globale (lit TreeAIPlan avec rawget).
local T = _G.TreeAI
if not (T and T.RegisterModule) then return end

local P = T:RegisterModule("Planner", {
  title = "Trajet",
  defaults = { enabled = true, toasts = true, sync = true, auto = true, repair = true },
  charDefaults = { route = nil, turned = {}, skip = {}, repaired = nil },
  options = {
    { key = "auto", type = "toggle", label = "Plan automatique quand TreeAI Coach n'en a pas fourni" },
    { key = "repair", type = "toggle", label = "Réparer le trajet quand je prends / abandonne une quête" },
    { key = "sync", type = "toggle", label = "Synchroniser l'étape avec le groupe" },
    { key = "toasts", type = "toggle", label = "Messages « itinéraire mis à jour »" },
  },
})
T.Planner = P

local floor, max, min, sqrt, abs, huge = math.floor, math.max, math.min, math.sqrt, math.abs, math.huge
local tinsert, tremove, tsort, tconcat, fmt = table.insert, table.remove, table.sort, table.concat, string.format
local unpack = unpack or table.unpack

-- (LITE) sans table par appel (pcall direct) ; toujours au moins une valeur
local function tryPass(ok, a, ...) if ok then return a, ... end return nil end
local function try(fn, ...)
  if type(fn) ~= "function" then return nil end
  return tryPass(pcall(fn, ...))
end
local function G(name) return rawget(_G, name) end
local function C(ns, fn) local t = G(ns) return type(t) == "table" and t[fn] or nil end
local function now() return try(G("GetTime")) or 0 end
local function epoch() return try(G("time")) or floor(now()) end
local function clock() return try(G("debugprofilestop")) or now() * 1000 end
local function inCombat() return T.InCombat and T:InCombat() or false end
local function cut(s, n) s = tostring(s or "") if #s > n then return s:sub(1, n - 1) .. "…" end return s end
local function cfg(k) return not (P.db and P.db[k] == false) end
local function myLevel() return tonumber(try(G("UnitLevel"), "player")) or 1 end
local function myName() return try(G("UnitName"), "player") end
local function short(name) return tostring(name or ""):match("^([^%-]+)") or tostring(name) end
local function thousands(n)
  local s = tostring(floor((n or 0) + 0.5)):reverse():gsub("(%d%d%d)", "%1 "):reverse()
  return (s:gsub("^%s+", ""))
end

-- ===================================================================== moteur pur (testable)
local E = {}
P.Engine = E

-- kill XP (Classic formulas, research/4 §1)
function E.ZD(pl)
  if pl < 8 then return 5 elseif pl < 10 then return 6 elseif pl < 12 then return 7 elseif pl < 16 then return 8
  elseif pl < 20 then return 9 elseif pl < 30 then return 11 elseif pl < 40 then return 12 elseif pl < 45 then return 13
  elseif pl < 50 then return 14 elseif pl < 55 then return 15 elseif pl < 60 then return 16 end
  return 17
end
function E.GreyLevel(pl)
  if pl <= 5 then return 0 elseif pl <= 39 then return pl - 5 - floor(pl / 10) elseif pl <= 59 then return pl - 1 - floor(pl / 5) end
  return 51
end
function E.KillXP(pl, ml, elite)
  local base = 5 * pl + 45
  local xp
  if ml >= pl then xp = base * (1 + 0.05 * min(ml - pl, 4))
  elseif ml <= E.GreyLevel(pl) then return 0
  else xp = base * (1 - (pl - ml) / E.ZD(pl)) end
  if elite then xp = xp * 2.5 end
  return floor(xp + 0.5)
end
E.GROUP_RATE = { 1, 1, 1.166, 1.3, 1.4 }
function E.GroupShare(n) n = max(1, min(5, floor(n or 1))) return E.GROUP_RATE[n] / n end
function E.QuestXPFactor(pl, ql)
  local d = pl - ql
  if d <= 5 then return 1 elseif d == 6 then return 0.8 elseif d == 7 then return 0.6 elseif d == 8 then return 0.4
  elseif d == 9 then return 0.2 end
  return 0.1
end
function E.QuestXPEstimate(ql) return 55 * ql + ql * ql end
-- relative power vs level difference Δ = player − mob
function E.LF(d)
  if d >= -2 then return 1.10 ^ d elseif d == -3 then return 0.62 elseif d == -4 then return 0.50 elseif d == -5 then return 0.38 end
  return 0.25
end
-- class priors: same-level TTK (s), downtime share, solo capacity in normal-mob units
E.CLASS = {
  HUNTER = { 16, 0.15, 2.6 }, WARLOCK = { 20, 0.12, 2.5 }, PALADIN = { 27, 0.12, 2.2 }, DRUID = { 20, 0.20, 2.2 },
  SHAMAN = { 20, 0.25, 2.1 }, WARRIOR = { 21, 0.37, 2.0 }, PRIEST = { 22, 0.27, 1.9 }, ROGUE = { 17, 0.30, 1.8 },
  MAGE = { 12, 0.32, 1.8 },
}
local TANK = { WARRIOR = true, DRUID = true, PALADIN = true, HUNTER = true }
local HEAL = { PRIEST = true, DRUID = true, SHAMAN = true, PALADIN = true }
local function classOf(c) return E.CLASS[c or ""] or { 20, 0.2, 2.0 } end
-- feasibility F = Σ cap_i · syn / Req ; members = {{class=, level=, g=?}}, pull = {level=, units=}
function E.Feasibility(members, pull)
  local cap, tank, heal = 0, false, false
  for _, m in ipairs(members) do
    cap = cap + classOf(m.class)[3] * (m.g or 1) * E.LF((m.level or 1) - (pull.level or 1))
    tank = tank or TANK[m.class or ""] or false
    heal = heal or HEAL[m.class or ""] or false
  end
  local syn = 1
  if #members > 1 and tank and heal then syn = 1.25 elseif (pull.units or 1) >= 4 and not heal and #members > 1 then syn = 0.85 end
  local F = cap * syn / max(1, pull.units or 1)
  local label = F >= 1.3 and "facile" or F >= 1.0 and "faisable" or F >= 0.8 and "galere" or F >= 0.6 and "risque" or "impossible"
  return F, label
end
-- one kill cycle (s): TTK·k_lvl + search·κ + downtime ; ctx = {class, level, ttk=fn(Δ)?, search=, down=, comp=}
function E.KillCycle(ctx, ml)
  local cl = classOf(ctx.class)
  local delta = (ml or ctx.level) - ctx.level
  local ttk = (ctx.ttk and ctx.ttk(delta)) or cl[1] / E.LF(-delta)
  local down = ctx.down or cl[2]
  local search = (ctx.search or 10) * (ctx.comp or 1)
  return ttk + search + down * ttk / (1 - down)
end
-- minutes (median, p80) for one objective record {k, n, have, p, plf, ml, mx, rank}
function E.ObjMinutes(o, ctx)
  local need = max(0, (tonumber(o.n) or 1) - (tonumber(o.have) or 0))
  if need == 0 then return 0, 0 end
  local k = max(1, ctx.group or 1)
  local ml = tonumber(o.mx) or tonumber(o.ml) or ctx.level
  local c = E.KillCycle(ctx, ml)
  if (o.rank or 0) == 1 or (o.rank or 0) == 2 then c = c * 3 end
  local sec, p80
  if o.k == "kill" then
    local kill = c - (ctx.search or 10) * (ctx.comp or 1)
    sec = need * (kill / (k == 1 and 1 or 0.8 * k) + (ctx.search or 10) * (ctx.comp or 1))
    p80 = sec * 1.25
  elseif o.k == "item" then
    local p = tonumber(o.p) or 0.45
    p = max(0.02, min(1, p))
    local corpses = (o.plf == 1 or k == 1) and need / p or k * need / p
    local per = c / (k == 1 and 1 or 0.8 * k) + (k > 1 and 0 or 0)
    sec = corpses * per
    local sd = sqrt(need * (1 - p)) / p
    p80 = (corpses + 0.84 * sd * (k > 1 and k or 1)) * per
  elseif o.k == "obj" then
    sec = need * 28 * (k > 1 and k / min(k, 2) or 1)
    p80 = sec * 1.3
  else
    sec, p80 = 30, 45
  end
  local eff = ctx.eff or 1
  return sec * eff / 60, p80 * eff / 60
end
-- galère score 0–100 + main reason for one quest record (export shape)
function E.Galere(q, ctx)
  local k = max(1, ctx.group or 1)
  local pl = ctx.level
  local terms = {}
  local function add(v, why) if v > 0 then terms[#terms + 1] = { v, why } end end
  local d = (q.lv or pl) - pl
  if d >= 5 then add(25, "beaucoup trop haute pour vous") elseif d >= 3 then add(15, "un peu haute pour vous") end
  local worstF = huge
  for _, o in ipairs(q.o or {}) do
    local ml = tonumber(o.mx) or tonumber(o.ml)
    if ml and ml > 0 then
      local units = ((o.rank == 1 or o.rank == 2) and 4) or (o.rank == 3 and 8) or 1
      local F = E.Feasibility(ctx.members or { { class = ctx.class, level = pl } }, { level = ml, units = units })
      if F < worstF then worstF = F end
      if units >= 4 and k < max(2, q.grp or 0) then add(20, "des élites : à faire à " .. max(2, q.grp or 3)) end
      if units == 1 and ml >= pl + 3 then add(18, "monstres plus forts que vous") end
    end
    if o.k == "item" then
      local need = max(0, (o.n or 1) - (o.have or 0))
      local p = max(0.02, tonumber(o.p) or 0.45)
      local corpses = (o.plf == 1 or k == 1) and need / p or k * need / p
      if corpses > 60 and p < 0.3 then
        add(50, k >= 2 and "l'objet tombe très rarement : très long" or "l'objet tombe très rarement : faites-la à 2")
      elseif need > 0 then
        add(12 * min(2, sqrt(1 - p) / sqrt(max(1, need)) * 3), "l'objet tombe rarement")
      end
    end
  end
  if worstF < huge then add(30 * max(0, 1 - worstF), "trop dur pour vous") end
  if (ctx.comp or 1) > 1 then add(10 * ((ctx.comp or 1) - 1), "monde sur le camp") end
  if q.esc == 1 or q.timed == 1 then add(10, q.esc == 1 and "escorte / événement" or "quête chronométrée") end
  local G2, best = 0, nil
  for _, t in ipairs(terms) do G2 = G2 + t[1]; if not best or t[1] > best[1] then best = t end end
  return min(100, floor(G2 + 0.5)), best and best[2] or nil, worstF
end
-- per-quest verdict: "ok" | "galere" | "impossible" (+ reason)
function E.QuestVerdict(q, ctx)
  local k = max(1, ctx.group or 1)
  if ctx.raid then return "impossible", "vous êtes en raid : les quêtes normales n'avancent pas" end
  if (q.lv or 0) >= ctx.level + 5 and k == 1 then return "impossible", "beaucoup trop haute pour vous" end
  if q.dg == 1 and k < 3 then return "impossible", "donjon : au moins 3 joueurs" end
  local g, why, F = E.Galere(q, ctx)
  if F and F < 0.6 then return "impossible", "trop dur pour vous" end
  if g >= 45 then return "galere", why end
  return "ok", nil
end
E.LABEL = { ok = "OK", galere = "On va galérer", long = "Trop long", impossible = "Pas faisable", zone = "Changez de zone" }
E.PRIO = { ok = 1, galere = 2, long = 2, impossible = 3, zone = 2 }
-- grind baseline XP/h (same-level mobs, solo)
function E.GrindXPH(ctx) return 3600 * E.KillXP(ctx.level, ctx.level) / E.KillCycle(ctx, ctx.level) end
-- route verdict for a plan this addon built itself; plan = {xph, minutes, steps}, info = {nonGrey, zoneMedian, galere={why}}
function E.RouteVerdict(plan, ctx, info)
  info = info or {}
  if ctx.raid then return "impossible", "vous êtes en raid : les quêtes normales n'avancent pas" end
  if #(plan.steps or {}) == 0 then return "impossible", "aucune étape faisable ici" end
  if (info.nonGrey or 99) < 3 then return "zone", "presque plus rien d'utile ici" end
  if info.zoneMedian and ctx.level >= info.zoneMedian + 5 then return "zone", "zone trop facile pour vous" end
  if ctx.session and (plan.minutes or 0) > ctx.session then return "long", "plus long que ta session de jeu" end
  local base = E.GrindXPH(ctx)
  if (plan.xph or 0) > 0 and plan.xph < 0.6 * base then return "long", "tuer des monstres rapporte plus" end
  if info.galere then return "galere", info.galere end
  return "ok", nil
end
-- deterministic hash of a step list (k, q, o) → 6 hex digits, no bit ops
-- (LITE) mémorisé par liste (même table, même longueur, même 1re/dernière étape) : recalculé seulement si elle change
local hashMemo = setmetatable({}, { __mode = "k" })
function E.Hash(steps)
  local m = type(steps) == "table" and hashMemo[steps]
  if m and m.n == #steps and m.a == steps[1] and m.z == steps[#steps] then return m.h end
  local h = 5381
  for _, s in ipairs(steps or {}) do
    local str = tostring(s.k or "") .. "," .. tostring(s.q or "") .. "," .. tostring(s.o or "") .. ";"
    for i = 1, #str do h = (h * 33 + str:byte(i)) % 16777216 end
  end
  local r = fmt("%06x", h)
  if type(steps) == "table" then hashMemo[steps] = { n = #steps, a = steps[1], z = steps[#steps], h = r } end
  return r
end
-- precedence: within one quest pick < obj < turn
local RANK = { pick = 1, obj = 2, turn = 3 }
function E.ValidOrder(steps, i0, i1)
  local seen = {}
  for i = i0, i1 do
    local s = steps[i]
    if s and s.q and RANK[s.k] then
      local r = seen[s.q]
      if r and r > RANK[s.k] then return false end
      seen[s.q] = max(r or 0, RANK[s.k])
    end
  end
  return true
end
local FIXED = { fly = true, hs = true, meet = true, train = true, vendor = true }
-- travel cost (s) of steps[i0..i1] from `start` with leg(a, b)
function E.PathCost(steps, i0, i1, start, leg)
  local c, prev = 0, start
  for i = i0, i1 do
    local s = steps[i]
    if s then c = c + leg(prev, s); prev = s end
  end
  return c
end
-- cheapest insertion of a quest's new steps (ordered list) into steps[i0..i0+w]
function E.Insert(steps, new, i0, w, start, leg, yieldFn)
  local pos = i0
  for _, ns in ipairs(new) do
    local last = min(#steps, i0 + w)
    local best, bestAt = huge, last + 1
    for j = pos, last + 1 do
      local a = (j == i0) and start or steps[j - 1] or start
      local b = steps[j]
      local delta = leg(a, ns) + (b and (leg(ns, b) - leg(a, b)) or 0)
      if delta < best then best, bestAt = delta, j end
      if yieldFn then yieldFn() end
    end
    tinsert(steps, bestAt, ns)
    pos = bestAt + 1
  end
  return steps
end
-- Or-opt-1 over the window: move single steps while the window cost drops; returns gain (s)
function E.OrOpt(steps, i0, w, start, leg, yieldFn, maxPasses)
  local i1 = min(#steps, i0 + w - 1)
  local before = E.PathCost(steps, i0, i1, start, leg)
  for _ = 1, maxPasses or 2 do
    local improved = false
    for i = i0, i1 do
      local s = steps[i]
      if s and not FIXED[s.k] and not s.who then
        local cur = E.PathCost(steps, i0, i1, start, leg)
        local bestC, bestJ = cur, nil
        for j = i0, i1 do
          if j ~= i then
            local x = tremove(steps, i)
            tinsert(steps, j, x)
            if E.ValidOrder(steps, i0, i1) then
              local c = E.PathCost(steps, i0, i1, start, leg)
              if c < bestC - 1 then bestC, bestJ = c, j end
            end
            x = tremove(steps, j)
            tinsert(steps, i, x)
            if yieldFn then yieldFn() end
          end
        end
        if bestJ then
          local x = tremove(steps, i)
          tinsert(steps, bestJ, x)
          improved = true
        end
      end
    end
    if not improved then break end
  end
  return before - E.PathCost(steps, i0, i1, start, leg), before
end
-- greedy plan: requests = {{q=, steps={...ordered, each {k, svc, value, ...}}, inLog=bool}}, best value/time next
function E.Greedy(requests, start, leg, opt)
  opt = opt or {}
  local cap, logCount, horizon = opt.cap or 18, opt.logCount or 0, opt.horizon or 20
  local out, pos, t, xp = {}, start, 0, 0
  local nextIdx = {}
  for _ = 1, horizon do
    local best, bestR, bestScore = nil, nil, -huge
    for ri, r in ipairs(requests) do
      local i = nextIdx[ri] or 1
      local s = r.steps[i]
      if s and (s.k ~= "pick" or logCount < cap) then
        if opt.yield then opt.yield() end
        local dt = leg(pos, s) + (s.svc or 20)
        local score = (s.value or 0) / max(10, dt)
        if score > bestScore or (score == bestScore and best and (r.q or 0) < (requests[bestR].q or 0)) then
          best, bestR, bestScore = s, ri, score
        end
      end
    end
    if not best then break end
    nextIdx[bestR] = (nextIdx[bestR] or 1) + 1
    t = t + leg(pos, best) + (best.svc or 20)
    xp = xp + (best.value or 0)
    if best.k == "pick" then logCount = logCount + 1 elseif best.k == "turn" then logCount = logCount - 1 end
    best.eta = floor(t / 60 + 0.5)
    out[#out + 1] = best
    if best.x then pos = best end
  end
  local minutes = max(1, floor(t / 60 + 0.5))
  return out, minutes, (t > 0) and floor(xp * 3600 / t) or 0
end

-- ===================================================================== état du plan
local Geo = T.Geo or {}
local function X() return T.Export end
local function playerPos() if Geo.Player then return Geo.Player() end end
local function costCtx()
  local Xm = X()
  local n = try(G("GetNumGroupMembers")) or 0
  local ctx = { class = T.PlayerClass and T:PlayerClass() or nil, level = myLevel(), group = max(1, n),
    raid = try(G("IsInRaid")) and true or false }
  if Xm then
    ctx.ttk = function(d) return Xm:TTK(d) end
    ctx.search, ctx.down, ctx.eff = Xm:Search(), Xm:Downtime(), Xm:Eff()
    local map = try(C("C_Map", "GetBestMapForUnit"), "player")
    ctx.comp = map and Xm:Comp(map) or nil
  end
  local SQ = T.Squad
  local lm = SQ and SQ.char and tonumber(SQ.char.leaveAt) or 0
  if lm > 0 then ctx.session = max(0, floor((lm - epoch()) / 60)) end
  -- members (me + squad) for group feasibility
  ctx.members = { { class = ctx.class, level = ctx.level } }
  if SQ and type(SQ.Members) == "function" and try(SQ.IsActive, SQ) then
    local me = myName()
    for _, name in ipairs(try(SQ.Members, SQ) or {}) do
      if name ~= me then
        local st = try(SQ.State, SQ, name)
        if type(st) == "table" and st.l then ctx.members[#ctx.members + 1] = { class = st.c, level = st.l } end
      end
    end
  end
  return ctx
end
P.CostCtx = costCtx

local function runSpeed()
  local _, s = try(G("GetUnitSpeed"), "player")
  s = tonumber(s)
  return (s and s > 0) and s or 7
end
-- travel seconds between two positioned things
local function leg(a, b)
  if not (a and b) or not (a.x and b.x) then return 45 end
  local d = Geo.Dist and Geo.Dist(a.map, a.x, a.y, b.map, b.x, b.y)
  if not d then return 240 end
  local det = X() and X():Detour(b.map) or 1.3
  return d * det / runSpeed()
end
P.Leg = leg

local function logSnap()
  if X() and X().LogSnapshot then return X().LogSnapshot() end
  return {}
end
local function questDone(qid)
  if P.char and P.char.turned and P.char.turned[qid] then return true end
  local f = C("C_QuestLog", "IsQuestFlaggedCompleted") or G("IsQuestFlaggedCompleted")
  return f and try(f, qid) and true or false
end

-- the file plan (validated) ------------------------------------------------------------------
function P:FilePlan()
  local plan = G("TreeAIPlan")
  if type(plan) ~= "table" then return nil, nil end
  if plan.v ~= 1 or type(plan.routes) ~= "table" or #plan.routes == 0 then return nil, "Plan TreeAI illisible (version)" end
  local key = T.CharKey and T:CharKey()
  if plan.char and key and key ~= "?" and plan.char ~= key then return nil, "Plan TreeAI ignoré : fait pour un autre personnage" end
  return plan, nil
end
local function inGroup() return (try(G("GetNumGroupMembers")) or 0) > 1 or try(G("IsInGroup")) and true or false end

function P:Routes()
  local plan = self:FilePlan()
  if plan then return plan.routes, "file", plan end
  if self.auto then return { self.auto }, "auto", nil end
  return {}, nil, nil
end
function P:SelectedRoute()
  local routes, src, plan = self:Routes()
  if #routes == 0 then return nil end
  local want = self.char and self.char.route
  for _, r in ipairs(routes) do if r.id == want then return r, src, plan end end
  for _, r in ipairs(routes) do if r.id ~= "squad" or inGroup() then return r, src, plan end end
  return routes[1], src, plan
end
function P:SelectRoute(id)
  if self.char then self.char.route = id end
  self.steps, self.key = nil, nil
  self:Refresh(true)
end

-- copy the route's steps (or the locally repaired order for this plan)
local function planKey(plan, route) return tostring(plan and plan.made or "auto") .. ":" .. tostring(route and route.id or "?") end
local function copySteps(list)
  local out = {}
  for i, s in ipairs(list or {}) do
    local c = {}
    for k, v in pairs(s) do c[k] = v end
    out[i] = c
  end
  return out
end
function P:EnsureSteps()
  local route, src, plan = self:SelectedRoute()
  if not route then self.steps, self.key, self.route = nil, nil, nil return nil end
  local key = planKey(plan, route)
  if self.key ~= key or not self.steps then
    self.key, self.route, self.src, self.plan = key, route, src, plan
    local rep = self.char and self.char.repaired
    if src == "file" and type(rep) == "table" and rep.key == key and type(rep.steps) == "table" then
      self.steps = copySteps(rep.steps)
    else
      self.steps = copySteps(route.steps)
    end
    self.cursor = nil
  end
  return self.steps
end

-- step done? (quest steps from C_QuestLog; others by skip / arrival / a later quest step)
local function stepDone(s, log, me)
  if s.who and me and short(s.who) ~= short(me) then return nil end  -- someone else's step
  local q = tonumber(s.q)
  if s.k == "pick" and q then return (log[q] ~= nil) or questDone(q) end
  if s.k == "obj" and q then
    if questDone(q) then return true end
    local lq = log[q]
    if not lq then return false end
    if lq.done == 1 then return true end
    local o = tonumber(s.o)
    if o and lq.obj[o] then return lq.obj[o].fin and true or false end
    return false
  end
  if s.k == "turn" and q then return questDone(q) end
  return false
end
function P:Evaluate()
  local steps = self:EnsureSteps()
  if not steps then return nil end
  local log = logSnap()
  self.log = log
  local me = myName()
  local lastQuestDone = 0
  for i, s in ipairs(steps) do
    local d = stepDone(s, log, me)
    s._other = (d == nil) or nil
    s._done = d and true or false
    if s.q and d then lastQuestDone = i end
    if not s.q and self.char.skip[self.key .. ":" .. i] then s._done = true end
  end
  local cursor = #steps + 1
  for i, s in ipairs(steps) do
    if i < lastQuestDone and not s.q then s._done = true end
    if not s._done and not s._other then cursor = i break end
  end
  if self.cursor and cursor ~= self.cursor then self:OnStepChanged(self.cursor, cursor) end
  self.cursor = cursor
  -- fallback contract for the Tracker: Planner.active = { route = { title, steps }, idx }
  self.active = (cursor <= #steps) and { route = { title = self.route and self.route.title, steps = steps }, idx = cursor } or nil
  return cursor
end

-- measured legs → Export (detour factor, efficiency)
function P:OnStepChanged(old, new)
  local t = now()
  local prevT, prevPos = self._legT, self._legPos
  local s = self.steps and self.steps[old]
  if prevT and s and s.x and prevPos and X() then
    local d = Geo.Dist and Geo.Dist(prevPos.map, prevPos.x, prevPos.y, s.map, s.x, s.y)
    local el = t - prevT
    if d and d > 100 and (s.k == "pick" or s.k == "turn") then X():LearnDetour(s.map, el / (d / runSpeed())) end
    if s.k == "obj" and s._pred and s._pred > 0 then X():LearnEff(el / 60 / s._pred) end
  end
  local map, x, y = playerPos()
  self._legT, self._legPos = t, (map and x) and { map = map, x = x, y = y } or nil
  self._syncDirty = true
  if new > old and self.steps and self.steps[new] then self:Toast(fmt("Étape suivante : %s", self:StepLabel(self.steps[new]))) end
end

-- labels -------------------------------------------------------------------------------------
local function questTitle(q)
  local lq = P.log and P.log[q]
  if lq and lq.t then return lq.t end
  local exp = X() and X():Data()
  for _, r in ipairs(exp and exp.quests or {}) do if r.id == q then return r.t end end
  return "quête " .. tostring(q)
end
local OBJ_VERB = { monster = "Tue", item = "Ramasse", object = "Utilise", event = "Fais", reputation = "Gagne" }
function P:StepLabel(s)
  if s.label and s.label ~= "" then return s.label end
  local q = s.q
  if s.k == "pick" then return "Prendre « " .. cut(questTitle(q), 32) .. " »" end
  if s.k == "turn" then return "Rendre « " .. cut(questTitle(q), 32) .. " »" end
  if s.k == "obj" then
    local lq = self.log and self.log[q]
    local o = lq and lq.obj[tonumber(s.o) or 0]
    if o and o.text then
      -- verbe d'abord, nom propre (sans « : 0/1 », espaces insécables du client FR compris)
      local name = T.ObjectiveName and T.ObjectiveName(o.text) or tostring(o.text)
      local verb = OBJ_VERB[o.type or ""] or "Fais"
      if o.type == "monster" then name = name:gsub("%s+tuée?s?$", "") end
      if name == "" then name = questTitle(q) end
      return verb .. " « " .. cut(name, 34) .. " »"
    end
    return "Objectif de « " .. cut(questTitle(q), 30) .. " »"
  end
  local names = { fly = "Prendre le vol", hs = "Pierre de foyer", train = "Voir le maître", vendor = "Marchand",
    meet = "Regroupement", grind = "Tuer des monstres ici" }
  return names[s.k] or tostring(s.k or "?")
end
local function progress(s, log)
  if s.k ~= "obj" or not s.q then return "" end
  local lq = log and log[s.q]
  local o = lq and lq.obj[tonumber(s.o) or 0]
  if o and (o.n or 0) > 0 then return fmt(" (%d/%d)", o.have or 0, o.n) end
  return ""
end
local DIRS = { "N", "NE", "E", "SE", "S", "SO", "O", "NO" }
local function where(s)
  local map, x, y = playerPos()
  if not (map and x and s.x) then return nil end
  local d = Geo.Dist and Geo.Dist(map, x, y, s.map, s.x, s.y)
  if not d then return nil, nil end
  local dx, dy = s.x - x, y - s.y      -- map y grows southwards
  local ang = math.atan2 and math.atan2(dx, dy) or 0
  local idx = floor(((ang * 180 / math.pi) + 360 + 22.5) % 360 / 45) + 1
  return d, DIRS[idx]
end
P.Where = where
local function stepMinutes(steps, i)
  local s = steps[i]
  if not s then return nil end
  if s.eta then
    local prev = 0
    for j = i - 1, 1, -1 do if steps[j].eta then prev = steps[j].eta break end end
    return max(1, s.eta - prev)
  end
  return s._pred and max(1, floor(s._pred + 0.5)) or nil
end

-- Tracker contract (TRACK-PRO): steps from the current one, TreeAI_Plan format {k,q,o,map,x,y,label,eta,who}
-- (label always filled, with progress), current index, total, route title. Done / other members' steps are
-- skipped. nil when no plan is active.
local PUBLIC = { "k", "q", "o", "map", "x", "y", "label", "eta", "who" }
function P:GetActiveSteps(n)
  n = tonumber(n) or 3
  local steps, cur = self.steps, self.cursor
  if not (steps and cur and self.route) or cur > #steps then return nil end
  local out = {}
  for i = cur, #steps do
    local s = steps[i]
    if not s._done and not s._other then
      local c = {}
      for _, k in ipairs(PUBLIC) do c[k] = s[k] end
      c.label = self:StepLabel(s) .. progress(s, self.log)
      out[#out + 1] = c
      if #out >= n then break end
    end
  end
  if #out == 0 then return nil end
  return out, cur, #steps, self.route.title or self.route.id
end

-- ===================================================================== plan automatique (sans fichier)
local function questRecords()
  local exp = X() and X():Data()
  local recs = {}
  for _, r in ipairs(exp and exp.quests or {}) do recs[r.id] = r end
  return recs, exp
end
local function waypoint(q)
  local wp = C("C_QuestLog", "GetNextWaypoint")
  local m, x, y
  if wp then m, x, y = try(wp, q) end
  if tonumber(m) and tonumber(x) then return { map = m, x = floor(x * 1000 + 0.5) / 10, y = floor(y * 1000 + 0.5) / 10 } end
end
local function place(dst, pos) if pos and pos.x then dst.map, dst.x, dst.y = pos.map, pos.x, pos.y end return dst end
-- requests for one quest (steps still to do), with svc/value from the cost model
function P:QuestSteps(q, rec, lq, ctx)
  local lv = (rec and rec.lv and rec.lv > 0 and rec.lv) or (lq and lq.lv) or ctx.level
  local xp = (rec and rec.xp and rec.xp > 0 and rec.xp) or E.QuestXPEstimate(lv)
  xp = xp * E.QuestXPFactor(ctx.level, lv)
  local out = {}
  if not lq then
    if not (rec and rec.s and rec.s.x) then return out end
    out[#out + 1] = place({ k = "pick", q = q, svc = 15, value = 0.25 * xp }, rec.s)
  end
  local objs = (rec and rec.o) or {}
  local n = 0
  local done = lq and lq.done == 1
  if not done then
    local count = max(1, #objs)
    for j, o in ipairs(objs) do
      local lo = lq and lq.obj[j]
      if not (lo and lo.fin) then
        local oo = { k = o.k, n = (lo and lo.n) or o.n, have = (lo and lo.have) or 0, p = (X() and o.id and X():DropRate(o.id)) or o.p,
          plf = o.plf, ml = o.ml, mx = o.mx, rank = o.rank }
        local med = E.ObjMinutes(oo, ctx)
        local ml = tonumber(o.mx) or tonumber(o.ml) or ctx.level
        local kxp = (o.k == "kill" or o.k == "item") and E.KillXP(ctx.level, ml) * E.GroupShare(ctx.group) * max(0, (oo.n or 1) - oo.have) or 0
        local st = place({ k = "obj", q = q, o = j, svc = med * 60, _pred = med, value = 0.5 * xp / count + kxp }, o)
        if not st.x then place(st, lq and waypoint(q)) end
        out[#out + 1] = st
        n = n + 1
      end
    end
    if #objs == 0 and lq and #lq.obj > 0 then  -- no DB: one step per unfinished log objective at the quest waypoint
      for j, lo in ipairs(lq.obj) do
        if not lo.fin then
          local med = E.ObjMinutes({ k = (lo.type == "item" and "item") or (lo.type == "monster" and "kill") or "other",
            n = lo.n, have = lo.have }, ctx)
          out[#out + 1] = place({ k = "obj", q = q, o = j, svc = med * 60, _pred = med, value = 0.5 * xp / #lq.obj }, waypoint(q))
        end
      end
    end
  end
  local fin = (rec and rec.f and rec.f.x and rec.f) or (lq and (done or #out == 0) and waypoint(q)) or nil
  out[#out + 1] = place({ k = "turn", q = q, svc = 15, value = xp }, fin)
  return out
end
function P:BuildAuto(yieldFn)
  local ctx = costCtx()
  local recs, exp = questRecords()
  local log = logSnap()
  self.log = log
  local requests, info = {}, { nonGrey = 0 }
  local levels, galereWhy = {}, nil
  local grey = E.GreyLevel(ctx.level)
  local map = try(C("C_Map", "GetBestMapForUnit"), "player")
  local function consider(q, rec, lq)
    local v, why = E.QuestVerdict(rec or { lv = lq and lq.lv or ctx.level, o = {} }, ctx)
    local lv = (rec and rec.lv) or (lq and lq.lv) or 0
    if lv > grey and (not rec or not rec.zone or rec.zone == map) then
      info.nonGrey = info.nonGrey + 1
      levels[#levels + 1] = lv
    end
    if v == "impossible" and not lq then return end
    if v == "galere" and not galereWhy then galereWhy = "« " .. cut((rec and rec.t) or (lq and lq.t) or "?", 24) .. " » : " .. tostring(why) end
    local steps = self:QuestSteps(q, rec, lq, ctx)
    if yieldFn then yieldFn() end
    if #steps > 0 then requests[#requests + 1] = { q = q, steps = steps, inLog = lq ~= nil } end
  end
  for q, lq in pairs(log) do consider(q, recs[q], lq) end
  for q, rec in pairs(recs) do
    if not log[q] and rec.st == "avail" and not questDone(q) and (rec.rl or 0) <= ctx.level and not rec.pre and not rec.preAny then
      consider(q, rec, nil)
    end
  end
  tsort(requests, function(a, b) return a.q < b.q end)
  tsort(levels)
  if #levels > 0 then info.zoneMedian = levels[floor((#levels + 1) / 2)] end
  info.galere = galereWhy
  local m, x, y = playerPos()
  local start = (m and x) and { map = m, x = x, y = y } or nil
  local cap = try(C("C_QuestLog", "GetMaxNumQuestsCanAccept")) or 20
  local logCount = 0
  for _ in pairs(log) do logCount = logCount + 1 end
  local steps, minutes, xph = E.Greedy(requests, start, leg, { cap = (tonumber(cap) or 20) - 2, logCount = logCount, horizon = 20, yield = yieldFn })
  local route = { id = "auto", title = "Plan auto (en jeu)", steps = steps, minutes = minutes, xph = xph }
  local v, why = E.RouteVerdict(route, ctx, info)
  route.verdict, route.reasons = v, why and { why } or {}
  self.auto = route
  self.steps, self.key = nil, nil
  return route, exp
end

-- ===================================================================== réparation locale (coroutine)
local driver, co
local function budgetYield()
  P._ops = (P._ops or 0) + 1
  if clock() - (P._frameStart or 0) > 2 or P._ops >= 300 then P._ops = 0; coroutine.yield() end
end
local function resume()
  if not co then if driver then driver:Hide() end return end
  if inCombat() then return end                       -- paused in combat, resumed after
  P._frameStart = clock()
  local ok, err = coroutine.resume(co)
  if not ok then T:Debug("Planner: " .. tostring(err)); co = nil
  elseif coroutine.status(co) == "dead" then co = nil end
  if not co and driver then driver:Hide() end
end
function P:StartJob(fn)
  co = coroutine.create(fn)
  if not driver then
    local CF = G("CreateFrame")
    driver = CF and try(CF, "Frame")
    if driver then driver:SetScript("OnUpdate", resume) end
  end
  if driver then driver:Show() else while co do resume() end end
end
function P:Busy() return co ~= nil end

-- repair: add steps of newly accepted quests, drop abandoned ones, Or-opt the next 20 steps
function P:Repair(force)
  if inCombat() or co then return false end
  local steps = self:EnsureSteps()
  if not steps then return false end
  local log = logSnap()
  self.log = log
  local inPlan, removed = {}, 0
  for i = #steps, 1, -1 do
    local s = steps[i]
    local q = tonumber(s.q)
    if q then
      inPlan[q] = true
      -- abandoned: quest was picked (pick step done earlier) but is neither in the log nor turned in
      if (s.k == "obj" or s.k == "turn") and not log[q] and not questDone(q) and self._seenInLog and self._seenInLog[q] then
        tremove(steps, i); removed = removed + 1
      end
    end
  end
  self._seenInLog = self._seenInLog or {}
  for q in pairs(log) do self._seenInLog[q] = true end
  local recs = questRecords()
  local ctx = costCtx()
  local newQ = {}
  for q, lq in pairs(log) do if not inPlan[q] then newQ[#newQ + 1] = { q = q, lq = lq } end end
  tsort(newQ, function(a, b) return a.q < b.q end)
  local cursor = self:Evaluate() or 1
  if not cfg("repair") and not force then return false end
  if #newQ == 0 and removed == 0 and not force and self._repairCursor == cursor and self._repairKey == self.key then return false end
  self._repairCursor, self._repairKey = cursor, self.key
  local m, x, y = playerPos()
  local start = (m and x) and { map = m, x = x, y = y } or steps[cursor - 1]
  self:StartJob(function()
    for _, n in ipairs(newQ) do
      local st = P:QuestSteps(n.q, recs[n.q], n.lq, ctx)
      local placed = {}
      for _, s in ipairs(st) do if s.k ~= "pick" then placed[#placed + 1] = s end end
      E.Insert(steps, placed, cursor, 20, start, leg, budgetYield)
      budgetYield()
    end
    -- Or-opt on a copy; keep it only if it saves ≥ 10 % and ≥ 2 min (hysteresis)
    local trial = copySteps(steps)
    local gain, before = E.OrOpt(trial, cursor, 20, start, leg, budgetYield, 2)
    if gain >= 120 and gain >= 0.10 * before then
      for i = 1, #trial do steps[i] = trial[i] end
      P:Toast(fmt("Itinéraire mis à jour (−%d min)", floor(gain / 60 + 0.5)))
    end
    if P.src == "file" and P.char then P.char.repaired = { key = P.key, steps = copySteps(steps) } end
    P:Evaluate()
    P:Render()
  end)
  return true
end

-- ===================================================================== groupe : synchro id + étape + empreinte
local peers = {}   -- [short name] = { made, route, step, hash, n, at }
P.peers = peers
function P:SyncPayload()
  if not (self.steps and self.route) then return nil end
  local made = self.plan and self.plan.made or 0
  return fmt("1:%s:%s:%d:%s:%d", tostring(made), (tostring(self.route.id or "?"):gsub("[^%w_]", "")),
    self.cursor or 1, E.Hash(self.steps), #self.steps)
end
function P:SendSync(forceNow)
  if not cfg("sync") or inCombat() or not inGroup() or try(G("IsInRaid")) then return false end
  local t = now()
  if not forceNow and self._lastSync and t - self._lastSync < 15 then return false end
  local payload = self:SyncPayload()
  if not payload then return false end
  if not forceNow and payload == self._lastPayload and self._lastSync and t - self._lastSync < 60 then return false end
  self._lastSync, self._lastPayload, self._syncDirty = t, payload, false
  return T:Send("PLN", payload) and true or false
end
function P:OnSync(payload, sender)
  if type(payload) ~= "string" or #payload > 80 or payload:find("|", 1, true) then return end
  local v, made, rid, step, hash, n = payload:match("^(%d+):(%d+):([%w_]+):(%d+):(%x+):(%d+)$")
  if v ~= "1" then return end
  local name = short(sender)
  if name == "" or name == myName() then return end
  local member = false               -- current party members only (same check as Squad)
  for i = 1, 4 do
    local nm = try(G("UnitExists"), "party" .. i) and try(G("UnitName"), "party" .. i)
    if nm and short(nm) == name then member = true end
  end
  if not member then return end
  peers[name] = { made = tonumber(made), route = rid, step = tonumber(step), hash = hash, n = tonumber(n), at = now() }
  self:Render()
end
-- per mate: "synchro" | "avance" | "retard" | "autre" (+ steps of difference)
function P:PeerStates()
  local out = {}
  local myHash = self.steps and E.Hash(self.steps)
  local made = self.plan and self.plan.made or 0
  for name, p in pairs(peers) do
    if now() - p.at < 180 then
      local st, diff = "autre", 0
      if myHash and p.hash == myHash and p.made == made then
        diff = (p.step or 0) - (self.cursor or 0)
        st = diff == 0 and "synchro" or diff > 0 and "avance" or "retard"
      end
      out[#out + 1] = { name = name, state = st, diff = diff }
    end
  end
  tsort(out, function(a, b) return a.name < b.name end)
  return out
end
-- « attendez X » before a turn-in: squad members who have the quest but not its objectives
function P:WaitFor(s)
  local out = {}
  if not (s and s.k == "turn" and s.q and inGroup()) then return out end
  local SQ = T.Squad
  if SQ and type(SQ.Members) == "function" and try(SQ.IsActive, SQ) then
    local me = myName()
    for _, name in ipairs(try(SQ.Members, SQ) or {}) do
      if name ~= me then
        local qs = try(SQ.Quests, SQ, name)
        local e = type(qs) == "table" and qs[s.q]
        if type(e) == "table" and not e.done then
          local have, need = 0, 0
          for _, o in ipairs(e.obj or {}) do have, need = have + (o.have or 0), need + (o.need or 0) end
          out[#out + 1] = need > 0 and fmt("%s (%d/%d)", short(name), have, need) or short(name)
        end
      end
    end
  end
  for _, p in ipairs(self:PeerStates()) do
    if p.state == "retard" then out[#out + 1] = fmt("%s (%d étape%s derrière)", p.name, -p.diff, p.diff < -1 and "s" or "") end
  end
  tsort(out)
  return out
end
-- meeting point: among the current + next 2 positioned steps, the one minimising the farthest member
function P:MeetingPoint()
  if not inGroup() or not (Geo.World and self.steps) then return nil end
  local SQ = T.Squad
  if not (SQ and type(SQ.Members) == "function" and try(SQ.IsActive, SQ)) then return nil end
  local pos = {}
  local ya, xa = try(G("UnitPosition"), "player")
  if tonumber(ya) then pos[#pos + 1] = { a = ya, b = xa, name = myName() } end
  local me = myName()
  for _, name in ipairs(try(SQ.Members, SQ) or {}) do
    local st = name ~= me and try(SQ.State, SQ, name)
    if type(st) == "table" and tonumber(st.py) and tonumber(st.px) then pos[#pos + 1] = { a = st.py, b = st.px, name = name } end
  end
  if #pos < 2 then return nil end
  local spread = 0
  for i = 1, #pos do for j = i + 1, #pos do spread = max(spread, sqrt((pos[i].a - pos[j].a) ^ 2 + (pos[i].b - pos[j].b) ^ 2)) end end
  local best, bestD, who
  local c = 0
  for i = self.cursor or 1, #self.steps do
    local s = self.steps[i]
    if s.x and not s._other then
      local a, b = Geo.World(s.map, s.x, s.y)
      if a then
        local far, farName = 0, nil
        for _, p in ipairs(pos) do
          local d = sqrt((a - p.a) ^ 2 + (b - p.b) ^ 2)
          if d > far then far, farName = d, p.name end
        end
        if not bestD or far < bestD then best, bestD, who = s, far, farName end
      end
      c = c + 1
      if c >= 3 then break end
    end
  end
  if not best then return nil end
  return best, bestD, who, spread
end

-- ===================================================================== navigation (clic seulement)
local KIND = { pick = "pickup", turn = "turnin", obj = "objective", fly = "flightmaster", hs = "innkeeper",
  train = "trainer", vendor = "innkeeper" }
function P:MarkSPF()  -- la boussole retire cet itinéraire SPF à la mort (pas de ligne flottante sur la fenêtre de mort)
  local Cmp = T.modules and T.modules.Compass
  if type(Cmp) == "table" and type(Cmp.MarkRoute) == "function" then Cmp.MarkRoute("spf") end
end
function P:Navigate(s)
  s = s or (self.steps and self.cursor and self.steps[self.cursor])
  if not (s and s.map and s.x) then T:Print("Pas de position connue pour cette étape.") return false end
  if inCombat() then T:Print("Guidage : hors combat seulement.") return false end
  local label = self:StepLabel(s)
  local x, y = s.x / 100, s.y / 100
  local Cmp = T.Compass
  if type(Cmp) == "table" and type(Cmp.SetTarget) == "function" then
    local ok = try(Cmp.SetTarget, Cmp, { mapID = s.map, x = x, y = y, label = label, source = "plan", questID = s.q })
    if ok ~= false then self.navBy = "compass" return true end
  end
  local spf = G("ShortestPathForever")
  local api = type(spf) == "table" and spf.API
  if type(api) == "table" then
    if type(api.NavigateRoute) == "function" then
      local stops = {}
      for i = self.cursor or 1, #(self.steps or {}) do
        local st = self.steps[i]
        if st.x and not st._other and not st._done then
          stops[#stops + 1] = { map = st.map, x = st.x / 100, y = st.y / 100, title = cut(self:StepLabel(st), 40), kind = KIND[st.k] }
          if #stops >= 5 then break end
        end
      end
      if #stops > 0 and try(api.NavigateRoute, "TreeAI", stops) then self.navBy = "spf" self:MarkSPF() return true end
    elseif type(api.Navigate) == "function" then
      if try(api.Navigate, "TreeAI", s.map, x, y, label, KIND[s.k]) then self.navBy = "spf" self:MarkSPF() return true end
    end
  end
  local tt = G("TomTom")
  if type(tt) == "table" and type(tt.AddWaypoint) == "function" then
    if self.ttUid and type(tt.RemoveWaypoint) == "function" then try(tt.RemoveWaypoint, tt, self.ttUid) end
    self.ttUid = try(tt.AddWaypoint, tt, s.map, x, y, { title = label, persistent = false, from = "TreeAI" })
    if self.ttUid then self.navBy = "tomtom" return true end
  end
  local can = C("C_Map", "CanSetUserWaypointOnMap")
  local set = C("C_Map", "SetUserWaypoint")
  local UMP = G("UiMapPoint")
  if set and type(UMP) == "table" and type(UMP.CreateFromCoordinates) == "function" and (not can or try(can, s.map)) then
    local pt = try(UMP.CreateFromCoordinates, s.map, x, y)
    if pt and try(set, pt) ~= false then
      local st = C("C_SuperTrack", "SetSuperTrackedUserWaypoint")
      if st then try(st, true) end
      self.navBy = "blizzard"
      return true
    end
  end
  T:Print(fmt("Aller à %.1f, %.1f : %s", s.x, s.y, label))
  self.navBy = "chat"
  return false
end

-- ===================================================================== affichage
function P:Toast(text)
  if not cfg("toasts") or inCombat() then return end
  local t = now()
  if self._lastToast and t - self._lastToast < 30 then return end
  self._lastToast = t
  pcall(self.Suggest, self, "toast", text, 1, { ttl = 6, sound = false })
end
local function verdictText(code, reasons)
  local lab = E.LABEL[code or "ok"] or tostring(code or "?")
  local why = type(reasons) == "table" and reasons[1] or nil
  return why and (lab .. " : " .. why) or lab
end
P.VerdictText = verdictText
-- trajets en mots simples (agent SIMPLE) : « Le plus rapide », « Tout finir ici », « Avec mes potes » + le temps
local function routeName(r)
  local Pl = T.Plain
  return Pl and Pl.RouteName(r.id, r.title) or cut(r.title or r.id or "?", 22)
end
local function routeTime(r)
  local Pl = T.Plain
  local t = Pl and tonumber(r.minutes) and Pl.Minutes(r.minutes * 60)
  return t and (" (" .. t .. ")") or ""
end
-- 3 choix au plus : « Avec mes potes » remplace « Sans galère » quand on est en groupe
local function routeChoices(self)
  local all = self:Routes()
  local hasSquad = false
  for _, r in ipairs(all) do if r.id == "squad" then hasSquad = true end end
  local out = {}
  for _, r in ipairs(all) do
    if #out >= 3 then break end
    if not (hasSquad and r.id == "safe") or r == self.route then out[#out + 1] = r end
  end
  return out
end
local function routeChoiceNames(self)
  local out = {}
  for _, r in ipairs(routeChoices(self)) do out[#out + 1] = routeName(r) end
  return out
end
P.RouteName, P.RouteChoices = routeName, routeChoices

function P:Lines()
  local lines = {}
  local route = self.route
  local steps = self.steps
  if self.planError then lines[#lines + 1] = { text = self.planError, prio = 2 } end
  if not (route and steps) then
    lines[#lines + 1] = { text = "Trajet : pas de plan (TreeAI Coach → « Calculer », puis /reload)", prio = 1 }
    return lines
  end
  local old = self.plan and self.plan.made and (epoch() - self.plan.made > 86400) and " · plan ancien" or ""
  local head = fmt("Trajet : %s%s%s%s", routeName(route), routeTime(route),
    (route.verdict and route.verdict ~= "ok") and (" — " .. (E.LABEL[route.verdict] or route.verdict)) or "", old)
  lines[#lines + 1] = { text = head, prio = E.PRIO[route.verdict or "ok"] or 1,
    tooltip = ((route.verdict and route.verdict ~= "ok") and (verdictText(route.verdict, route.reasons) .. "\n") or "")
      .. "Clic : changer de trajet (" .. tconcat(routeChoiceNames(self), " · ") .. ")",
    onClick = function() P:CycleRoute() end }
  local cur = self.cursor or 1
  local log = self.log
  if cur > #steps then
    lines[#lines + 1] = { text = "Trajet terminé ✔ (TreeAI Coach → « Calculer » pour la suite)", prio = 1 }
  else
    local shown = 0
    for i = cur, #steps do
      local s = steps[i]
      if not s._done and not s._other then
        local label = self:StepLabel(s) .. progress(s, log)
        local mins = stepMinutes(steps, i)
        if shown == 0 then
          local d, dir = where(s)
          local Pl = T.Plain
          local w = d and Pl and Pl.Where(d, dir) or nil
          local tm = mins and Pl and Pl.Minutes(mins * 60) or nil
          local txt = label .. (w and (" " .. w) or "") .. (tm and (" (" .. tm .. ")") or "")
          lines[#lines + 1] = { text = txt, prio = 2, pin = true, id = "plan:step",
            tooltip = "Clic : guider (boussole / Shortest Path / TomTom / repère)\n/tai plan passer : étape suivante"
              .. (mins and ("\nDurée estimée : ~" .. mins .. " min") or ""),
            onClick = function() P:Navigate(s) end }
          -- per-quest badge when not OK
          if s.q then
            local recs = questRecords()
            local rec = recs[s.q]
            if rec then
              local v, why = E.QuestVerdict(rec, costCtx())
              if v ~= "ok" then lines[#lines + 1] = { text = verdictText(v, { why }), prio = E.PRIO[v] } end
            end
          end
        else
          lines[#lines + 1] = { text = (shown == 1 and "ensuite : " or "puis : ") .. label, prio = 1,
            onClick = function() P:Navigate(s) end }
        end
        shown = shown + 1
        if shown >= 3 then break end
      end
    end
    -- group
    local wait = self:WaitFor(steps[cur])
    if #wait > 0 then lines[#lines + 1] = { text = "Attendez " .. tconcat(wait, ", ") .. " avant de rendre", prio = 2 } end
    local mp, far, who, spread = self:MeetingPoint()
    if mp and spread and spread > 250 then
      lines[#lines + 1] = { text = fmt("Rendez-vous : %s (le plus loin : %s, %d m)", cut(self:StepLabel(mp), 30), short(who),
        floor(far * 0.9144 + 0.5)), prio = 1, onClick = function() P:Navigate(mp) end }
    end
    local ps = self:PeerStates()
    if #ps > 0 then
      local parts = {}
      local SYM = { synchro = "✔ synchro", avance = "↑ en avance", retard = "↓ en retard", autre = "≠ autre plan" }
      for _, p in ipairs(ps) do
        parts[#parts + 1] = p.name .. " " .. SYM[p.state] .. (p.diff ~= 0 and fmt(" (%+d)", p.diff) or "")
      end
      lines[#lines + 1] = { text = tconcat(parts, " · "), prio = 1 }
    end
  end
  -- route selector
  local routes = routeChoices(self)
  if #routes > 1 then
    for i, r in ipairs(routes) do
      local sel = (r == route) and "●" or "○"
      lines[#lines + 1] = { text = fmt("%s %d. %s%s", sel, i, routeName(r), routeTime(r)), prio = 1,
        tooltip = verdictText(r.verdict, r.reasons), onClick = function() P:SelectRoute(r.id) end }
    end
  end
  return lines
end
function P:CycleRoute()
  local routes = routeChoices(self)
  if #routes < 2 then return end
  local idx = 1
  for i, r in ipairs(routes) do if r == self.route then idx = i end end
  self:SelectRoute(routes[idx % #routes + 1].id)
end
function P:Render()
  if inCombat() then self._renderDirty = true return end
  local lines = self:Lines()
  local sig = {}
  for _, l in ipairs(lines) do sig[#sig + 1] = l.text end
  sig = tconcat(sig, "\n")
  local t = now()
  if sig == self._sig then return end
  if self._lastRender and t - self._lastRender < 0.5 then self._renderDirty = true return end
  self._sig, self._lastRender, self._renderDirty = sig, t, false
  self.lines = lines
  if type(T.SetTabLines) == "function" then
    pcall(T.SetTabLines, T, "quests", lines, "plan")
  else
    local card
    for _, l in ipairs(lines) do if l.id == "plan:step" then card = l end end
    if card then pcall(self.Suggest, self, "step", card.text, 1, { ttl = 0, onClick = card.onClick, sound = false })
    else pcall(self.Clear, self, "step") end
    local r = self.route
    if r and r.verdict and r.verdict ~= "ok" then
      pcall(self.Suggest, self, "verdict", "Trajet — " .. verdictText(r.verdict, r.reasons), E.PRIO[r.verdict] or 2,
        { ttl = 0, cooldown = 300, sound = false })
    else pcall(self.Clear, self, "verdict") end
  end
end

-- full refresh (debounced from events), out of combat
local function logSig(log)
  local ids = {}
  for q, lq in pairs(log) do
    local f = 0
    for _, o in ipairs(lq.obj or {}) do if o.fin then f = f + 1 end end
    ids[#ids + 1] = q .. ":" .. (lq.done or 0) .. ":" .. f
  end
  tsort(ids)
  return tconcat(ids, ",") .. "|" .. myLevel()
end
function P:Refresh(force)
  if inCombat() then self._dirty = true return end
  if co then self:Dirty(force and true or nil) return end   -- a job runs: try again shortly
  local plan, err = self:FilePlan()
  self.planError = err
  if not plan and cfg("auto") then
    local sig = logSig(logSnap())
    if force or not self.auto or (self._autoStale and sig ~= self._autoSig) then
      self._autoStale, self._autoSig = false, sig
      self:StartJob(function()
        P:BuildAuto(budgetYield)   -- no pcall here: Lua 5.1 cannot yield across pcall (resume catches errors)
        P:EnsureSteps(); P:Evaluate(); P:Render()
        if P._syncDirty then P:SendSync(false) end
      end)
      return
    end
  elseif not plan then
    self.auto = nil
  end
  self:EnsureSteps()
  self:Evaluate()
  if self.steps then self:Repair(false) end
  self:Render()
  if self._syncDirty then self:SendSync(false) end
end
function P:Dirty(autoStale)
  if autoStale then self._autoStale = true end
  if self._pending then return end
  self._pending = true
  T:After(1.5, function()
    P._pending = false
    T:RunOutOfCombat("Planner:refresh", function() P:Refresh(false) end)
  end)
end

-- summary for TreeAI Coach / AI context
function P:WriteSummary()
  local s = T.Summary and T:Summary()
  if type(s) ~= "table" then return end
  local r, steps = self.route, self.steps
  if not (r and steps) then s.plan = { v = 1, src = "none" } return end
  local cur = self.cursor or 1
  local st = steps[cur]
  local left = 0
  if st and st.eta and r.minutes then left = max(0, r.minutes - (steps[cur - 1] and steps[cur - 1].eta or 0)) end
  local sync = { n = 0, synchro = 0, avance = 0, retard = 0, autre = 0 }
  for _, p in ipairs(self:PeerStates()) do sync.n = sync.n + 1; sync[p.state] = sync[p.state] + 1 end
  s.plan = { v = 1, src = self.src, made = self.plan and self.plan.made or nil, route = r.id, title = r.title,
    step = cur, steps = #steps, label = st and self:StepLabel(st) or nil, kind = st and st.k or nil, quest = st and st.q or nil,
    verdict = r.verdict or "ok", reason = type(r.reasons) == "table" and r.reasons[1] or nil, xph = r.xph, minutes = r.minutes,
    left = left, hash = E.Hash(steps), sync = sync, updated = epoch() }
end

-- ===================================================================== cycle de vie
function P:OnEnable()
  local function dirty() P:Dirty(true) end
  for _, ev in ipairs({ "QUEST_LOG_UPDATE", "QUEST_ACCEPTED", "QUEST_REMOVED", "PLAYER_LEVEL_UP",
    "ZONE_CHANGED_NEW_AREA", "GROUP_ROSTER_UPDATE" }) do self:RegisterEvent(ev, dirty) end
  self:RegisterUnitEvent("UNIT_QUEST_LOG_CHANGED", "player", dirty)
  self:RegisterEvent("QUEST_TURNED_IN", function(_, _, qid)
    qid = tonumber(qid)
    if qid and P.char then P.char.turned[qid] = true end
    P:Dirty(true)
  end)
  self:On("COMBAT_END", function()
    if P._dirty or P._renderDirty then P._dirty = false; P:Dirty(false) end
  end)
  self:On("EXPORT_UPDATED", function() P:Dirty(true) end)
  self:On("SUMMARY", function() pcall(P.WriteSummary, P) end)
  self:On("SQUAD_UPDATE", function() P:Render() end)
  T:RegisterComm("PLN", self, function(_, payload, sender) P:OnSync(payload, sender) end)
  self.ticker = T:NewTicker(3, function()
    if inCombat() or not P.steps then return end
    -- arrival on a non-quest step (fly / hs / meet…) within 40 yd → done
    local s = P.steps[P.cursor or 0]
    if s and not s.q and s.x then
      local d = where(s)
      if d and d < 40 and P.char then P.char.skip[P.key .. ":" .. P.cursor] = true; P:Evaluate() end
    end
    P:Render()
    if P._syncDirty or (P._lastSync and now() - P._lastSync > 60) then P:SendSync(false) end
  end)
  self:Dirty(true)
end
function P:OnDisable()
  co = nil
  if driver then driver:Hide() end
  if self.ticker then self.ticker:Cancel() end
  if type(T.SetTabLines) == "function" then pcall(T.SetTabLines, T, "quests", {}, "plan") end
end

local function slash(rest)
  rest = tostring(rest or ""):lower():gsub("^%s+", ""):gsub("%s+$", "")
  if rest == "passer" or rest == "skip" then
    local s = P.steps and P.steps[P.cursor or 0]
    if s and P.char then
      if s.q then   -- a quest step is read from the quest log: passing it = moving it after the next step
        if P.steps[P.cursor + 1] then local x = tremove(P.steps, P.cursor); tinsert(P.steps, P.cursor + 1, x) end
      else
        P.char.skip[P.key .. ":" .. P.cursor] = true
      end
    end
    P:Evaluate(); P._sig = nil; P:Render()
    return
  elseif rest == "go" or rest == "guide" then
    P:Navigate()
    return
  elseif rest == "recalc" or rest == "recalcul" then
    if P.char then P.char.repaired = nil end
    P.steps, P.key = nil, nil
    P:Refresh(true)
    T:Print("Trajet recalculé.")
    return
  elseif tonumber(rest) then
    local routes = P:Routes()
    local r = routes[tonumber(rest)]
    if r then P:SelectRoute(r.id) T:Print("Route : " .. tostring(r.title or r.id)) end
    return
  end
  P._sig = nil
  P:Refresh(false)
  for _, l in ipairs(P:Lines()) do T:Print(l.text) end
  T:Print("/tai plan [n° de route | passer | go | recalc] · /tai export")
end
T:RegisterSlash("plan", slash, "plan : trajet de quêtes (routes, étape, verdict)")
T:RegisterSlash("trajet", slash)
