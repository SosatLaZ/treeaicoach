-- TreeAI — module « Talents » (agent TALENTS). WoW Forever a retravaillé les arbres (« rééquilibrés pour que
-- chaque classe soit viable »), double spé au 40 pour 50 po, prix de réinitialisation qui redescend avec le temps.
-- Lit tes talents (GetTalentInfo, signatures Classic / modernes détectées ; C_ClassTalents seulement s'il
-- n'y a pas d'arbre classique), choisit le parcours de montée de ta classe (solo pour les quêtes, groupe selon
-- le rôle ; Data/Talents.lua, validé par tools/wow_build_data.py), et dit point par point QUOI prendre et
-- POURQUOI. Ligne « Prochain point : X (pourquoi) » dans « Maintenant » quand un point est libre, plan de
-- double spé au 40, estimation du prix de réinitialisation (prix réel vu sur CONFIRM_TALENT_WIPE).
-- RIEN n'est dépensé à ta place : LearnTalent est protégé → instructions seulement (touche N, onglet, ligne,
-- colonne). Calcul hors combat. Sortie : onglet « Infos », bloc Talents (SetTabLines, lignes cliquables) + Suggest. Lua 5.1, aucune globale.
local T = _G.TreeAI
if not (T and T.RegisterModule) then return end

local M = T:RegisterModule("Talents", {
  title = "Talents",
  defaults = { enabled = true, nowLine = true, upcoming = 3, dualHint = true },
  charDefaults = { mode = "auto", resets = 0, lastReset = 0, wipeCost = 0, wipeSeen = 0, lastSpent = 0 },
  options = {
    { key = "nowLine", type = "toggle", label = "« Prochain point » dans « Maintenant » quand un point est libre", section = "Affichage" },
    { key = "dualHint", type = "toggle", label = "Conseils de double spé (niveau 40, 50 po)", section = "Affichage" },
    { key = "upcoming", type = "range", label = "Points à venir affichés", min = 0, max = 8, step = 1, section = "Affichage" },
  },
})
if T.MODULE_TAB and not T.MODULE_TAB.Talents then T.MODULE_TAB.Talents = "info" end

local floor, max, min = math.floor, math.max, math.min
local fmt = string.format
local DUAL_LEVEL, DUAL_COST = 40, 50

local function try(fn, ...)
  if type(fn) ~= "function" then return nil end
  local ok, a, b, c, d, e, f, g, h = pcall(fn, ...)
  if ok then return a, b, c, d, e, f, g, h end
end
local function g(name) return rawget(_G, name) end
local function inCombat()
  local f = g("InCombatLockdown")
  return f and try(f) and true or false
end
local function now()
  local t = g("time")
  return (t and try(t)) or 0
end

---------------------------------------------------------------------------------------------------
-- Cœur pur (testable sans WoW)
---------------------------------------------------------------------------------------------------
local C = {}
M.Core = C

function C.Key(tab, tier, col) return tab .. ":" .. tier .. ":" .. col end

