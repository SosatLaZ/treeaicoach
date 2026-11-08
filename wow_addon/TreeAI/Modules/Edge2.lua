-- TreeAI — module « Avantages » (fichier Edge2.lua, CLEAN 4.0) : les petits avantages LÉGAUX qui reposent sur un
-- FAIT lisible dans le jeu — rien de deviné. Fusion des anciens « Coups d'avance » (Edge, retiré) et « Avantages ».
-- Chacun = UNE ligne qui commence par un verbe, cachée tant qu'elle ne sert à rien, coupable une par une (options).
-- Prio 2 = peut monter dans « Maintenant » ; le détail (chiffres) va dans l'infobulle. Jamais dans le chat.
--   Farm    : contestCamp (monstres de quête déjà tapés par d'autres : barres de nom grisées par le jeu)
--             dropLuck (objet de quête qui ne tombe pas : taux mesuré par Stats sur tes kills)
--             lootLeft (corps pas fouillés, en solo : kills comptés par Detect, fouilles par le jeu)
--             gatherTrack (récolteur sans suivi actif sur la mini-carte : un clic l'active)
--   Quêtes  : turninLevel (tes quêtes finies valent le niveau suivant : XP des récompenses lue dans le journal)
--             escortFail (escorte échouée : à reprendre) — le chrono d'une quête chronométrée : module Quêtes seul
--             breadcrumb (quête de liaison devenue inutile : QuestieDB + quêtes faites du jeu)
--   Ville   : worldBuff (cri d'un buff mondial en ville : reste là quelques secondes)
-- Retiré en 4.0 : repop / patrouilles / coin de farm appris sur trop peu d'observations, chuchotement pré-rempli à
-- ta guilde, double spé / Legacy / camp (devinés), marchand ↔ HV (sans scan frais), doublons de Combat, Progression,
-- Chasseur, Guichets, Inventaire (repos, maître de classe, quoi jeter, boire ou enchaîner, gros sorts prêts…).
-- Coût : événements + AutoRefresh qui ne fait qu'UNE petite vérification par passage, rien en combat.
-- Lua 5.1, aucune globale, API testées (pcall), valeurs secrètes (Forever, en combat) ignorées.
local T = _G.TreeAI
if not T or not T.RegisterModule then return end
local API = T.API or {}
local G = _G

local FEATURES = {
  { "contestCamp", "Camp disputé : d'autres joueurs tapent tes monstres de quête", "Farm" },
  { "dropLuck", "Objet de quête qui ne tombe pas : malchance ou mauvais monstre", "Farm" },
  { "lootLeft", "Corps pas fouillés (solo)", "Farm" },
  { "gatherTrack", "Récolte : active le suivi des plantes / filons", "Farm" },
  { "turninLevel", "Quêtes finies = niveau suivant d'un coup", "Quêtes" },
  { "escortFail", "Escorte échouée : à reprendre", "Quêtes" },
  { "breadcrumb", "Quête de liaison devenue inutile", "Quêtes" },
  { "worldBuff", "Buff mondial annoncé en ville : reste là", "Ville" },
}

local defaults = { enabled = true }
local options = {}
for _, f in ipairs(FEATURES) do
  defaults[f[1]] = true
  options[#options + 1] = { key = f[1], type = "toggle", label = f[2], section = f[3] }
end

local M = T:RegisterModule("Edge2", {
  title = "Avantages",
  defaults = defaults,
  charDefaults = {},
  options = options,
})
T.Edge2 = M
M.FEATURES = FEATURES
local TAB = "adv"
M.TAB = TAB
if T.MODULE_TAB then T.MODULE_TAB.Edge2 = TAB end
if T.TAB_OWNER then T.TAB_OWNER[TAB] = "Edge2" end

local floor, max, min, ceil = math.floor, math.max, math.min, math.ceil
local fmt, tconcat = string.format, table.concat
local function g(name) return rawget(G, name) end
local function pass(ok, ...) if ok then return ... end return nil end
local function try(fn, ...) if type(fn) ~= "function" then return nil end return pass(pcall(fn, ...)) end
local function secret(v) local f = g("issecretvalue") return type(f) == "function" and try(f, v) and true or false end
local function sv(v) if v == nil or secret(v) then return nil end return v end
local function num(v) v = sv(v) return tonumber(v) end
local function now() return try(g("GetTime")) or 0 end
local function opt(k) return M.db and M.db[k] ~= false end
local function mod(n) local m = T.modules and T.modules[n] return (m and (not m.IsEnabled or m:IsEnabled())) and m or nil end
local function level() return num(try(g("UnitLevel"), "player")) or 1 end
local function inCombat() return T.InCombat and T:InCombat() or false end
local function inInstance() return try(g("IsInInstance")) and true or false end
local function resting() return try(g("IsResting")) and true or false end
local function groupSize() return num(try(g("GetNumGroupMembers"))) or 0 end
local function low(s) return tostring(s or ""):lower() end
local function zone() return sv(try(g("GetRealZoneText"))) or sv(try(g("GetZoneText"))) or "" end
local Fmt = T.Fmt or {}
local function thousands(n) return Fmt.Num and Fmt.Num(n) or tostring(floor(tonumber(n) or 0)) end

local function nm(s, n)
  if s == nil or secret(s) then return nil end
  s = tostring(s):gsub("|c%x%x%x%x%x%x%x%x", ""):gsub("|r", ""):gsub("|H.-|h(.-)|h", "%1"):gsub("^%s+", ""):gsub("%s+$", "")
  if s == "" or s:find("?", 1, true) or s == "nil" then return nil end
  n = n or 30
  return T.Cut(s, n)                -- (4.0) Core : n caractères UTF-8, « … » compris
end
M.Name = nm

-- ===================================================================================== sortie (une seule voie)
-- o.window (s) + o.sig : une même ligne n'est montrée que `window` s, puis se tait jusqu'à ce que le sens change.
local firstAt, firstN = {}, 0
M.shown = {}
local tabDirty = true
local function say(key, text, prio, o)
  if not opt(key) or not text then M:Clear(key) return nil end
  o = o or {}
  if o.window then
    local sig = key .. "|" .. tostring(o.sig or text)
    if not firstAt[sig] then
      firstN = firstN + 1
      if firstN > 300 then firstAt, firstN = {}, 0 end
      firstAt[sig] = now()
    end
    if now() - firstAt[sig] > o.window then M:Clear(key) return nil end
  end
  o.window, o.sig = nil, nil
  if o.sound == nil then o.sound = false end
  if o.ttl == nil then o.ttl = 60 end
  if o.cooldown == nil then o.cooldown = 120 end
  o.chat, o.tab = false, TAB
  M.shown[key] = (M.shown[key] or 0) + 1
  tabDirty = true
  return M:Suggest(key, text, prio or 1, o)
end
local function drop(key)
  if T._suggestions and T._suggestions["Edge2:" .. key] then tabDirty = true end
  M:Clear(key)
end
M.Say, M.Drop = say, drop

local function quests()
  local Q = mod("Quests")
  return (Q and Q.lastList) or {}
end
local function mobOf(objName)
  local s = T.ObjectiveName and T.ObjectiveName(objName) or tostring(objName or "")
  s = s:gsub("%s+tuée?s?$", ""):gsub("%s+slain$", ""):gsub("%s+killed$", "")
  return s
end
M.MobOf = mobOf
-- QuestieDB (lecture seule) : champ d'une quête
local function qField(id, key)
  local Q = T.modules and T.modules.Quests
  local db = Q and Q.QuestieDB and Q.QuestieDB()
  if db and db.QueryQuestSingle then return try(db.QueryQuestSingle, id, key) end
  local lib = g("LibQuestieDB")
  if type(lib) == "table" and type(lib.Quest) == "table" and type(lib.Quest.Get) == "function" then return try(lib.Quest.Get, id, key) end
  return nil
end
local function questDone(id)
  local CQ = g("C_QuestLog")
  local fn = (CQ and CQ.IsQuestFlaggedCompleted) or g("IsQuestFlaggedCompleted")
  return try(fn, id) and true or false
end
local CAPITALS = { ["hurlevent"] = true, ["stormwind"] = true, ["forgefer"] = true, ["ironforge"] = true,
  ["darnassus"] = true, ["orgrimmar"] = true, ["les pitons-du-tonnerre"] = true, ["pitons-du-tonnerre"] = true,
  ["thunder bluff"] = true, ["fossoyeuse"] = true, ["undercity"] = true, ["la croisée"] = true, ["the crossroads"] = true }
local function inCapital() return CAPITALS[low(zone())] or CAPITALS[low(sv(try(g("GetSubZoneText"))))] or false end

-- ===================================================================================== moteur pur (testable)
local E = {}
M.Engine = E

--- camp disputé : plates = { {name, tapped} }, mobs = set des monstres de quête -> nom, tapés, total
function E.Contest(plates, mobs)
  local by = {}
  for _, p in ipairs(plates or {}) do
    if p.name and mobs[p.name] then
      local b = by[p.name] or { n = 0, t = 0 }
      b.n = b.n + 1
      if p.tapped then b.t = b.t + 1 end
      by[p.name] = b
    end
  end
  local best, bt, bn
  for name, b in pairs(by) do
    if b.t >= 2 and b.t / b.n >= 0.5 and (not bt or b.t > bt) then best, bt, bn = name, b.t, b.n end
  end
  return best, bt, bn
end

--- malchance : n kills sans drop pour un taux p -> probabilité (0-1) d'une telle série
function E.Streak(n, p)
  n, p = tonumber(n) or 0, tonumber(p)
  if not p or p <= 0 or p >= 1 then return nil end
  return (1 - p) ^ n
end

--- quêtes finies : list (Quests.lastList), need -> n, xp, couvre le niveau ?
function E.TurnIn(list, need)
  local n, xp = 0, 0
  for _, q in ipairs(list or {}) do
    if q.complete and tonumber(q.xp) then n, xp = n + 1, xp + q.xp end
  end
  return n, xp, (need or 0) > 0 and xp >= need
end

--- escorte : titre / objectif qui parle d'escorter, protéger, accompagner
local ESCORT = { "escort", "protég", "proteg", "accompagn", "protect" }
function E.IsEscort(q)
  if type(q) ~= "table" then return false end
  local txt = low(q.title)
  for _, o in ipairs(q.obj or {}) do txt = txt .. " " .. low(o.text or o.name) end
  for _, p in ipairs(ESCORT) do if txt:find(p, 1, true) then return true end end
  return false
end

--- buff mondial annoncé par un cri (Classic : Onyxia / Nefarian, Rend, Hakkar) : nom du buff ou nil
E.WORLD_BUFFS = { dragon = "Cri de ralliement du pourfendeur de dragons", rend = "Bénédiction du chef de guerre",
  hakkar = "Esprit de Zandalar" }
function E.WorldBuff(msg, speaker)
  local s = low(msg) .. " " .. low(speaker)
  if s:find("onyxia", 1, true) or s:find("nefarian", 1, true) then
    for _, k in ipairs({ "slain", "vaincu", "tué", "mort", "rallying", "ralliement", "tête", "head" }) do
      if s:find(k, 1, true) then return E.WORLD_BUFFS.dragon end
    end
  end
  if s:find("rend", 1, true) and (s:find("blackhand", 1, true) or s:find("main-noire", 1, true)) then return E.WORLD_BUFFS.rend end
  if s:find("hakkar", 1, true) and (s:find("slain", 1, true) or s:find("vaincu", 1, true) or s:find("tué", 1, true)) then
    return E.WORLD_BUFFS.hakkar
  end
  return nil
end

-- ===================================================================================== état (session)
local lastKillAt, pendingLoot = -1e9, 0
local streak = {}      -- [item|mob] = kills sans drop
local haveSeen = {}    -- [qid|i] = have

-- ----------------------------------------------------------------------------- kills (Detect) : dropLuck, lootLeft
function M.OnKill(name)
  name = nm(name, 40)
  if not name then return end
  lastKillAt = now()
  if groupSize() <= 1 then pendingLoot = pendingLoot + 1 end
  local S = T.Stats or mod("Stats")
  if S and S.DropByName then
    for _, q in ipairs(quests()) do
      for _, o in ipairs(q.obj or {}) do
        if o.t == "i" and not o.finished and o.name then
          local m = try(S.DropByName, S, name, o.name)
          if tonumber(m) then
            local k = o.name .. "|" .. name
            streak[k] = (streak[k] or 0) + 1
          end
        end
      end
    end
  end
end

function M.QuestProgress()
  for _, q in ipairs(quests()) do
    for i, o in ipairs(q.obj or {}) do
      local k = tostring(q.id) .. "|" .. i
      local h = tonumber(o.have) or 0
      if haveSeen[k] and h > haveSeen[k] and o.name then
        for sk in pairs(streak) do if sk:sub(1, #o.name + 1) == o.name .. "|" then streak[sk] = 0 end end
      end
      haveSeen[k] = h
    end
  end
end
function M.CheckLuck()
  if not opt("dropLuck") then return drop("dropLuck") end
  local S = T.Stats or mod("Stats")
  if not (S and S.DropByName) then return drop("dropLuck") end
  local worst, wp, wn, wm, witem, wmob
  for k, n in pairs(streak) do
    if n >= 5 then
      local item, mob = k:match("^(.-)|(.*)$")
      local m = tonumber((try(S.DropByName, S, mob, item)))
      local p = E.Streak(n, m)
      if p and p < 0.1 and (not wp or p < wp) then worst, wp, wn, wm, witem, wmob = k, p, n, m, item, mob end
    end
  end
  if not worst then return drop("dropLuck") end
  local best, bm = nil, nil
  if S.BestSourceByName then best, bm = try(S.BestSourceByName, S, witem) end
  local tip = { fmt("%d %s sans « %s »", wn, wmob, witem), fmt("Taux mesuré : %d %% (1 chance sur %d d'une telle série)",
    floor(wm * 100 + 0.5), max(2, floor(1 / wp + 0.5))) }
  if best and best ~= wmob and tonumber(bm) and bm > wm * 1.3 then
    tip[#tip + 1] = fmt("%s : %d %%", best, floor(bm * 100 + 0.5))
    say("dropLuck", fmt("Tue plutôt des %s pour « %s » : meilleur taux mesuré", nm(best, 22), nm(witem, 22)), 2,
      { ttl = 120, cooldown = 300, tooltip = tip })
  elseif wp < 0.03 then
    say("dropLuck", fmt("Vérifie la source de « %s » : %d %s sans en avoir", nm(witem, 22), wn, nm(wmob, 20)), 2,
      { ttl = 120, cooldown = 300, tooltip = tip })
  else
    drop("dropLuck")
  end
end

function M.OnLoot() pendingLoot = max(0, pendingLoot - 1) drop("lootLeft") end   -- il fouille : on se tait
function M.CheckLoot()
  if not opt("lootLeft") or inCombat() or groupSize() > 1 then return drop("lootLeft") end
  if now() - lastKillAt > 60 then pendingLoot = 0 end
  if pendingLoot < 3 then return drop("lootLeft") end
  say("lootLeft", fmt("Fouille tes corps : %d monstres tués sans ramasser", pendingLoot), 2,
    { ttl = 30, cooldown = 120, tooltip = "Butin, objets de quête et argent restent sur les corps (solo)." })
end

-- ----------------------------------------------------------------------------- camp disputé
local function questMobs()
  local set, any = {}, false
  for _, q in ipairs(quests()) do
    if not q.complete then
      for _, o in ipairs(q.obj or {}) do
        if not o.finished and o.name then
          local m = mobOf(o.name)
          if m ~= "" then set[m], any = true, true end
        end
      end
    end
  end
  return set, any
end
function M.ScanPlates()
  local out = {}
  for i = 1, 40 do
    local u = "nameplate" .. i
    if try(g("UnitExists"), u) and not try(g("UnitIsPlayer"), u) and not try(g("UnitIsDead"), u)
        and try(g("UnitCanAttack"), "player", u) then
      local name = nm(sv(try(g("UnitName"), u)), 40)
      if name then out[#out + 1] = { name = name, tapped = sv(try(g("UnitIsTapDenied"), u)) and true or false } end
    end
  end
  return out
end
function M.CheckContest()
  if not opt("contestCamp") or inCombat() or inInstance() then return drop("contestCamp") end
  local set, any = questMobs()
  if not any then return drop("contestCamp") end
  local name, t, n = E.Contest(M.ScanPlates(), set)
  if not name then return drop("contestCamp") end
  say("contestCamp", fmt("Change de camp ou groupe-toi : %d %s sur %d tapés par d'autres", t, nm(name, 24), n), 2,
    { ttl = 40, cooldown = 180, tooltip = { "Camp disputé : un monstre tapé par un autre joueur ne te compte pas.",
      "En groupe, les kills de quête sont partagés : inviter l'autre joueur fait gagner les deux." } })
end

-- ----------------------------------------------------------------------------- quêtes finies = niveau
function M.CheckTurnIn()
  if not opt("turninLevel") or level() >= 60 then return drop("turninLevel") end
  local xp, xpMax = num(try(g("UnitXP"), "player")), num(try(g("UnitXPMax"), "player"))
  if not (xp and xpMax) then return drop("turninLevel") end
  local n, total, enough = E.TurnIn(quests(), xpMax - xp)
  if not enough or n == 0 then return drop("turninLevel") end
  local what = n > 1 and fmt("tes %d quêtes finies", n) or "ta quête finie"
  say("turninLevel", fmt("Rends %s : niveau %d d'un coup (puis passe chez ton maître de classe)", what, level() + 1), 2,
    { ttl = 600, cooldown = 900, window = 600, sig = level(),
      tooltip = fmt("%s XP à rendre, il manque %s XP pour le niveau.", thousands(total), thousands(xpMax - xp)) })
end

-- ----------------------------------------------------------------------------- escorte échouée
function M.CheckEscort()
  if not opt("escortFail") or inCombat() then return drop("escortFail") end
  local Q = mod("Quests")
  for _, q in ipairs(quests()) do
    local title = q.failed and E.IsEscort(q) and nm(q.title, 30)
    if title then
      return say("escortFail", fmt("Abandonne puis reprends « %s » : l'escorte a échoué", title), 2,
        { ttl = 300, cooldown = 600, tooltip = "Clic : comment la retirer (l'abandon reste manuel).",
          onClick = function() if Q and Q.OpenQuest then try(Q.OpenQuest, q) end end })
    end
  end
  drop("escortFail")
end

-- ----------------------------------------------------------------------------- quête de liaison inutile
function M.CheckBreadcrumb()
  if not opt("breadcrumb") then return drop("breadcrumb") end
  for _, q in ipairs(quests()) do
    local target = tonumber(qField(q.id, "breadcrumbForQuestId"))
    if target and target > 0 and not q.complete and questDone(target) then
      local a, b = nm(q.title, 28), nm(qField(target, "name"), 28)
      if a and b then
        local Q = mod("Quests")
        return say("breadcrumb", fmt("Abandonne « %s » : elle mène à « %s », déjà faite", a, b), 1,
          { ttl = 300, cooldown = 1800, window = 300, sig = q.id, tooltip = "Clic : comment la retirer (l'abandon reste manuel).",
            onClick = function() if Q and Q.OpenQuest then try(Q.OpenQuest, q) end end })
      end
    end
  end
  drop("breadcrumb")
end

-- ----------------------------------------------------------------------------- suivi de récolte (mini-carte)
local TRACK = {
  { skills = { "herboristerie", "herbalism" }, label = "Découverte d'herbes", tex = 133939, keys = { "herb", "herbe" } },
  { skills = { "minage", "mining" }, label = "Découverte de gisements", tex = 136025, keys = { "miner", "gisement", "filon" } },
}
local function skillNames()
  local out = {}
  for _, l in ipairs(API.SkillLines and API.SkillLines() or {}) do
    if l.name and not l.isHeader and not secret(l.name) then out[#out + 1] = l.name end
  end
  return out
end
local function trackingState()
  local MM = g("C_Minimap")
  if MM and MM.GetNumTrackingTypes and MM.GetTrackingInfo then
    local list, any = {}, false
    for i = 1, min(tonumber((try(MM.GetNumTrackingTypes))) or 0, 40) do
      local a, b, c = try(MM.GetTrackingInfo, i)
      local name, tex, active
      if type(a) == "table" then name, tex, active = a.name, a.texture, a.active else name, tex, active = a, b, c end
      list[#list + 1] = { i = i, name = low(name), tex = tex, active = active and true or false }
      if active then any = true end
    end
    return list, any
  end
  local tex = try(g("GetTrackingTexture"))
  if tex == nil then return nil end
  return { { name = "", tex = tex, active = true } }, true
end
function M.CheckTracking()
  if not opt("gatherTrack") or inCombat() or inInstance() or resting() then return drop("gatherTrack") end
  local known = {}
  for _, n in ipairs(skillNames()) do known[low(n)] = true end
  local list, any = trackingState()
  if list == nil or any then return drop("gatherTrack") end
  for _, tr in ipairs(TRACK) do
    if known[tr.skills[1]] or known[tr.skills[2]] then
      local idx
      for _, e in ipairs(list) do
        if e.tex == tr.tex then idx = e.i end
        for _, k in ipairs(tr.keys) do if e.name:find(k, 1, true) then idx = idx or e.i end end
      end
      local MM = g("C_Minimap")
      return say("gatherTrack", fmt("Active « %s » (mini-carte) : les nœuds s'affichent", tr.label), 2,
        { ttl = 120, cooldown = 1800, window = 120, sig = zone(), onClick = (idx and MM and MM.SetTracking) and function()
          T:UserAction(function() MM.SetTracking(idx, true) end)
          drop("gatherTrack")
        end or nil })
    end
  end
  drop("gatherTrack")
end

-- ----------------------------------------------------------------------------- buff mondial
function M.OnYell(msg, speaker)
  if not opt("worldBuff") or secret(msg) or secret(speaker) then return end
  local buff = E.WorldBuff(msg, speaker)
  if not buff then return end
  if buff == E.WORLD_BUFFS.hakkar then
    local z = low(zone())
    if not (z:find("strangleronce", 1, true) or z:find("stranglethorn", 1, true)) then return end
  elseif not inCapital() then return end
  say("worldBuff", fmt("Reste là 20 s : buff « %s » dans un instant", buff), 2, { ttl = 25, cooldown = 60 })
end

-- ===================================================================================== onglet « Avantages »
local lastSig
function M.BuildTab(force)
  if not (T.SetTabLines and (tabDirty or force)) then return end
  tabDirty = false
  local lines = {}
  local active = false
  for _, f in ipairs(FEATURES) do if T._suggestions and T._suggestions["Edge2:" .. f[1]] then active = true break end end
  if not active then
    lines[1] = { text = "Rien pour l'instant : chaque avantage apparaît ici quand il sert", prio = 1,
      tooltip = "Camp disputé, objet de quête qui ne tombe pas, quêtes finies = niveau, escorte échouée, suivi de récolte…" }
  end
  local parts = {}
  for _, l in ipairs(lines) do parts[#parts + 1] = l.text end
  local sig = tconcat(parts, "\n")
  if sig == lastSig and not force then return end
  lastSig = sig
  pcall(T.SetTabLines, T, TAB, lines, "Edge2")
end

-- ===================================================================================== boucle + évènements
local function run(fn, ...) return pcall(fn, ...) end
local tick = 0
--- une seule petite vérification par passage ; rien en combat (AutoRefresh n'y tourne pas)
local ROT = { "CheckContest", "CheckTurnIn", "CheckLuck", "CheckLoot", "CheckEscort", "CheckTracking", "CheckBreadcrumb" }
function M:AutoRefresh()
  tick = tick + 1
  if inCombat() then return end
  run(M[ROT[(tick % #ROT) + 1]])
  if tick % 4 == 0 then run(M.BuildTab) end
end

function M:OnEnable()
  if T.RegisterTab and not (T.tabs and T.tabs[TAB] and not T.tabs[TAB].auto) then T:RegisterTab(TAB, "Avantages", 31) end
  if T.TAB_OWNER then T.TAB_OWNER[TAB] = "Edge2" end
  -- un seul lecteur du message d'XP : Detect publie chaque mort (« X meurt, vous gagnez… »)
  self:On("DETECT", function(_, _, kind, data) if kind == "kill" and type(data) == "table" then run(M.OnKill, data.name) end end)
  self:RegisterEvent("LOOT_OPENED", function() run(M.OnLoot) end)
  self:RegisterEvent("QUEST_LOG_UPDATE", function() run(M.QuestProgress) end)
  self:RegisterEvent("CHAT_MSG_MONSTER_YELL", function(_, _, msg, speaker) M.OnYell(msg, speaker) end)
  tabDirty = true
  run(M.BuildTab, true)
end
function M:OnDisable()
  if T.SetTabLines then pcall(T.SetTabLines, T, TAB, {}, "Edge2") end
  lastSig = nil
end
function M:OnOptionChanged(key, v)
  if v == false and key then drop(key) end
  tabDirty = true
  run(M.BuildTab, true)
end
function M.ResetSession()
  lastKillAt, pendingLoot, streak, haveSeen = -1e9, 0, {}, {}
  firstAt, firstN = {}, 0
  M.shown = {}
end
M.Streaks = function() return streak end