function C.BuildsFor(data, cls)
  local out = {}
  for _, b in ipairs((data and data.builds) or {}) do
    if b.class == cls then out[#out + 1] = b end
  end
  return out
end

-- points d'un parcours déjà en place (pour choisir solo/groupe en mode auto)
function C.Match(build, ranks)
  local got, seen = 0, {}
  for _, s in ipairs(build.steps or {}) do
    local k = C.Key(s[1], s[2], s[3])
    local have = (ranks and ranks[k]) or 0
    local prev = seen[k] or 0
    got = got + max(0, min(have, s[4]) - prev)
    seen[k] = max(prev, min(have, s[4]))
  end
  return got
end

function C.Pick(builds, ranks, mode, activeGroup, numGroups)
  local solo, group
  for _, b in ipairs(builds) do
    if b.role == "solo" then solo = solo or b else group = group or b end
  end
  if mode == "solo" then return solo or group, "solo" end
  if mode == "group" then return group or solo, "groupe" end
  if (numGroups or 1) >= 2 and activeGroup == 2 and group then return group, "auto : spé 2 = groupe" end
  if solo and group and C.Match(group, ranks) > C.Match(solo, ranks) then return group, "auto : tes points suivent le parcours groupe" end
  return solo or group, "auto"
end

-- prochain point légal du parcours (et les suivants), compte les points hors parcours
function C.Plan(build, ranks, tabPoints, want)
  ranks, tabPoints = ranks or {}, tabPoints or {}
  local res = { upcoming = {}, off = 0, done = 0 }
  local inBuild = {}
  local sim = {}
  for k, v in pairs(ranks) do sim[k] = v end
  local tabs = { tabPoints[1] or 0, tabPoints[2] or 0, tabPoints[3] or 0 }
  for _, s in ipairs(build.steps or {}) do
    local k = C.Key(s[1], s[2], s[3])
    inBuild[k] = max(inBuild[k] or 0, s[4])
  end
  for k, v in pairs(ranks) do
    if v > 0 then res.off = res.off + max(0, v - (inBuild[k] or 0)) end
  end
  for idx, s in ipairs(build.steps or {}) do
    local k = C.Key(s[1], s[2], s[3])
    local cur = sim[k] or 0
    while cur < s[4] do
      local legal = tabs[s[1]] >= 5 * (s[2] - 1)
      if not legal then break end
      cur = cur + 1
      sim[k] = cur
      tabs[s[1]] = tabs[s[1]] + 1
      local p = { tab = s[1], tier = s[2], col = s[3], rank = cur, target = s[4], name = s[5], why = s[6], step = idx }
      if #res.upcoming < (want or 4) then res.upcoming[#res.upcoming + 1] = p end
    end
    if (ranks[k] or 0) >= s[4] then res.done = res.done + 1 end
  end
  res.next = res.upcoming[1]
  res.complete = res.done >= #(build.steps or {})
  return res
end

-- maximum de rang d'un talent dans l'arbre (données)
function C.MaxRank(data, cls, tab, tier, col)
  local tree = data and data.trees and data.trees[cls] and data.trees[cls][tab]
  for _, t in ipairs((tree and tree.t) or {}) do
    if t[1] == tier and t[2] == col then return t[3], t[4] end
  end
end

-- prix estimé (po) de la prochaine réinitialisation : barème Classic 1, 5, 10 … 50 po, qui redescend
-- de 5 po par mois sans réinitialiser (Forever : le prix « décroît », barème exact non publié).
function C.RespecCost(resets, monthsSince, seenCopper, seenAgeDays)
  if seenCopper and seenCopper > 0 and (seenAgeDays or 99) < 7 then
    return floor(seenCopper / 10000 + 0.5), true
  end
  resets = resets or 0
  if resets <= 0 then return 1, false end
  local cost = min(50, 5 * resets) - 5 * floor(monthsSince or 0)
  return max(1, cost), false
end

function C.Split(tabPoints) return fmt("%d/%d/%d", tabPoints[1] or 0, tabPoints[2] or 0, tabPoints[3] or 0) end

---------------------------------------------------------------------------------------------------
-- Lecture des talents du joueur (toutes signatures, jamais d'erreur)
---------------------------------------------------------------------------------------------------
local function talentInfo(tab, i, group)
  local a, b, c, d, e, f = try(g("GetTalentInfo"), tab, i, false, false, group)
  if type(a) == "string" then            -- Classic : name, icon, tier, column, rank, maxRank
    return a, tonumber(c), tonumber(d), tonumber(e) or 0, tonumber(f) or 0
  end
  if type(a) == "table" then             -- éventuelle forme table
    return a.name, tonumber(a.tier or a.row), tonumber(a.column), tonumber(a.rank) or 0, tonumber(a.maxRank) or 0
  end
end

local function tabName(tab)
  local a, b = try(g("GetTalentTabInfo"), tab)
  if type(a) == "string" then return a end
  if type(b) == "string" then return b end
end

function M.Read()
  local r = { tabs = {}, ranks = {}, points = { 0, 0, 0 }, spent = 0, ok = false }
  local nt = tonumber((try(g("GetNumTalentTabs")))) or 0
  local group = tonumber((try(g("GetActiveTalentGroup")))) or nil
  for tab = 1, min(nt, 3) do
    local n = tonumber((try(g("GetNumTalents"), tab))) or 0
    for i = 1, n do
      local name, tier, col, rank = talentInfo(tab, i, group)
      if name and tier and col then
        r.ok = true
        r.ranks[C.Key(tab, tier, col)] = rank
        r.points[tab] = r.points[tab] + rank
        r.spent = r.spent + rank
      end
    end
    r.tabs[tab] = tabName(tab) or ("Arbre " .. tab)
  end
  if not r.ok and type(g("C_ClassTalents")) == "table" then r.modern = true end   -- arbre moderne : non géré
  local level = tonumber((try(g("UnitLevel"), "player"))) or 1
  local unspent = tonumber((try(g("UnitCharacterPoints"), "player")))
  if not unspent then unspent = tonumber((try(g("GetUnspentTalentPoints")))) end
  if not unspent and r.ok then unspent = max(0, level - 9 - r.spent) end
  r.unspent, r.level = unspent or 0, level
  r.group = group or 1
  r.numGroups = tonumber((try(g("GetNumTalentGroups")))) or 1
  return r
end

---------------------------------------------------------------------------------------------------
-- Rendu
---------------------------------------------------------------------------------------------------
local function data() return T.Data and T.Data.Talents end
local function playerClass()
  local _, cls = try(g("UnitClass"), "player")
  return cls
end

local function howTo(p, tabs)
  return {
    fmt("Touche N (talents) → onglet « %s »", tostring(tabs[p.tab] or p.tab)),
    fmt("Ligne %d, colonne %d : « %s » (%d/%d)", p.tier, p.col, p.name, p.rank, p.target),
    "Clic gauche dessus. TreeAI ne dépense jamais un point à ta place.",
  }
end

function M.State()
  local d = data()
  local cls = playerClass()
  if not (d and cls) then return nil end
  local r = M.Read()
  local builds = C.BuildsFor(d, cls)
  if #builds == 0 then return { r = r, cls = cls } end
  local mode = (M.char and M.char.mode) or "auto"
  local b, why = C.Pick(builds, r.ranks, mode, r.group, r.numGroups)
  local want = 1 + ((M.db and tonumber(M.db.upcoming)) or 3)
  local plan = C.Plan(b, r.ranks, r.points, want)
  local other
  for _, x in ipairs(builds) do if x ~= b then other = other or x end end
  return { r = r, cls = cls, build = b, pickWhy = why, plan = plan, other = other, builds = builds }
end

function M.RespecText()
  local c = M.char or {}
  local months = (c.lastReset or 0) > 0 and (now() - c.lastReset) / (30 * 86400) or 0
  local age = (c.wipeSeen or 0) > 0 and (now() - c.wipeSeen) / 86400 or 99
  local po, seen = C.RespecCost(c.resets or 0, months, c.wipeCost, age)
  if seen then return fmt("Réinitialiser les talents : %d po (prix affiché par ton maître de classe).", po) end
  return fmt("Réinitialiser les talents : ~%d po estimés (le prix monte à chaque fois puis redescend avec le temps).", po)
end

function M.Lines(st)
  local lines = {}
  local function add(text, prio, tooltip, pin, onClick)
    lines[#lines + 1] = { text = text, prio = prio or 1, tooltip = tooltip, pin = pin, onClick = onClick }
  end
  -- règle d'affichage : pas de ligne « rien à faire » (arbre pas encore lu = silence), les actions de
  -- l'onglet sont des boutons (control), un talent pris sur plusieurs rangs = une seule ligne « 1 → 3/5 »
  if not st then add("Talents : données absentes (Data/Talents.lua).", 1) return lines end
  local r = st.r
  if not r.ok and r.modern then
    add("Talents : arbre moderne détecté — conseils écrits seulement (/tai talents).", 1)
  end
  if r.level < 10 then add("Talents au niveau 10 : TreeAI te dira quoi prendre et pourquoi.", 1) end
  local b, plan = st.build, st.plan
  if not b then return lines end
  add(fmt("Parcours : %s (%s) — %s", b.name, b.split, st.pickWhy), 1,
    { b.summary, "Clic : auto → solo → groupe → auto", "(ou /tai talents solo | groupe | auto)" }, nil, M.CycleMode)
  lines[#lines + 1] = { text = M.full and "[Clic] Masquer le parcours complet" or "[Clic] Voir le parcours complet, point par point",
    label = M.full and "Masquer le parcours" or "Parcours complet", control = true, prio = 1,
    tooltip = { "Liste toutes les étapes du parcours avec le niveau et le pourquoi." }, onClick = M.ToggleFull }
  if M.full then
    for _, s in ipairs(b.steps or {}) do
      local have = (r.ranks[C.Key(s[1], s[2], s[3])] or 0) >= s[4]
      add(fmt("%s %s → %d (%s, ligne %d) — %s", have and "✓" or "·", s[5], s[4], tostring(r.tabs[s[1]] or s[1]), s[2], s[6]), 1)
    end
  end
  if r.ok then
    add(fmt("Tes points : %s (%s)", C.Split(r.points), table.concat({ r.tabs[1] or "?", r.tabs[2] or "?", r.tabs[3] or "?" }, " / ")), 1)
  end
  local p = plan.next
  if r.unspent > 0 then
    if p then
      add(fmt("%d point%s libre%s → prends « %s » (%d/%d) : %s", r.unspent, r.unspent > 1 and "s" or "", r.unspent > 1 and "s" or "",
        p.name, p.rank, p.target, p.why), 2, howTo(p, r.tabs), true)
    else
      add(fmt("%d point(s) libre(s) : parcours terminé, mets-les où tu veux.", r.unspent), 2)
    end
  end
  local shown, last = 0, nil
  for i, u in ipairs(plan.upcoming) do
    if i > 1 or r.unspent == 0 then
      if last and last.name == u.name then
        last.to = u.rank
        last.line.text = fmt("Ensuite : %s (%d → %d/%d) — %s", u.name, last.from, u.rank, u.target, u.why)
      else
        shown = shown + 1
        if shown > ((M.db and tonumber(M.db.upcoming)) or 3) then break end
        add(fmt("Ensuite : %s (%d/%d) — %s", u.name, u.rank, u.target, u.why), 1, howTo(u, r.tabs))
        last = { name = u.name, from = u.rank, line = lines[#lines] }
      end
    end
  end
  if plan.off > 0 then
    add(fmt("%d point(s) hors parcours : pas grave, mais le parcours suppose une réinitialisation. %s", plan.off, M.RespecText()), 1)
  elseif plan.complete then
    add("Parcours complet. " .. M.RespecText(), 1)
  end
  if M.db == nil or M.db.dualHint ~= false then
    local dual = st.other and st.build and (st.build.role == "solo" and st.other or st.build)
    local solo = st.build.role == "solo" and st.build or st.other
    if solo and dual and r.level >= DUAL_LEVEL - 4 then
      if r.numGroups >= 2 then
        add(fmt("Double spé : spé 1 « %s », spé 2 « %s ». Spé active : %d.", solo.spec, dual.spec, r.group), 1)
      elseif r.level >= DUAL_LEVEL then
        add(fmt("Double spé disponible (%d po, maître de classe) : spé 1 « %s » pour les quêtes, spé 2 « %s » pour le groupe.",
          DUAL_COST, solo.spec, dual.spec), 2)
      else
        add(fmt("Au niveau %d : double spé (%d po). Plan : « %s » en solo + « %s » en groupe — garde %d po.",
          DUAL_LEVEL, DUAL_COST, solo.spec, dual.spec, DUAL_COST), 1)
      end
    end
    local inGroup = try(g("IsInGroup"))
    if inGroup and st.build.role == "solo" and dual and dual ~= st.build and r.numGroups < 2 then
      add(fmt("En groupe : « %s » (%s) est fait pour ça — /tai talents groupe pour le voir.", dual.spec, dual.role), 1)
    end
  end
  return lines
end

local pending, lastRun = false, 0
function M.Update()
  if inCombat() then M.dirty = true return end
  M.dirty = false
  lastRun = now()
  local st = M.State()
  M.state = st
  local lines = M.Lines(st)
  if T.SetTabLines then pcall(T.SetTabLines, T, "info", lines, "Talents") end
  local p = st and st.plan and st.plan.next
  if st and st.r.unspent > 0 and p and (M.db == nil or M.db.nowLine ~= false) then
    M:Suggest("next", fmt("Prochain point : %s (%d/%d) — %s", p.name, p.rank, p.target, p.why), 2,
      { ttl = 0, tab = "info", tooltip = howTo(p, st.r.tabs), sound = false })
  else
    M:Clear("next")
  end
  -- réinitialisation détectée (tous les points rendus)
  if st and st.r.ok and M.char then
    if (M.char.lastSpent or 0) >= 5 and st.r.spent == 0 then
      M.char.resets = (M.char.resets or 0) + 1
      M.char.lastReset = now()
    end
    M.char.lastSpent = st.r.spent
  end
end

function M.Refresh(delay)
  M.dirty = true
  if pending then return end
  pending = true
  T:After(delay or 0.5, function() pending = false pcall(M.Update) end)
end

function M:AutoRefresh()
  if M.dirty or now() - lastRun > 60 then pcall(M.Update) end
end

function M.WriteSummary()
  local s = T:Summary()
  if type(s) ~= "table" then return end
  local st = M.state or M.State()
  if not st then return end
  local r = st.r
  local out = { unspent = r.unspent, points = C.Split(r.points), trees = { r.tabs[1], r.tabs[2], r.tabs[3] },
    group = r.group, groups = r.numGroups, mode = M.char and M.char.mode, resets = M.char and M.char.resets,
    respec = M.RespecText() }
  if st.build then
    out.build, out.buildName, out.role = st.build.id, st.build.name, st.build.role
    local p = st.plan and st.plan.next
    if p then out.next = { name = p.name, rank = p.rank, max = p.target, why = p.why, tab = p.tab, tier = p.tier, col = p.col } end
    out.off = st.plan and st.plan.off
  end
  local rk = {}
  for k, v in pairs(r.ranks) do if v > 0 then rk[#rk + 1] = k .. "=" .. v end end
  table.sort(rk)
  out.ranks = table.concat(rk, ",")
  s.talents = out
end

function M.CycleMode()
  if not M.char then return end
  local nxt = { auto = "solo", solo = "group", group = "auto" }
  M.char.mode = nxt[M.char.mode or "auto"] or "auto"
  pcall(M.Update)
end
function M.ToggleFull()
  M.full = not M.full
  pcall(M.Update)
end

function M.Report(arg)
  arg = tostring(arg or ""):lower():gsub("^%s+", ""):gsub("%s+$", "")
  if arg == "solo" or arg == "groupe" or arg == "group" or arg == "auto" then
    if M.char then M.char.mode = (arg == "groupe" or arg == "group") and "group" or arg end
  end
  M.Update()
  for _, l in ipairs(M.Lines(M.state)) do M:Print(l.text) end
end

function M:OnEnable()
  for _, ev in ipairs({ "PLAYER_TALENT_UPDATE", "CHARACTER_POINTS_CHANGED", "PLAYER_LEVEL_UP", "ACTIVE_TALENT_GROUP_CHANGED",
    "GROUP_ROSTER_UPDATE", "PLAYER_ENTERING_WORLD" }) do
    M:RegisterEvent(ev, function() M.Refresh(1) end)
  end
  M:RegisterEvent("CONFIRM_TALENT_WIPE", function(_, _, cost)
    if M.char and tonumber(cost) then M.char.wipeCost, M.char.wipeSeen = tonumber(cost), now() end
    M.Refresh(0.5)
  end)
  M:On("COMBAT_END", function() M.Refresh(1) end)
  M:On("SUMMARY", function() pcall(M.WriteSummary) end)
  if T.RegisterSlash then
    T:RegisterSlash("talents", function(rest) M.Report(rest) end, "talents : prochain point + pourquoi (/tai talents solo|groupe|auto)")
  end
  M.Refresh(2)
end

function M:OnDisable()
  if T.SetTabLines then pcall(T.SetTabLines, T, "info", {}, "Talents") end
end
function M:OnOptionChanged() M.Refresh(0.2) end

T.Talents = M
