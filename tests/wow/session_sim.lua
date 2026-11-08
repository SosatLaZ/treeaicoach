-- session_sim.lua — 60 minutes of simulated play, 4 TreeAI clients in one group, on the WoW API mock.
-- Nobody tests the 31 modules TOGETHER over TIME: this does. Each client = the whole addon (TOC order)
-- loaded in its own Lua environment (setfenv) with its own wowmock instance; addon messages sent by one
-- client are delivered to the 3 others (and echoed to the sender) through CHAT_MSG_ADDON, like PARTY.
-- The main client lives a scripted session: login, quests accepted / progressed (kills via XP messages,
-- loot) / completed / turned in, level-up, trainer, vendor (sell / repair), bags filling, hunter pet
-- events, a death + ghost run, hearthstone, flight, auction house, combat in / out every ~40 s,
-- /reload twice (SavedVariables serialised WoW-style and restored), panel tabs + clicks.
-- Measured and asserted (exit 1 if any budget is broken):
--   Lua errors (handlers AND errors swallowed by the addon's pcall/safecall), rule violations (mock:
--   protected calls in combat / outside a click, comms limits), global leaks, CPU per module per minute
--   (deterministic: count-hook samples of 1000 VM instructions, attributed to the innermost addon file),
--   « Maintenant » changes / min, corner alerts / 10 min, duplicates by meaning, SavedVariables size
--   growth, addon memory growth (reachable from TreeAI, upvalues included), addon message rate.
-- Fake names only. Lua 5.1:
--   lua5.1 tests/wow/session_sim.lua [--class HUNTER] [--flavor modern] [--minutes 60] [--addon DIR] [-v]
--     [--profile essentiel|tout] (default essentiel = what a new player gets) [--fine] (CPU by function)
--   Hitches (SPIKE): cost of every main-client frame (timers + OnUpdate, 0.2-s step): worst / p99, the 1-Hz
--   phase ratio, the worst frames with their top functions (-v: + the jobs that pay), KB allocated / s.
local HERE = (arg and arg[0] or "session_sim.lua"):match("^(.*)[/\\][^/\\]*$") or "."
local opts = { class = "HUNTER", flavor = "modern", minutes = 60, addon = HERE .. "/../../wow_addon/TreeAI" }
do
  local i = 1
  while arg and i <= #arg do
    local a = arg[i]
    if a == "--class" then opts.class = arg[i + 1]:upper(); i = i + 1
    elseif a == "--flavor" then opts.flavor = arg[i + 1]; i = i + 1
    elseif a == "--minutes" then opts.minutes = tonumber(arg[i + 1]); i = i + 1
    elseif a == "--addon" then opts.addon = arg[i + 1]; i = i + 1
    elseif a == "--profile" then opts.profile = arg[i + 1]; i = i + 1
    elseif a == "--fine" then opts.fine = true
    elseif a == "-v" then opts.verbose = true end
    i = i + 1
  end
end

-- budgets ----------------------------------------------------------------------------------------
local BUDGET = {
  kinstrPerModuleMin = 4000,     -- avg kilo-instructions / module / simulated minute (main client)
  kinstrPerModulePeak = 15000,   -- worst single minute for one module
  -- whole addon, avg / minute (LITE): « essentiel » (default profile) ≤ 1.5 M instr/min, everything on ≤ 4 M
  kinstrTotalMin = (opts.profile == "tout") and 4600 or 2450,   -- 3.9.5: budgets as on the hotfix branch (3.9.4: 9.9 M)
  -- 3.9.9: +50 for 10 new light modules (Quick, QuickFight, QuickTravel, PathDraw, Finder, NavTime, Horizon, ZonePlan,
  -- PvPMark, Macros review ≈ 190 kinstr/min, each under its own cap) after cutting integration waste (2477 → 2418 measured)
  kinstrStatsMin = 60,           -- Stats hub (Modules/Stats.lua): event-driven, nearly free
  kinstrModuleMin = { Dungeon = 80, PathDraw = 60, Edge2 = 80, Finder = 60, Quick = 80, QuickFight = 80, Macros = 80, QuickTravel = 80, Auction = 80, Compass = 90, NavTime = 80, Horizon = 80, ZonePlan = 80, PvPMark = 40,
    -- QUESTQA: the quest-tracking chain, each under the round-7 module budget
    Route = 80, Planner = 80, Quests = 80, Tracker = 80, Todo = 80, Export = 80, Detect = 80, Speed = 80, Tour = 10,
    -- CLEAN 4.0 (ROUND7 : ≤ 80 par module) : Combat, Rares, Chasseur, Équipement… (avant : Fight 139-191, Hunter 97)
    Fight = 80, Boost = 40, Hunter = 80, Gear = 80, Inventory = 80, Errands = 60, Session = 60, Leveling = 60, Prof = 60,
    Talents = 40, Binds = 60, Classes = 60, PvP = 60 },   -- per-module caps (avg kinstr / minute); Group below
  nowChangesPerMinMax = 12,      -- « Maintenant » changes (text skeleton, digits ignored) in any minute
  nowChangesPerMinAvg = 6,      -- a fight every ~35 s brings its own in-combat lines (aspect, pet HP…)
  alertsPer10Min = 8,            -- corner alert pops in any 10-minute window
  sameAlertRepeats = 3,          -- the same alert text popping more than this in 10 min = nagging
  dupMeaningPct = 5,             -- % of sampled seconds with two « Maintenant » lines saying the same thing
  svBytesMax = 400 * 1024,       -- SavedVariables at logout
  svGrowthLastHalf = 48 * 1024,  -- growth between the 30th and the 60th minute
  memGrowthKB = 512,             -- addon-reachable memory growth inside one load segment (after warm-up)
  msgsPerMinMax = 70,            -- per client, any minute (Core throttle = 8 burst + 1/s)
  msgsPerMinAvg = 20,
  chatLinesMax = 6,              -- unsolicited chat lines (advice never goes to the chat)
  blockedMax = 0,                -- TAINT: actions the game would block (ADDON_ACTION_BLOCKED, « Échec des actions d'interface »)
  edgeNowPct = 25,               -- CHEAT25 « Avantages » (Edge2 ; l'ancien Edge est retiré en 4.0) : % of sampled seconds with an
  edgeDistinctMax = 12,          -- « Avantages » line in « Maintenant » ; distinct lines (text skeleton) over the session
  edgeKinstrMin = 80,            -- = cap Edge2 (était 1500 pour l'ancien Edge)
  -- SPIKE (3.9.7): per-frame peaks on the main client (one frame = timers + OnUpdate of a 0.2-s step)
  -- 3.9.6 (every C_Timer ticker in phase, all :AutoRefresh() in one frame): worst 129 / 297 (tout), p99 56 / 123,
  -- phase ratio 2.06 / 2.42 ; 3.9.7 scheduler: worst 97 / 171, p99 40 / 61, ratio ≤ 1.36
  frameLoginKinstr = 4000,       -- worst frame in the first 30 s (one-time panel layout, loading screen just gone)
  frameWorstKinstr = (opts.profile == "tout") and 210 or 115,   -- worst single frame after 30 s (kilo-instructions)
  frameP99Kinstr = (opts.profile == "tout") and 86 or 48,   -- 3.9.9 « tout »: 84 measured (3.9.8: 75) with the 10 new modules
  framePhaseRatio = 1.6,         -- busiest of the 5 frame phases / average phase (1.0 = flat; 1-Hz spikes → ≫ 1)
  allocKBps = (opts.profile == "tout") and 280 or 160,   -- KB allocated / s in frames (GC pressure)
}
-- GROUPMERGE (4.0): Squad + Missions + Team + Group = ONE « Groupe » module (Modules/Group.lua), 4 TreeAI clients:
-- one cap for all of it (3.9.9 here: Squad 231 + Missions 185 + Team 16; the audit measured ≈ 1800 on its run), and its
-- protocol (« GRP », deltas only) at most 1 message / 5 s per client outside a join / reload minute
BUDGET.kinstrModuleMin.Group = 150
BUDGET.groupMsgsPerMin = 12

local BASE = {}
for k, v in pairs(_G) do BASE[k] = v end
local unpack = unpack
math.randomseed(4242)

-- TOC -------------------------------------------------------------------------------------------
local FILES = {}
do
  local f = assert(io.open(opts.addon .. "/TreeAI.toc", "rb"), "TreeAI.toc not found in " .. opts.addon)
  for line in f:lines() do
    line = line:gsub("\r", ""):gsub("^%s+", ""):gsub("%s+$", "")
    if line ~= "" and not line:match("^#") and line:lower():match("%.lua$") then FILES[#FILES + 1] = line:gsub("\\", "/") end
  end
  f:close()
end

-- report ------------------------------------------------------------------------------------------
local R = { errors = {}, swallowed = {}, violations = {}, leaks = {}, fails = {}, notes = {} }
local function fail(msg) R.fails[#R.fails + 1] = msg end
local function note(msg) R.notes[#R.notes + 1] = msg end
local seenErr = {}
local function addErr(list, who, label, msg)
  local key = label .. "|" .. tostring(msg):sub(1, 160)
  if seenErr[key] then seenErr[key].n = seenErr[key].n + 1 return end
  local e = { who = who, label = label, msg = tostring(msg), n = 1 }
  seenErr[key] = e
  list[#list + 1] = e
end

-- serializer (WoW SavedVariables-like) -------------------------------------------------------------
local svProblems = {}
local function ser(v, path, seen, out)
  local t = type(v)
  if t == "string" then out[#out + 1] = ("%q"):format(v):gsub("\\\n", "\\n") return end
  if t == "number" then
    if v ~= v or v == math.huge or v == -math.huge then svProblems[path] = "NaN/inf" out[#out + 1] = "0" return end
    out[#out + 1] = (v == math.floor(v) and v < 1e15 and v > -1e15) and ("%d"):format(v) or ("%.17g"):format(v) return
  end
  if t == "boolean" then out[#out + 1] = tostring(v) return end
  if t ~= "table" then svProblems[path] = t out[#out + 1] = "nil" return end
  if seen[v] then svProblems[path] = "cycle/shared table" out[#out + 1] = "nil" return end
  seen[v] = true
  local keys = {}
  for k in pairs(v) do
    local tk = type(k)
    if tk == "string" or tk == "number" or tk == "boolean" then keys[#keys + 1] = k else svProblems[path] = tk .. " key" end
  end
  table.sort(keys, function(a, b)
    if type(a) == type(b) and type(a) ~= "boolean" then return a < b end
    return type(a) < type(b)
  end)
  out[#out + 1] = "{"
  for _, k in ipairs(keys) do
    local val = v[k]
    local tv = type(val)
    if tv == "function" or tv == "userdata" or tv == "thread" then
      svProblems[path .. "." .. tostring(k)] = tv
    else
      out[#out + 1] = type(k) == "string" and ("[%q]="):format(k) or ("[" .. tostring(k) .. "]=")
      ser(val, path .. "." .. tostring(k), seen, out)
      out[#out + 1] = ","
    end
  end
  out[#out + 1] = "}"
  seen[v] = nil
end
local function serialize(name, v)
  local out = { name, " = " }
  ser(v, name, {}, out)
  out[#out + 1] = "\n"
  return table.concat(out)
end

-- addon-reachable memory (tables, strings, upvalues of addon functions) -------------------------------
local function deepSize(root, stop)
  local seen, bytes, stack = {}, 0, { root }
  while #stack > 0 do
    local v = table.remove(stack)
    local t = type(v)
    if (t == "table" or t == "function" or t == "string") and not seen[v] and not stop[v] then
      seen[v] = true
      if t == "string" then
        bytes = bytes + 24 + #v
      elseif t == "table" then
        if rawget(v, "__kind") == nil then   -- mock widgets: the game owns them
          bytes = bytes + 56
          for k, x in next, v do bytes = bytes + 40; stack[#stack + 1] = k; stack[#stack + 1] = x end
        end
      else
        local info = debug.getinfo(v, "S")
        if info.what == "Lua" and info.source:find("TreeAI", 1, true) then
          bytes = bytes + 40
          local i = 1
          while true do
            local n, x = debug.getupvalue(v, i)
            if not n then break end
            stack[#stack + 1] = x
            i = i + 1
          end
        end
      end
    end
  end
  return bytes
end

-- CPU profiler: count hook, innermost addon file wins -------------------------------------------
local CUR = "sim"            -- client being driven
local MINUTE = 0
local cpu = {}               -- cpu[minute][client][module] = samples (1 sample = 1000 VM instructions)
local srcMod = {}
local function modOf(src)
  local m = srcMod[src]
  if m == nil then
    m = src:find("TreeAI", 1, true) and src:match("([%w_]+)%.lua$") or false
    if m and src:find("/Data/", 1, true) then m = "Data" .. m end
    srcMod[src] = m
  end
  return m
end
local getinfo = debug.getinfo
-- SPIKE: per-frame cost on the main client (one frame = timers + OnUpdate of one wowmock Advance step,
-- 0.2 s on the main client). FR.acc = samples (×1000 instr) in the open frame; FR.mods = by Module:line.
local FR = { open = false, acc = 0, mods = {}, jobs = {}, fjobs = {}, costs = {}, phase = {}, worst = {}, idx = 0, kbStart = 0, kb = {} }
local fine = {}             -- --fine: samples by « Module:line » of the innermost addon function (main client)
local function hook()
  local lvl, m = 2, nil
  while true do
    local info = getinfo(lvl, "S")
    if not info then break end
    m = modOf(info.source)
    if m then
      if opts.fine and CUR == "Arbrelune" then
        local k = m .. ":" .. info.linedefined
        -- + the first caller in another addon file (who pays for Core helpers)
        local l2 = lvl + 1
        while true do
          local i2 = getinfo(l2, "S")
          if not i2 then break end
          local m2 = modOf(i2.source)
          if m2 and m2 ~= m then k = k .. " <- " .. m2 .. ":" .. i2.linedefined break end
          l2 = l2 + 1
        end
        fine[k] = (fine[k] or 0) + 1
      end
      break
    end
    lvl = lvl + 1
  end
  if FR.open then
    FR.acc = FR.acc + 1
    local mk = m or "(mock/sim)"
    local byLine = FR.mods[mk]
    if not byLine then byLine = {}; FR.mods[mk] = byLine end
    local line = 0
    if m then local info = getinfo(lvl, "S") if info then line = info.linedefined end end
    byLine[line] = (byLine[line] or 0) + 1   -- no string building here: the hook's own work is counted / allocated
    if opts.verbose then   -- outermost non-Core addon function = the job (ticker / OnUpdate / slice) that pays
      local l, job = lvl, nil
      while true do
        local i2 = getinfo(l, "S")
        if not i2 then break end
        local m2 = modOf(i2.source)
        if m2 and (m2 ~= "Core" or not job) then job = m2 .. ":" .. i2.linedefined end
        l = l + 1
      end
      if job then FR.jobs[job] = (FR.jobs[job] or 0) + 1; FR.fjobs[job] = (FR.fjobs[job] or 0) + 1 end
    end
  end
  local byMin = cpu[MINUTE]
  if not byMin then byMin = {}; cpu[MINUTE] = byMin end
  local byCl = byMin[CUR]
  if not byCl then byCl = {}; byMin[CUR] = byCl end
  m = m or "(mock/sim)"
  byCl[m] = (byCl[m] or 0) + 1
end

-- clients -------------------------------------------------------------------------------------------
local REALM = "Royaume-Test"
local NAMES = { "Arbrelune", "Brindille", "Cendrine", "Dornival" }   -- fake names
local CLASSES = { opts.class, "PRIEST", opts.class == "WARRIOR" and "ROGUE" or "WARRIOR", opts.class == "MAGE" and "HUNTER" or "MAGE" }
local LOCAL = { HUNTER = "Chasseur", MAGE = "Mage", WARRIOR = "Guerrier", PRIEST = "Prêtre", ROGUE = "Voleur" }
local CLASS_SPELLS = {
  MAGE = { [133] = "Boule de feu", [116] = "Éclair de givre", [168] = "Armure de givre", [1459] = "Intelligence des arcanes",
    [5504] = "Invocation d'eau", [587] = "Invocation de nourriture", [2136] = "Trait de feu", [122] = "Nova de givre" },
  WARRIOR = { [78] = "Frappe héroïque", [6673] = "Cri de guerre", [772] = "Pourfendre", [100] = "Charge",
    [6343] = "Coup de tonnerre", [2687] = "Rage sanguinaire", [7384] = "Fulgurance" },
  PRIEST = { [2050] = "Soins inférieurs", [1243] = "Mot de pouvoir : Robustesse", [17] = "Mot de pouvoir : Bouclier" },
  ROGUE = { [1752] = "Attaque pernicieuse", [2098] = "Éviscération" },
}
local KEEP_OUT = { frames = 1, timers = 1, namedFrames = 1, prefixes = 1, errors = 1, violations = 1, output = 1,
  sentAddon = 1, sentChat = 1, unmockedMethods = 1, combatEvent = 1, sounds = 1 }
local PROTECTED = { "CreateMacro", "EditMacro", "DeleteMacro", "PickupMacro", "PickupSpell", "PlaceAction",
  "SetBinding", "SetBindingMacro", "SetBindingSpell", "SetBindingItem", "SetOverrideBinding", "SaveBindings",
  "ReplicateItems" }

local clients, order = {}, {}

local function newWorld(spec, carryM)
  local env = setmetatable({}, { __index = BASE })
  env._G = env
  local chunk = assert(loadfile(HERE .. "/wowmock.lua"))
  setfenv(chunk, env)
  local M = chunk()
  M.Install(opts.flavor)
  if carryM then
    for k, v in pairs(carryM) do if type(v) ~= "function" and not KEEP_OUT[k] then M[k] = v end end
  else
    local p = M.player
    p.name, p.realm, p.class, p.classLocal = spec.name, REALM, spec.class, LOCAL[spec.class] or spec.class
    p.guid = "Player-0000-" .. ("%08d"):format(spec.idx)
    p.level, p.xp, p.xpMax = spec.level, spec.xp or 20000, 26000
    if spec.class == "WARRIOR" or spec.class == "ROGUE" then p.powerType, p.power, p.powerMax = 1, 0, 100 end
    if spec.class ~= "HUNTER" then
      M.pet.exists = false
      M.inventory[0] = nil
      M.SetBagItem(1, 1, nil); M.SetBagItem(1, 2, nil)
    end
    for id, n in pairs(CLASS_SPELLS[spec.class] or {}) do M.spells[id] = { name = n, rank = "", icon = 135000 + id, castTime = 0, known = true } end
    M.pos = { mapID = 1424, x = 0.40 + spec.idx * 0.01, y = 0.45 }
  end
  -- what the real FR client defines and the mock does not
  env.LOOT_ITEM_SELF = "Vous recevez le butin : %s."
  env.LOOT_ITEM_SELF_MULTIPLE = "Vous recevez le butin : %sx%d."
  env.LOOT_ITEM_PUSHED_SELF = "Vous recevez l'objet : %s."
  env.LOOT_ITEM_PUSHED_SELF_MULTIPLE = "Vous recevez l'objet : %sx%d."
  env.COMBATLOG_XPGAIN_FIRSTPERSON = "%s meurt, vous gagnez %d points d'expérience."
  env.COMBATLOG_XPGAIN_FIRSTPERSON_UNNAMED = "Vous gagnez %d points d'expérience."
  env.ERR_QUEST_OBJECTIVE_COMPLETE_S = "%s (Terminé)"
  env.time = function(t) if t then return os.time(t) end return math.floor(M.serverTime) end
  env.date = function(fmt, t) return os.date(fmt, t or math.floor(M.serverTime)) end
  env.UnitOnTaxi = function(u) return (u == "player" and M.onTaxi) and true or false end
  local isDead = env.UnitIsDead
  env.UnitIsGhost = function(u) return (u == "player" and M.player.ghost) and true or false end
  env.UnitIsDeadOrGhost = function(u) return isDead(u) or env.UnitIsGhost(u) end
  -- protected / player-action-only calls: a click or a typed command (hardware event) is required
  for _, fn in ipairs(PROTECTED) do
    local orig = rawget(env, fn)
    if type(orig) == "function" then
      env[fn] = function(...)
        if not M.hardware then M.Violation("protected", fn .. " outside a player action (click / command)") end
        return orig(...)
      end
    end
  end
  local runSlash = M.RunSlash
  M.RunSlash = function(line)
    local hw = M.hardware
    M.hardware, M.hwCalls = true, 0
    local r = runSlash(line)
    M.hardware = hw
    return r
  end
  return env, M
end

local function loadAddon(c, svString)
  local env, M = c.env, c.M
  -- swallowed errors: the addon wraps a lot in pcall; count every failure raised from addon code
  env.pcall = function(f, ...)
    local r = { BASE.pcall(f, ...) }
    if not r[1] then
      local msg = tostring(r[2])
      if msg:find("TreeAI", 1, true) and not msg:find("wowmock", 1, true) then addErr(R.swallowed, c.name, CUR .. "@" .. MINUTE .. "min", msg) end
    end
    return unpack(r)
  end
  local before = {}
  for k in pairs(env) do before[k] = true end
  local allowed = { TreeAI = true, TreeAIDB = true }
  setmetatable(env, { __index = BASE, __newindex = function(t, k, v)
    if not allowed[k] and not M.namedFrames[k] and type(k) == "string" and not k:match("^SLASH_") and not k:match("^BINDING_") then
      R.leaks[k] = R.leaks[k] or (debug.getinfo(2, "Sl").short_src .. ":" .. debug.getinfo(2, "Sl").currentline)
    end
    rawset(t, k, v)
  end })
  local ns = {}
  for _, rel in ipairs(FILES) do
    local ch, err = loadfile(opts.addon .. "/" .. rel)
    if not ch then addErr(R.errors, c.name, "syntax " .. rel, err) else
      setfenv(ch, env)
      M.SafeCall("load " .. rel, ch, "TreeAI", ns)
    end
  end
  if svString then
    local f = assert(loadstring(svString))
    local box = {}
    setfenv(f, box)
    f()
    rawset(env, "TreeAIDB", box.TreeAIDB)
    -- round-trip fidelity: what WoW gives back must be what was written
    local again = serialize("TreeAIDB", box.TreeAIDB)
    if again ~= svString then fail("SavedVariables round-trip changed the data (" .. #svString .. " -> " .. #again .. " bytes)") end
  end
  if not svString then   -- (3.9.5) new players keep « tout »; « essentiel » is an explicit choice
    rawset(env, "TreeAIDB", { profileV = 999, moduleProfile = (opts.profile == "tout") and "tout" or "essentiel" })
  end
  local T = rawget(env, "TreeAI")
  c.T = T
  if T then
    local debugFn = T.Debug
    T.Debug = function(self, tag, ...)
      if tag == "erreur:" or tag == "action:" then addErr(R.swallowed, c.name, "safecall " .. tostring(self._ctx or CUR) .. "@" .. MINUTE .. "min", (...)) end
      return debugFn(self, tag, ...)
    end
    local showAlert = T.ShowAlert
    if showAlert then
      T.ShowAlert = function(self, e, force)
        showAlert(self, e, force)
        local a = self.GetAlert and self:GetAlert()
        if c.main and a and a:IsShown() and e and e.text then c.alerts[#c.alerts + 1] = { t = M.now, text = e.text, id = e.id } end
      end
    end
  end
  c.sentIdx, c.outIdx = 0, #M.output
  CUR = c.name
  M.FireEvent("ADDON_LOADED", "TreeAI")
  M.FireEvent("VARIABLES_LOADED")
  M.FireEvent("SPELLS_CHANGED")
  M.FireEvent("PLAYER_LOGIN")
  M.FireEvent("PLAYER_ENTERING_WORLD", svString == nil, svString ~= nil)
  if not svString and opts.profile ~= "tout" and T and T.SetModuleProfile then T:SetModuleProfile("essentiel") end   -- the player picked it
  for k in pairs(env) do if not before[k] and not allowed[k] then
    if not (type(k) == "string" and (k:match("^SLASH_") or k:match("^BINDING_") or M.namedFrames[k])) then R.leaks[k] = R.leaks[k] or "?" end
  end end
end

local function newClient(idx)
  local spec = { idx = idx, name = NAMES[idx], class = CLASSES[idx], level = (idx == 1) and 26 or (25 + idx % 3),
    xp = 15000 + idx * 1000 }
  local c = { name = spec.name, idx = idx, main = idx == 1, alerts = {}, sends = {} }
  c.env, c.M = newWorld(spec)
  clients[c.name] = c
  order[#order + 1] = c
  return c
end

local function collect(c)
  local M = c.M
  for _, e in ipairs(M.errors) do addErr(R.errors, c.name, e.label .. "@" .. MINUTE .. "min", e.msg) end
  for _, v in ipairs(M.violations) do addErr(R.violations, c.name, v.kind, v.msg .. " @ " .. v.where) end
  for i = #M.errors, 1, -1 do M.errors[i] = nil end
  for i = #M.violations, 1, -1 do M.violations[i] = nil end
  for _, b in ipairs(M.blocked or {}) do
    c.blocked = (c.blocked or 0) + 1
    R.blockedBy = R.blockedBy or {}
    R.blockedBy[b.fn] = (R.blockedBy[b.fn] or 0) + 1
  end
  if M.blocked then for i = #M.blocked, 1, -1 do M.blocked[i] = nil end end
end

local function reload(c)
  CUR = c.name
  c.M.FireEvent("PLAYER_LEAVING_WORLD")
  c.M.FireEvent("PLAYER_LOGOUT")
  collect(c)
  local sv = serialize("TreeAIDB", rawget(c.env, "TreeAIDB"))
  local oldM = c.M
  c.env, c.M = newWorld({ idx = c.idx, name = c.name, class = oldM.player.class }, oldM)
  loadAddon(c, sv)
  c.segmentStart = MINUTE
  R.joinMin = R.joinMin or {}
  R.joinMin[MINUTE], R.joinMin[MINUTE + 1] = true, true   -- full states exchanged again (Groupe): not steady state
  return sv
end

-- the bus: addon messages go to everybody in the group (and echo to the sender, as WoW does) --------
local function pump()
  for _, c in ipairs(order) do
    local M = c.M
    while c.sentIdx < #M.sentAddon do
      c.sentIdx = c.sentIdx + 1
      local m = M.sentAddon[c.sentIdx]
      c.sends[#c.sends + 1] = M.now
      if m.msg:sub(1, 6) == "1~GRP~" then c.grp = c.grp or {}; c.grp[#c.grp + 1] = M.now end
      for _, o in ipairs(order) do
        local deliver = (m.channel ~= "WHISPER") or (tostring(m.target):match("^[^%-]+") == o.name)
        if deliver and o.M.prefixes[m.prefix] then
          CUR = o.name
          o.M.DeliverAddon(m.prefix, m.msg, m.channel, c.name .. "-" .. REALM)
        end
      end
    end
  end
end

-- metrics sampled every second on the main client --------------------------------------------------
local TOPICS = {   -- first match wins: specific before generic
  { "hearth", { "pierre de foyer", "foyer" } },
  { "aspect", { "sans bon aspect", "aucun aspect", "aspect du" } },
  { "petfeed", { "nourri.-familier", "familier.-faim", "bonheur", "mécontent", "heureux" } },
  { "petdead", { "familier.-mort", "ressusciter le familier", "familier absent", "appel du familier" } },
  { "pethp", { "familier.-pv", "familier.-%d+ ?%%", "soigne.-familier", "guérison du familier" } },
  { "ammo", { "munition", "flèche", "balle", "carquois" } },
  { "repair", { "répar", "durabilit" } },
  { "bags", { "sacs? plein", "sacs? presque", "place.-sac", "cases? libre", "sac.-libre" } },
  { "sell", { "vendre", "camelote", "objets? gris" } },
  { "train", { "entraîn", "maître", "formation", "nouveaux? sorts?" } },
  { "drink", { "boisson", "boire", "mana bas", "mana faible" } },
  { "eat", { "manger", "nourriture" } },
  { "buff", { "armure de givre", "intelligence des arcanes", "cri de guerre", "aspect d", "robustesse" } },
  { "turnin", { "à rendre", "prête à rendre", "rendre «", "rends" } },
  { "death", { "mort", "esprit", "fantôme" } },
}
local function topicOf(text)
  local s = text:lower():gsub("|c%x%x%x%x%x%x%x%x", ""):gsub("|r", "")
  for _, tp in ipairs(TOPICS) do
    for _, pat in ipairs(tp[2]) do if s:find(pat) then return tp[1] == "buff" and ("buff:" .. pat) or tp[1] end end
  end
  return nil
end
local function skeleton(text)
  return (tostring(text):gsub("|c%x%x%x%x%x%x%x%x", ""):gsub("|r", ""):gsub("[%d%.,:]+", "#"):gsub("%s+", " "))
end

-- internal jargon only (the player likes XP/min, XP/h, % and minutes: they stay)
local JARGON = { "score", "λ", "/100", "coefficient" }
local function jargonOf(text)
  local t = tostring(text or ""):gsub("|c%x%x%x%x%x%x%x%x", ""):gsub("|r", ""):gsub("|T.-|t", "")
  local low = t:lower()
  for _, w in ipairs(JARGON) do if low:find(w, 1, true) then return w end end
  return nil
end
local S = { nowChanges = {}, nowRaw = {}, lastNow = nil, dupSec = 0, samples = 0, dupEx = {}, sv = {}, mem = {},
  topicSeen = {}, nowLog = {}, jargon = {} }
local function sampleMain(c)
  local T = c.T
  if not (T and T.GetTabLines) then return end
  CUR = "(sim)"   -- the sampling itself is measurement, not addon work: kept out of the main client CPU
  local ok, lines = BASE.pcall(T.GetTabLines, T, "now")
  if not ok then addErr(R.errors, c.name, "GetTabLines", lines) return end
  local sk, topics = {}, {}
  local edge = false
  for _, e in ipairs(lines) do
    sk[#sk + 1] = skeleton(e.text)
    if tostring(e.id or ""):find("^Edge2:") then
      edge = true
      S.edgeSeen = S.edgeSeen or {}
      local ek = skeleton(e.text)
      if not S.edgeSeen[ek] then S.edgeSeen[ek] = 0 S.edgeDistinct = (S.edgeDistinct or 0) + 1 end
      S.edgeSeen[ek] = S.edgeSeen[ek] + 1
    end
    local tp = topicOf(tostring(e.text))
    if tp then
      if topics[tp] then
        S.dupSec = S.dupSec + 1
        local key = tp .. ": « " .. skeleton(topics[tp]) .. " » / « " .. skeleton(e.text) .. " »"
        S.dupEx[key] = (S.dupEx[key] or 0) + 1
        break
      end
      topics[tp] = tostring(e.text)
    end
  end
  -- SIMPLE: the quest guidance speaks plain French (no XP/min, score, λ, % outside a progress)
  -- (checked here, not with TreeAI.Plain, so the sampling never counts as addon CPU; quests tab every 30 s)
  for _, e in ipairs(lines) do
    if e.now then local w = jargonOf(e.text) if w then S.jargon[tostring(e.text)] = w end end
  end
  if S.samples % 30 == 0 then
    local okq, ql = BASE.pcall(T.GetTabLines, T, "quests")
    for _, e in ipairs(okq and ql or {}) do local w = jargonOf(e.text) if w then S.jargon[tostring(e.text)] = w end end
  end
  S.samples = S.samples + 1
  if edge then S.edgeSec = (S.edgeSec or 0) + 1 end
  local key = table.concat(sk, "\n")
  if S.lastNow ~= nil and key ~= S.lastNow then
    S.nowChanges[MINUTE] = (S.nowChanges[MINUTE] or 0) + 1
    if opts.verbose then S.nowLog[#S.nowLog + 1] = ("%5.1f  %s"):format(c.M.now / 60 - 1000 / 60, key:gsub("\n", " | ")) end
  end
  S.lastNow = key
end

-- time ----------------------------------------------------------------------------------------------
local T0
local function simSeconds() return order[1].M.now - T0 end
local FRAMES_PER_SEC = 5   -- main client Advance step 0.2 s
local function frameHook(begin)
  if begin then
    FR.acc = 0
    for _, byLine in pairs(FR.mods) do for k in pairs(byLine) do byLine[k] = nil end end
    for k in pairs(FR.fjobs) do FR.fjobs[k] = nil end
    collectgarbage("stop")
    FR.kbStart = collectgarbage("count")
    FR.open = true
    return
  end
  FR.open = false
  local kb = collectgarbage("count") - FR.kbStart
  collectgarbage("restart")
  if not T0 then return end   -- login frames: not measured
  FR.idx = FR.idx + 1
  local sec = math.floor(order[1].M.now - T0)
  FR.kb[sec] = (FR.kb[sec] or 0) + math.max(0, kb)
  local cost = FR.acc
  if sec < 30 then   -- login / first layout (loading screen just gone): measured apart
    if cost > (FR.loginWorst or 0) then FR.loginWorst = cost end
    return
  end
  FR.costs[#FR.costs + 1] = cost
  local ph = FR.idx % FRAMES_PER_SEC
  FR.phase[ph] = (FR.phase[ph] or 0) + cost
  local w = FR.worst
  if #w < 5 or cost > w[#w].cost then
    local top = {}
    for mk, byLine in pairs(FR.mods) do for line, n in pairs(byLine) do top[#top + 1] = { k = mk .. ":" .. line, n = n } end end
    local jt = {}
    for k, n in pairs(FR.fjobs) do jt[#jt + 1] = { k = k, n = n } end
    table.sort(jt, function(a, b) return a.n > b.n end)
    for i = #jt, 5, -1 do jt[i] = nil end
    table.sort(top, function(a, b) return a.n > b.n end)
    for i = #top, 7, -1 do top[i] = nil end
    w[#w + 1] = { cost = cost, t = order[1].M.now - T0, top = top, jobs = jt }
    table.sort(w, function(a, b) return a.cost > b.cost end)
    if #w > 5 then w[#w] = nil end
  end
end
local function tick(sec)
  sec = sec or 1
  for _ = 1, sec do
    for _, c in ipairs(order) do
      CUR = c.name
      if c.main then c.M.FrameHook = frameHook end
      c.M.Advance(1, c.main and 0.2 or 0.5)
    end
    pump()
    sampleMain(order[1])
    CUR = order[1].name
    local m = math.floor(simSeconds() / 60)
    if m ~= MINUTE then
      MINUTE = m
      for _, c in ipairs(order) do collect(c) end
      local main = order[1]
      local db = rawget(main.env, "TreeAIDB")
      local sv = serialize("TreeAIDB", db)
      S.sv[m] = #sv
      S.mem[m] = { seg = main.segmentStart or 0, bytes = deepSize(main.T, { [main.env] = true, [BASE] = true, [main.M] = true,
        [string] = true, [table] = true, [math] = true, [os] = true, [io] = true, [debug] = true, [coroutine] = true }) }
      for _, c in ipairs(order) do
        for _, o in ipairs(order) do  -- keep the roster in sync (levels move)
          for _, g in ipairs(c.M.group) do if g.name == o.name then g.level = o.M.player.level end end
        end
      end
    end
  end
end

-- world helpers (main client unless said) -----------------------------------------------------------
local function fire(c, ev, ...) CUR = c.name return c.M.FireEvent(ev, ...) end
local ITEMS = {
  [20001] = { name = "Croc de loup-garou", questItem = true, stack = 20, classID = 12 },
  [20002] = { name = "Défense de sanglier", sellPrice = 35, stack = 10, classID = 7 },
  [20003] = { name = "Cuir déchiré", sellPrice = 12, stack = 20, classID = 7 },
  [20004] = { name = "Pantalon usé", quality = 0, sellPrice = 41, classID = 4, equipLoc = "INVTYPE_LEGS" },
  [20005] = { name = "Lame rouillée", quality = 0, sellPrice = 55, classID = 2, equipLoc = "INVTYPE_WEAPON" },
  [20006] = { name = "Bottes de l'éclaireur", quality = 2, sellPrice = 210, classID = 4, equipLoc = "INVTYPE_FEET", ilvl = 27, minLevel = 22 },
}
local function seedItems(M)
  for id, t in pairs(ITEMS) do
    local copy = {}
    for k, v in pairs(t) do copy[k] = v end
    M.AddItem(id, copy)
  end
end
local function freeSlot(M)
  for b = 0, 4 do
    local bag = M.bags[b]
    if bag then for s = 1, bag.size do if not bag.slots[s] then return b, s end end end
  end
end
local function addToBags(M, id, n)
  for b = 0, 4 do
    local bag = M.bags[b]
    if bag then for s = 1, bag.size do
      local sl = bag.slots[s]
      if sl and sl.id == id and sl.count + n <= (M.items[id].stack or 1) then sl.count = sl.count + n return true end
    end end
  end
  local b, s = freeSlot(M)
  if not b then return false end
  M.SetBagItem(b, s, id, n)
  return true
end
local function countFree(M)
  local n = 0
  for b = 0, 4 do local bag = M.bags[b] if bag then for s = 1, bag.size do if not bag.slots[s] then n = n + 1 end end end end
  return n
end

local QUESTS = {   -- ids stable across removals (mock q.id)
  { id = 5101, title = "Les loups-garous de la forêt", level = 26, xp = 2100, mob = "Loup-garou galeux", need = 8,
    item = 20001, objective = "Croc de loup-garou" },
  { id = 5102, title = "Sangliers des collines", level = 25, xp = 1750, mob = "Sanglier des collines", need = 10 },
  { id = 5103, title = "Le marchand disparu", level = 27, xp = 2400, mob = "Bandit de Syndicat", need = 6 },
  { id = 5104, title = "Les nids d'araignées", level = 28, xp = 2900, mob = "Araignée des bois", need = 9 },
  { id = 5105, title = "Le courrier d'Alterac", level = 29, xp = 3100, mob = "Ogre d'Alterac", need = 7 },
}
local function questObj(q, have)
  local label = q.item and q.objective or (q.mob .. " tué")
  return { text = ("%s : %d/%d"):format(label, have, q.need), done = have >= q.need, have = have, need = q.need }
end
local function accept(c, qi)
  local q = QUESTS[qi]
  local M = c.M
  for _, e in ipairs(M.quests) do if e.id == q.id then return end end
  fire(c, "QUEST_DETAIL")
  M.quests[#M.quests + 1] = { id = q.id, title = q.title, level = q.level, xp = q.xp, complete = false,
    objectives = { questObj(q, 0) }, def = q }
  fire(c, "QUEST_ACCEPTED", #M.quests, q.id)
  fire(c, "QUEST_LOG_UPDATE"); fire(c, "UNIT_QUEST_LOG_CHANGED", "player"); fire(c, "QUEST_WATCH_LIST_CHANGED", q.id, true)
  fire(c, "QUEST_FINISHED")
end
local function progress(c, mobName)
  local M = c.M
  for _, e in ipairs(M.quests) do
    local q = e.def
    if q and q.mob == mobName and not e.complete then
      local have = (e.objectives[1].have or 0) + 1
      if q.item then
        addToBags(M, q.item, 1)
        fire(c, "CHAT_MSG_LOOT", (c.env.LOOT_ITEM_SELF):format(M.ItemLink(q.item)), c.name, "", "", c.name)
      end
      e.objectives[1] = questObj(q, have)
      e.complete = have >= q.need
      fire(c, "UI_INFO_MESSAGE", 0, e.objectives[1].text)
      fire(c, "QUEST_WATCH_UPDATE", e.id)
      fire(c, "QUEST_LOG_UPDATE"); fire(c, "UNIT_QUEST_LOG_CHANGED", "player")
      return e
    end
  end
end
local function turnIn(c, qid)
  local M = c.M
  for i, e in ipairs(M.quests) do
    if e.id == qid and e.complete then
      fire(c, "QUEST_PROGRESS"); fire(c, "QUEST_COMPLETE")
      if e.def and e.def.item then
        for b = 0, 4 do local bag = M.bags[b] if bag then for s = 1, bag.size do
          if bag.slots[s] and bag.slots[s].id == e.def.item then bag.slots[s] = nil end end end end
      end
      table.remove(M.quests, i)
      local xp = e.xp or 900
      M.player.xp = M.player.xp + xp
      M.player.money = M.player.money + 1500
      fire(c, "QUEST_TURNED_IN", qid, xp, 1500)
      fire(c, "QUEST_REMOVED", qid)
      fire(c, "QUEST_LOG_UPDATE"); fire(c, "UNIT_QUEST_LOG_CHANGED", "player")
      fire(c, "PLAYER_XP_UPDATE", "player"); fire(c, "PLAYER_MONEY")
      fire(c, "BAG_UPDATE", 0); fire(c, "BAG_UPDATE_DELAYED")
      fire(c, "QUEST_FINISHED")
      return true
    end
  end
end
local function levelCheck(c)
  local p = c.M.player
  if p.xp >= p.xpMax then
    p.xp, p.level = p.xp - p.xpMax, p.level + 1
    p.xpMax = p.xpMax + 1500
    fire(c, "PLAYER_LEVEL_UP", p.level, 120, 40, 1, 1, 0, 1, 0, 0)
    fire(c, "PLAYER_XP_UPDATE", "player")
    fire(c, "CHARACTER_POINTS_CHANGED", 1)
    fire(c, "UNIT_LEVEL", "player")
    return true
  end
end
local function killXP(c, mob, xp)
  local p = c.M.player
  local rested = math.min(p.rested or 0, xp)
  p.rested = (p.rested or 0) - rested
  local msg = (c.env.COMBATLOG_XPGAIN_FIRSTPERSON):format(mob, xp + rested)
  if rested > 0 then msg = ("%s meurt, vous gagnez %d points d'expérience. (+%d exp. bonus de repos)"):format(mob, xp + rested, rested) end
  p.xp = p.xp + xp + rested
  fire(c, "CHAT_MSG_COMBAT_XP_GAIN", msg)
  fire(c, "PLAYER_XP_UPDATE", "player")
  fire(c, "UPDATE_EXHAUSTION")
  levelCheck(c)
end

local mobSeq = 0
local function fight(c, mob, o)
  o = o or {}
  local M, cls = c.M, c.M.player.class
  mobSeq = mobSeq + 1
  M.target = { exists = true, name = mob, level = 26 + (mobSeq % 3) - 1, health = 900, healthMax = 900, hostile = true,
    classification = o.elite and "elite" or "normal", creatureType = "Bête", guid = ("Creature-0-0-0-0-%d-%08d"):format(3000 + mobSeq % 7, mobSeq) }
  M.units.nameplate1 = { name = mob, guid = M.target.guid, hostile = true, classification = "normal", level = M.target.level, health = 900, healthMax = 900 }
  fire(c, "PLAYER_TARGET_CHANGED"); fire(c, "UNIT_TARGET", "player"); fire(c, "NAME_PLATE_UNIT_ADDED", "nameplate1")
  if o.inCombatHook then o.inCombatHook("before") end
  M.EnterCombat()
  local rounds = o.rounds or 4
  for r = 1, rounds do
    if cls == "HUNTER" then
      if r == 1 then M.autoRepeat = true; fire(c, "START_AUTOREPEAT_SPELL") end
      fire(c, "UNIT_SPELLCAST_SUCCEEDED", "player", "Cast-3-0-0-0-75-" .. mobSeq .. r, 75)
      local inv0 = M.inventory[0]
      if inv0 then inv0.count = math.max(0, (inv0.count or 0) - 3) end
      local sl = M.bags[1] and M.bags[1].slots[1]
      if sl then sl.count = math.max(1, sl.count - 3) end
      fire(c, "UNIT_INVENTORY_CHANGED", "player")
      if M.pet.exists and not M.pet.dead then M.pet.health = math.max(1, M.pet.health - 60); fire(c, "UNIT_HEALTH", "pet") end
    elseif cls == "MAGE" then
      fire(c, "UNIT_SPELLCAST_START", "player", "Cast-m" .. mobSeq .. r, 133)
      tick(1)
      M.player.power = math.max(0, M.player.power - 45)
      fire(c, "UNIT_SPELLCAST_SUCCEEDED", "player", "Cast-m" .. mobSeq .. r, 133)
      fire(c, "UNIT_SPELLCAST_STOP", "player", "Cast-m" .. mobSeq .. r, 133)
      fire(c, "UNIT_POWER_UPDATE", "player", "MANA")
    else
      M.player.power = math.min(100, M.player.power + 15)
      fire(c, "UNIT_POWER_UPDATE", "player", "RAGE")
      if r % 2 == 0 then fire(c, "UNIT_SPELLCAST_SUCCEEDED", "player", "Cast-w" .. mobSeq .. r, 78) end
    end
    M.player.health = math.max(1, M.player.health - (o.hit or 70))
    fire(c, "UNIT_HEALTH", "player"); fire(c, "UNIT_COMBAT", "player", "WOUND", "", 70, 1)
    M.target.health = math.max(0, math.floor(900 * (1 - r / rounds)))
    M.units.nameplate1.health = M.target.health
    fire(c, "UNIT_HEALTH", "target")
    fire(c, "UNIT_THREAT_LIST_UPDATE", "target")
    if r == 2 then   -- TAINT: a second mob joins, target switch and back, quest loot lands in the bags, in combat
      M.units.nameplate2 = { name = mob, guid = M.target.guid .. "b", hostile = true, classification = "normal", level = M.target.level, health = 900, healthMax = 900 }
      fire(c, "NAME_PLATE_UNIT_ADDED", "nameplate2"); fire(c, "PLAYER_TARGET_CHANGED")
      fire(c, "BAG_UPDATE", 0); fire(c, "BAG_UPDATE_DELAYED")
      if mobSeq % 4 == 0 then   -- PVPMARK: world PvP while questing, an enemy player shows up in combat
        M.units.nameplate3 = { name = "Ennemi Sim", isPlayer = true, hostile = true, class = "ROGUE", level = 29,
          guid = "Player-0-SIM" .. mobSeq, targetsPlayer = mobSeq % 8 == 0 }
        fire(c, "NAME_PLATE_UNIT_ADDED", "nameplate3"); fire(c, "UNIT_TARGET", "nameplate3")
      end
    elseif r == 3 then
      fire(c, "NAME_PLATE_UNIT_REMOVED", "nameplate2"); M.units.nameplate2 = nil
      if M.units.nameplate3 then fire(c, "NAME_PLATE_UNIT_REMOVED", "nameplate3"); M.units.nameplate3 = nil end
      fire(c, "PLAYER_TARGET_CHANGED")
    end
    if o.inCombatHook then o.inCombatHook(r) end
    if o.die and r == rounds - 1 then return "died" end
    tick(2)
  end
  M.target.dead = true
  fire(c, "UNIT_HEALTH", "target")
  killXP(c, mob, o.xp or 380)
  local qe = progress(c, mob)
  if cls == "HUNTER" then M.autoRepeat = false; fire(c, "STOP_AUTOREPEAT_SPELL") end
  M.LeaveCombat()
  fire(c, "NAME_PLATE_UNIT_REMOVED", "nameplate1"); M.units.nameplate1 = nil
  -- loot
  M.loot = { { name = ITEMS[20003].name, count = 1 } }
  if mobSeq % 3 == 0 then M.loot[2] = { name = ITEMS[20004 + mobSeq % 2].name, count = 1 } end
  fire(c, "LOOT_OPENED", false)
  local ids = { 20003, mobSeq % 3 == 0 and (20004 + mobSeq % 2) or nil }
  for i, id in ipairs(ids) do
    if addToBags(M, id, 1) then
      fire(c, "CHAT_MSG_LOOT", (c.env.LOOT_ITEM_SELF):format(M.ItemLink(id)), c.name, "", "", c.name)
    else
      fire(c, "UI_ERROR_MESSAGE", 3, "Vos sacs sont pleins.")
    end
    fire(c, "LOOT_SLOT_CLEARED", i)
  end
  M.loot = nil
  fire(c, "LOOT_CLOSED")
  fire(c, "BAG_UPDATE", 0); fire(c, "BAG_UPDATE_DELAYED")
  M.money = nil
  M.player.money = M.player.money + 37
  fire(c, "PLAYER_MONEY")
  if mobSeq % 4 == 0 then
    for _, it in pairs(M.inventory) do if it.durMax then it.dur = math.max(0, it.dur - 1) end end
    fire(c, "UPDATE_INVENTORY_DURABILITY")
  end
  M.target = { exists = false }
  fire(c, "PLAYER_TARGET_CHANGED")
  return qe
end

local function restUp(c, sec)
  local M = c.M
  local drink = M.player.powerType == 0 and M.player.power < M.player.powerMax * 0.6
  local aura = { name = drink and "Boisson" or "Nourriture", spellId = drink and 430 or 433, duration = 18, expirationTime = M.now + 18, kind = "buff" }
  table.insert(M.auras.player, aura)
  fire(c, "UNIT_AURA", "player")
  local steps = math.max(1, math.floor(sec / 3))
  for _ = 1, steps do
    M.player.health = math.min(M.player.healthMax, M.player.health + 120)
    if M.player.powerType == 0 then M.player.power = math.min(M.player.powerMax, M.player.power + 90) end
    fire(c, "UNIT_HEALTH", "player"); fire(c, "UNIT_POWER_UPDATE", "player", "MANA")
    tick(3)
  end
  for i = #M.auras.player, 1, -1 do if M.auras.player[i] == aura then table.remove(M.auras.player, i) end end
  fire(c, "UNIT_AURA", "player")
  if M.pet.exists and not M.pet.dead then M.pet.health = M.pet.healthMax; fire(c, "UNIT_HEALTH", "pet") end
end

local function setZone(c, zone, sub, mapID, x, y)
  local M = c.M
  M.player.zone, M.player.subzone = zone, sub
  M.pos = { mapID = mapID, x = x, y = y }
  fire(c, "ZONE_CHANGED_NEW_AREA"); fire(c, "ZONE_CHANGED"); fire(c, "ZONE_CHANGED_INDOORS")
end

local function clickPanel(c, which)
  local T = c.T
  if not (T and T.GetPanel) then return end
  local panel = T:GetPanel()
  if not panel then return end
  CUR = c.name
  local rows = {}
  for _, ch in ipairs({ panel:GetChildren() }) do
    -- (LITE) the one-time « Tout réactiver » notice is left alone: the run measures the profile it was asked for
    if ch.entry and ch:IsShown() and ch.__scripts.OnClick and ch.entry.id ~= "core:lite" then rows[#rows + 1] = ch end
  end
  local r = rows[which or 1]
  if r then
    c.M.Script(r, "OnEnter")
    r:Click("LeftButton")
    c.M.Script(r, "OnLeave")
  end
end

-- toolbar: every icon clicked twice (toggle + back), as a player exploring the panel would
local function clickToolbar(c)
  local T = c.T
  if not (T and T.GetPanel and T:GetPanel()) then return end
  CUR = c.name
  for _, ch in ipairs({ T:GetPanel():GetChildren() }) do
    if ch.tex and not ch.entry and not ch.id and ch.__scripts.OnClick then
      c.M.Script(ch, "OnEnter")
      ch:Click("LeftButton"); tick(1); ch:Click("LeftButton")
      c.M.Script(ch, "OnLeave")
      T:ShowPanel()
    end
  end
end

local function refreshTabs(c)
  local T = c.T
  if not (T and T.GetTabs and T.SelectTab) then return end
  CUR = c.name
  T:ShowPanel()
  if T.IsMini and T:IsMini() then T:SetMini(false) end
  local tabBtn = {}
  for _, ch in ipairs({ T:GetPanel():GetChildren() }) do
    if ch.id and not ch.entry and ch.__scripts.OnClick then tabBtn[ch.id] = ch end
  end
  for _, t in ipairs(T:GetTabs()) do
    if tabBtn[t.id] then tabBtn[t.id]:Click("LeftButton") else T:SelectTab(t.id) end   -- the player clicks the tab
    if T:GetActiveTab() ~= t.id then fail("clic sur l'onglet " .. t.id .. " sans effet") end
    tick(1)
    local vis = T.GetVisibleLines and T:GetVisibleLines() or {}
    for _, e in ipairs(vis) do
      if type(e.text) ~= "string" or e.text == "" then fail("tab " .. t.id .. ": empty line") end
      if e.text:find("nil", 1, true) and e.text:find("%f[%a]nil%f[%A]") then fail("tab " .. t.id .. ": « nil » shown: " .. e.text) end
    end
  end
  T:SelectTab("now")
end

-- friends: lighter life (kills, quest progress, rests) so the group sync has real changes ---------------
local function friendLife(sec)
  for i = 2, #order do
    local f = order[i]
    if (sec + i * 17) % 75 == 0 then
      local e = f.M.quests[1 + (sec % 2)] or f.M.quests[1]
      local mob = e and e.def and e.def.mob or "Sanglier des collines"
      CUR = f.name
      f.M.EnterCombat()
      f.M.player.health = math.max(200, f.M.player.health - 250)
      fire(f, "UNIT_HEALTH", "player")
      f.M.LeaveCombat()
      killXP(f, mob, 360)
      progress(f, mob)
    end
    if (sec + i * 29) % 240 == 0 then
      f.M.player.health = f.M.player.healthMax
      fire(f, "UNIT_HEALTH", "player")
      for _, e in ipairs(f.M.quests) do if e.complete then turnIn(f, e.id) break end end
    end
  end
end

-- ===================================================================================================
-- the session
-- ===================================================================================================
local t0clock = os.clock()
for i = 1, 4 do newClient(i) end
local roster = {}
for _, c in ipairs(order) do roster[#roster + 1] = { name = c.name, class = c.M.player.class, level = c.M.player.level } end
for _, c in ipairs(order) do
  seedItems(c.M)
  c.M.group = {}
  for _, r in ipairs(roster) do
    if r.name ~= c.name then
      c.M.group[#c.M.group + 1] = { name = r.name, class = r.class, level = r.level, health = 800, healthMax = 900,
        online = true, guid = "Player-0000-" .. r.name }
    end
  end
end
debug.sethook(hook, "", 1000)
for _, c in ipairs(order) do loadAddon(c) end
T0 = order[1].M.now
local main = order[1]
local M0 = function() return main.M end
for _, c in ipairs(order) do fire(c, "GROUP_ROSTER_UPDATE"); fire(c, "PARTY_MEMBERS_CHANGED") end
for _, c in ipairs(order) do for qi = 1, 3 do accept(c, qi) end end
for i = 2, #order do for qi = 1, 3 do accept(order[i], qi) end end
tick(5)

local events = {}   -- [second] = fn
local function at(min, fn) events[#events + 1] = { t = math.floor(min * 60), fn = fn } end
local MOBS = { "Loup-garou galeux", "Sanglier des collines", "Bandit de Syndicat" }
local ZONE1 = { "Contreforts de Hautebrande", "Moulin-de-Tarren", 1424 }
local TOWN = { "Contreforts de Hautebrande", "Moulin-de-Tarren", 1424, 0.62, 0.20 }
local ZONE2 = { "Montagnes d'Alterac", "Ruines d'Alterac", 1416 }
local questing = { from = 0.2, to = 14.5 }
local questing2 = { from = 23, to = 54 }

at(1.5, function()   -- the player answers the buff reminders (long buffs only)
  local B = { MAGE = { { 168, "Armure de givre" }, { 1459, "Intelligence des arcanes" } } }
  for _, b in ipairs(B[main.M.player.class] or {}) do
    fire(main, "UNIT_SPELLCAST_SUCCEEDED", "player", "Cast-buff" .. b[1], b[1])
    table.insert(main.M.auras.player, { name = b[2], spellId = b[1], duration = 1800, expirationTime = main.M.now + 1800, kind = "buff" })
  end
  fire(main, "UNIT_AURA", "player")
end)
at(6, function() main.M.pet.happiness = 2; fire(main, "UNIT_HAPPINESS", "pet"); fire(main, "PET_UI_UPDATE") end)
at(13, function() main.M.pet.happiness = 1; fire(main, "UNIT_HAPPINESS", "pet") end)
at(13.5, function()   -- feed the pet (hunter) / buff up (others), out of combat
  if main.M.pet.exists then
    fire(main, "UNIT_SPELLCAST_SUCCEEDED", "player", "Cast-feed", 6991)
    main.M.pet.happiness = 3; fire(main, "UNIT_HAPPINESS", "pet")
  end
end)
at(15, function()   -- hearthstone
  fire(main, "UNIT_SPELLCAST_START", "player", "Cast-hs", 8690)
  tick(10)
  fire(main, "UNIT_SPELLCAST_SUCCEEDED", "player", "Cast-hs", 8690)
  fire(main, "UNIT_SPELLCAST_STOP", "player", "Cast-hs", 8690)
  setZone(main, TOWN[1], TOWN[2], TOWN[3], TOWN[4], TOWN[5])
  main.M.player.resting = true
  fire(main, "PLAYER_UPDATE_RESTING")
end)
at(15.5, function()   -- turn in what is ready
  for _, e in ipairs({ unpack(main.M.quests) }) do if e.complete then turnIn(main, e.id) end end
end)
at(16, function()   -- trainer
  fire(main, "TRAINER_SHOW"); tick(4)
  fire(main, "LEARNED_SPELL_IN_TAB", 1130, 2); fire(main, "SPELLS_CHANGED")
  main.M.player.money = main.M.player.money - 4200; fire(main, "PLAYER_MONEY")
  fire(main, "TRAINER_CLOSED")
end)
at(17, function()   -- vendor: the player clicks what the panel offers, then repairs
  local M = main.M
  M.SetMerchant(true)
  tick(2)
  refreshTabs(main)
  clickPanel(main, 1)
  for b = 0, 4 do local bag = M.bags[b] if bag then for s = 1, bag.size do
    local sl = bag.slots[s]
    if sl and M.items[sl.id] and M.items[sl.id].quality == 0 then bag.slots[s] = nil; M.player.money = M.player.money + 50 end
  end end end
  fire(main, "BAG_UPDATE", 0); fire(main, "BAG_UPDATE_DELAYED"); fire(main, "PLAYER_MONEY")
  for _, it in pairs(M.inventory) do if it.durMax then it.dur = it.durMax end end
  fire(main, "UPDATE_INVENTORY_DURABILITY")
  if M.inventory[0] then M.inventory[0].count = 1000; addToBags(M, 2515, 200); fire(main, "UNIT_INVENTORY_CHANGED", "player") end
  tick(3)
  M.SetMerchant(false)
end)
at(18, function()   -- auction house
  local M = main.M
  M.SetAuctionHouse(true)
  tick(20)
  M.RunSlash("/tai hv")
  tick(20)
  M.SetAuctionHouse(false)
end)
at(19, function()   -- /reload #1 (tab choice must survive)
  main.T:SelectTab("group")
  S.svReload1 = #reload(main)
  local pc = main.T.PanelChar and main.T:PanelChar()
  if not (pc and pc.tab == "group") then fail("/reload: the selected tab was not kept (" .. tostring(pc and pc.tab) .. ")") end
  main.T:SelectTab("now")
  for _, c in ipairs(order) do if c ~= main then fire(c, "GROUP_ROSTER_UPDATE") end end
end)
-- CONTENTFIX: dead / ghost / on a taxi, « Maintenant » only carries death lines (corpse run, recap…);
-- never two macro lines; never « 0 dégâts » in a death recap
local function quietCheck(c, label)
  local T = c.T
  if not (T.QuietKindOf and T.Quiet) then return end
  for _, e in ipairs(T:GetTabLines("now")) do
    local k = T:QuietKindOf(e)
    if k ~= "death" and k ~= "always" then fail(label .. ": « " .. tostring(e.text) .. " » shown") end
  end
end
local function macroCheck(c, label)
  local n = 0
  for _, e in ipairs(c.T:GetSuggestions()) do if tostring(e.text):lower():find("macro") then n = n + 1 end end
  if n > 1 then fail(label .. ": " .. n .. " macro lines") end
end
at(20, function()   -- flight to Alterac
  fire(main, "TAXIMAP_OPENED", 1); tick(3); fire(main, "TAXIMAP_CLOSED")
  main.M.player.resting = false; fire(main, "PLAYER_UPDATE_RESTING")
  main.M.onTaxi = true
  fire(main, "PLAYER_CONTROL_LOST")
  for k = 1, 12 do
    main.M.pos = { mapID = 1424, x = 0.62 - k * 0.015, y = 0.20 - k * 0.01 }
    main.M.speed = 30
    tick(10)
    quietCheck(main, "on a taxi")
  end
  macroCheck(main, "after the flight")
  main.M.onTaxi, main.M.speed = false, 0
  setZone(main, ZONE2[1], ZONE2[2], ZONE2[3], 0.55, 0.60)
  fire(main, "PLAYER_CONTROL_GAINED")
  for qi = 4, 5 do accept(main, qi) end
  for i = 2, #order do for qi = 4, 5 do accept(order[i], qi) end setZone(order[i], ZONE2[1], ZONE2[2], ZONE2[3], 0.55 + i * 0.01, 0.6) end
end)
at(31, function()   -- a death (pull gone wrong), ghost run, durability loss
  local M = main.M
  local r = fight(main, "Ogre d'Alterac", { elite = true, hit = 260, die = true })
  M.player.health, M.player.dead = 0, true
  fire(main, "UNIT_HEALTH", "player"); fire(main, "PLAYER_DEAD")
  M.LeaveCombat()
  if M.pet.exists then M.pet.dead = true; M.pet.health = 0; fire(main, "UNIT_PET", "player") end
  tick(5)
  quietCheck(main, "dead")
  local d = main.T._suggestions and main.T._suggestions["Fight:death"]
  if d and tostring(d.text):find("%f[%d]0 dégâts") then fail("death recap shows « 0 dégâts »: " .. d.text) end
  M.player.dead, M.player.ghost = false, true
  fire(main, "PLAYER_ALIVE")
  for _ = 1, 6 do M.speed = 7; tick(10); quietCheck(main, "ghost run") end
  macroCheck(main, "after the death")
  M.player.ghost, M.player.health, M.speed = false, M.player.healthMax * 0.5, 0
  fire(main, "PLAYER_UNGHOST"); fire(main, "PLAYER_ALIVE"); fire(main, "UNIT_HEALTH", "player")
  for _, it in pairs(M.inventory) do if it.durMax then it.dur = math.max(0, math.floor(it.dur - it.durMax * 0.1)) end end
  fire(main, "UPDATE_INVENTORY_DURABILITY")
  M.target = { exists = false }; fire(main, "PLAYER_TARGET_CHANGED")
  return r
end)
at(32.5, function()   -- pet back (hunter)
  if main.M.pet.exists then
    fire(main, "UNIT_SPELLCAST_START", "player", "Cast-rez", 982); tick(4)
    main.M.pet.dead, main.M.pet.health = false, 300
    fire(main, "UNIT_SPELLCAST_SUCCEEDED", "player", "Cast-rez", 982); fire(main, "UNIT_PET", "player"); fire(main, "PET_BAR_UPDATE")
  end
  restUp(main, 18)
end)
at(36, function()   -- bags full while questing
  local M = main.M
  while freeSlot(M) do local b, s = freeSlot(M); M.SetBagItem(b, s, 20002, 1) end
  fire(main, "BAG_UPDATE", 1); fire(main, "BAG_UPDATE_DELAYED")
  fire(main, "UI_ERROR_MESSAGE", 3, "Vos sacs sont pleins.")
end)
at(40, function()   -- a click on a panel line DURING combat must not do anything protected
  fight(main, "Araignée des bois", { inCombatHook = function(r) if r == 2 then clickPanel(main, 1); clickToolbar(main) end end })
end)
at(45, function() S.svReload2 = #reload(main) end)
at(47, function()   -- vendor again: sell the boar tusks, repair
  local M = main.M
  M.SetMerchant(true); tick(2)
  for b = 0, 4 do local bag = M.bags[b] if bag then for s = 1, bag.size do
    local sl = bag.slots[s]
    if sl and (sl.id == 20002 or (M.items[sl.id] and M.items[sl.id].quality == 0)) then bag.slots[s] = nil end
  end end end
  fire(main, "BAG_UPDATE", 0); fire(main, "BAG_UPDATE_DELAYED")
  for _, it in pairs(M.inventory) do if it.durMax then it.dur = it.durMax end end
  fire(main, "UPDATE_INVENTORY_DURABILITY")
  tick(2); M.SetMerchant(false)
end)
at(55, function()   -- back in town: turn in everything, a last tab tour
  setZone(main, TOWN[1], TOWN[2], TOWN[3], TOWN[4], TOWN[5])
  for _, e in ipairs({ unpack(main.M.quests) }) do if e.complete then turnIn(main, e.id) end end
  main.M.player.resting = true; fire(main, "PLAYER_UPDATE_RESTING")
end)
for m = 2, 58, 7 do at(m + 0.5, function() refreshTabs(main) end) end
for m = 4, 58, 11 do at(m + 0.3, function() clickToolbar(main) end) end
for m = 3, 58, 9 do at(m + 0.7, function() if not main.M.inCombat then clickPanel(main, 2) end end) end
table.sort(events, function(a, b) return a.t < b.t end)

local nextFight, fightN, ev = 20, 0, 1
local total = opts.minutes * 60
while simSeconds() < total do
  local s = math.floor(simSeconds())
  if events[ev] and s >= events[ev].t then
    local e = events[ev]; ev = ev + 1
    CUR = main.name
    local ok, err = BASE.pcall(e.fn)
    if not ok then addErr(R.errors, "sim", "timeline@" .. e.t, err) end
  else
    local mins = s / 60
    local q1 = mins >= questing.from and mins < questing.to
    local q2 = mins >= questing2.from and mins < questing2.to
    if (q1 or q2) and s >= nextFight then
      fightN = fightN + 1
      local mob = q2 and (fightN % 2 == 0 and "Araignée des bois" or "Ogre d'Alterac") or MOBS[1 + fightN % 3]
      BASE.pcall(function() fight(main, mob, { xp = 360 + (fightN % 5) * 10 }) end)
      if main.M.player.health < main.M.player.healthMax * 0.5 or (main.M.player.powerType == 0 and main.M.player.power < 200) then
        restUp(main, 15)
      end
      nextFight = math.floor(simSeconds()) + 25 + (fightN % 4) * 6
    else
      friendLife(s)
      tick(1)
    end
  end
end
for _, c in ipairs(order) do fire(c, "PLAYER_LEAVING_WORLD"); fire(c, "PLAYER_LOGOUT"); collect(c) end
debug.sethook()
local svFinal = serialize("TreeAIDB", rawget(main.env, "TreeAIDB"))
local elapsed = os.clock() - t0clock

-- ===================================================================================================
-- verdict
-- ===================================================================================================
local out = {}
local function P(s) out[#out + 1] = s end
P(("== session_sim class=%s flavor=%s profile=%s minutes=%d clients=%d (cpu %.1fs)"):format(opts.class, opts.flavor, opts.profile or "essentiel", opts.minutes, #order, elapsed))

-- CPU per module (main client)
local perMod, peak, peakMin, totalMain = {}, {}, {}, 0
local nMin = 0
for m, byCl in pairs(cpu) do
  if m >= 1 and m < opts.minutes then
    nMin = nMin + 1
    for mod, n in pairs(byCl[main.name] or {}) do
      perMod[mod] = (perMod[mod] or 0) + n
      if mod ~= "(mock/sim)" then totalMain = totalMain + n end
      if n > (peak[mod] or 0) then peak[mod], peakMin[mod] = n, m end
    end
  end
end
nMin = math.max(1, nMin)
local mods = {}
for mod in pairs(perMod) do mods[#mods + 1] = mod end
table.sort(mods, function(a, b) return perMod[a] > perMod[b] end)
P("cpu (main client, kilo-instructions / minute: avg, peak@min):")
for i, mod in ipairs(mods) do
  local avg = perMod[mod] / nMin
  local cap = BUDGET.kinstrModuleMin[mod]
  if i <= 12 or opts.verbose or cap then P(("  %-14s %7.0f  %7d@%d"):format(mod, avg, peak[mod], peakMin[mod])) end
  if cap and avg > cap then fail(("cpu: %s %.0f kinstr/min > %d"):format(mod, avg, cap)) end
  if mod ~= "(mock/sim)" then
    if avg > BUDGET.kinstrPerModuleMin then fail(("cpu: %s %.0f kinstr/min > %d"):format(mod, avg, BUDGET.kinstrPerModuleMin)) end
    if peak[mod] > BUDGET.kinstrPerModulePeak then fail(("cpu: %s peak %d kinstr in minute %d > %d"):format(mod, peak[mod], peakMin[mod], BUDGET.kinstrPerModulePeak)) end
  end
end
P(("  Stats        %7.1f kinstr/min (budget %d)"):format((perMod.Stats or 0) / nMin, BUDGET.kinstrStatsMin))
if (perMod.Stats or 0) / nMin > BUDGET.kinstrStatsMin then
  fail(("cpu: Stats %.1f kinstr/min > %d"):format((perMod.Stats or 0) / nMin, BUDGET.kinstrStatsMin))
end
P(("  total addon  %7.0f kinstr/min"):format(totalMain / nMin))
-- SPIKE: per-frame peaks (hitches) + allocation rate, main client
do
  local c = {}
  for i, v in ipairs(FR.costs) do c[i] = v end
  table.sort(c)
  local n = #c
  local function pct(p) return n > 0 and c[math.max(1, math.ceil(n * p))] or 0 end
  local sum = 0
  for _, v in ipairs(c) do sum = sum + v end
  local mean = n > 0 and sum / n or 0
  local phMax, phSum = 0, 0
  for ph = 0, FRAMES_PER_SEC - 1 do
    local v = FR.phase[ph] or 0
    phSum = phSum + v
    if v > phMax then phMax = v end
  end
  local phRatio = phSum > 0 and phMax / (phSum / FRAMES_PER_SEC) or 0
  local kbs, kbMax, nk = 0, 0, 0
  for sec, kb in pairs(FR.kb) do if sec >= 60 then kbs = kbs + kb nk = nk + 1 if kb > kbMax then kbMax = kb end end end
  local kbAvg = nk > 0 and kbs / nk or 0
  P(("login (first 30 s): worst frame %d kinstr (budget %d)"):format(FR.loginWorst or 0, BUDGET.frameLoginKinstr))
  if (FR.loginWorst or 0) > BUDGET.frameLoginKinstr then fail(("hitch: login frame %d kinstr > %d"):format(FR.loginWorst, BUDGET.frameLoginKinstr)) end
  P(("frames: %d  worst %d kinstr  p99 %d  p50 %d  mean %.1f  1-Hz phase ratio %.2f  alloc %.1f KB/s (max %.0f KB in 1 s)")
    :format(n, c[n] or 0, pct(0.99), pct(0.5), mean, phRatio, kbAvg, kbMax))
  if opts.verbose then
    local js = {}
    for k, v in pairs(FR.jobs) do js[#js + 1] = { k = k, n = v } end
    table.sort(js, function(a, b) return a.n > b.n end)
    for i = 1, math.min(25, #js) do P(("  job %-24s %6d kinstr total"):format(js[i].k, js[i].n)) end
    local ph = {}
    for p = 0, FRAMES_PER_SEC - 1 do ph[#ph + 1] = tostring(FR.phase[p] or 0) end
    P("  phases: " .. table.concat(ph, " "))
  end
  for i, w in ipairs(FR.worst) do
    local parts = {}
    for _, e in ipairs(w.top) do parts[#parts + 1] = ("%s=%d"):format(e.k, e.n) end
    local jp = {}
    for _, e in ipairs(w.jobs) do jp[#jp + 1] = ("%s=%d"):format(e.k, e.n) end
    P(("  worst#%d %4d kinstr @%6.1fs  %s  | jobs %s"):format(i, w.cost, w.t, table.concat(parts, " "), table.concat(jp, " ")))
  end
  if (c[n] or 0) > BUDGET.frameWorstKinstr then fail(("hitch: worst frame %d kinstr > %d"):format(c[n], BUDGET.frameWorstKinstr)) end
  if pct(0.99) > BUDGET.frameP99Kinstr then fail(("hitch: p99 frame %d kinstr > %d"):format(pct(0.99), BUDGET.frameP99Kinstr)) end
  if phRatio > BUDGET.framePhaseRatio then fail(("hitch: 1-Hz pattern, frame phase ratio %.2f > %.2f"):format(phRatio, BUDGET.framePhaseRatio)) end
  if kbAvg > BUDGET.allocKBps then fail(("gc: %.1f KB/s allocated > %d"):format(kbAvg, BUDGET.allocKBps)) end
end
do  -- CHEAT25 : les avantages restent discrets (une ligne quand elle sert, rien sinon)
  local ek = (perMod.Edge2 or 0) / nMin
  local pct = 100 * (S.edgeSec or 0) / math.max(1, S.samples)
  P(("edge: %.0f kinstr/min, in « Maintenant » %.1f%% of samples, %d distinct lines"):format(ek, pct, S.edgeDistinct or 0))
  if opts.verbose or pct > BUDGET.edgeNowPct then for k, n in pairs(S.edgeSeen or {}) do P(("  edge %5d s  %s"):format(n, k)) end end
  if ek > BUDGET.edgeKinstrMin then fail(("cpu: Edge2 %.0f kinstr/min > %d"):format(ek, BUDGET.edgeKinstrMin)) end
  if pct > BUDGET.edgeNowPct then fail(("Edge2 lines in « Maintenant » %.1f%% of the time > %d%%"):format(pct, BUDGET.edgeNowPct)) end
  if (S.edgeDistinct or 0) > BUDGET.edgeDistinctMax then fail(("%d distinct Edge2 lines > %d"):format(S.edgeDistinct, BUDGET.edgeDistinctMax)) end
end
if opts.fine then
  local ks = {}
  for k in pairs(fine) do ks[#ks + 1] = k end
  table.sort(ks, function(a, b) return fine[a] > fine[b] end)
  P("fine (main client, kinstr/min by Module:function line):")
  for i = 1, math.min(opts.verbose and 300 or 80, #ks) do P(("  %-40s %7.1f"):format(ks[i], fine[ks[i]] / opts.minutes)) end
end
if totalMain / nMin > BUDGET.kinstrTotalMin then fail(("cpu: addon total %.0f kinstr/min > %d"):format(totalMain / nMin, BUDGET.kinstrTotalMin)) end

-- SIMPLE: jargon in the quest guidance
do
  local nj = 0
  for text, w in pairs(S.jargon) do
    nj = nj + 1
    if nj <= 3 then P(("jargon (%s): %s"):format(tostring(w), text)) end
  end
  P(("quest guidance: %d line(s) with jargon"):format(nj))
  if nj > 0 then fail(("%d quest line(s) with internal jargon (score / λ / /100)"):format(nj)) end
end

-- « Maintenant » changes
local sumCh, maxCh, maxChMin = 0, 0, 0
for m = 1, opts.minutes - 1 do
  local n = S.nowChanges[m] or 0
  sumCh = sumCh + n
  if n > maxCh then maxCh, maxChMin = n, m end
end
local avgCh = sumCh / math.max(1, opts.minutes - 1)
P(("now: %.1f changes/min avg, max %d (minute %d)"):format(avgCh, maxCh, maxChMin))
if maxCh > BUDGET.nowChangesPerMinMax then fail(("« Maintenant » changed %d times in minute %d > %d"):format(maxCh, maxChMin, BUDGET.nowChangesPerMinMax)) end
if avgCh > BUDGET.nowChangesPerMinAvg then fail(("« Maintenant » changes %.1f/min > %d"):format(avgCh, BUDGET.nowChangesPerMinAvg)) end

-- alerts
local maxWin, worstText, worstN = 0, nil, 0
local A = main.alerts
for i = 1, #A do
  local n, same = 0, {}
  for j = i, #A do
    if A[j].t - A[i].t < 600 then
      n = n + 1
      same[A[j].text] = (same[A[j].text] or 0) + 1
      if same[A[j].text] > worstN then worstN, worstText = same[A[j].text], A[j].text end
    end
  end
  if n > maxWin then maxWin = n end
end
P(("alerts: %d pops, max %d per 10 min, worst repeat %d× « %s »"):format(#A, maxWin, worstN, tostring(worstText)))
if opts.verbose then for _, a in ipairs(A) do P(("  alert %5.1f min  %s"):format((a.t - T0) / 60, a.text)) end end
if maxWin > BUDGET.alertsPer10Min then fail(("corner alerts: %d in 10 min > %d"):format(maxWin, BUDGET.alertsPer10Min)) end
if worstN > BUDGET.sameAlertRepeats then fail(("corner alert repeated %d× in 10 min: %s"):format(worstN, tostring(worstText))) end

-- duplicates by meaning
local dupPct = 100 * S.dupSec / math.max(1, S.samples)
P(("dup-meaning: %.1f%% of %d samples"):format(dupPct, S.samples))
local dups = {}
for k, n in pairs(S.dupEx) do dups[#dups + 1] = { k = k, n = n } end
table.sort(dups, function(a, b) return a.n > b.n end)
for i = 1, math.min(#dups, opts.verbose and 20 or 5) do P(("  %4ds  %s"):format(dups[i].n, dups[i].k)) end
if dupPct > BUDGET.dupMeaningPct then fail(("duplicates by meaning in « Maintenant »: %.1f%% > %d%%"):format(dupPct, BUDGET.dupMeaningPct)) end

-- SavedVariables
local sv30 = S.sv[30] or S.sv[math.floor(opts.minutes / 2)] or 0
P(("savedvars: %d B at min 1, %d B at min 30, %d B at logout (reloads: %s / %s B)"):format(S.sv[1] or 0, sv30, #svFinal,
  tostring(S.svReload1), tostring(S.svReload2)))
if #svFinal > BUDGET.svBytesMax then fail(("SavedVariables %d B > %d"):format(#svFinal, BUDGET.svBytesMax)) end
if opts.minutes >= 60 and #svFinal - sv30 > BUDGET.svGrowthLastHalf then fail(("SavedVariables grew %d B in the last 30 min > %d"):format(#svFinal - sv30, BUDGET.svGrowthLastHalf)) end
for p, what in pairs(svProblems) do fail("SavedVariables: " .. p .. " is a " .. what) end

-- memory per load segment: compare 3 min after the (re)load with the end of the segment
local segs = {}
for m, v in pairs(S.mem) do
  local sg = segs[v.seg] or {}
  segs[v.seg] = sg
  if m >= v.seg + 3 and (not sg.a or m < sg.am) then sg.a, sg.am = v.bytes, m end
  if not sg.b or m > sg.bm then sg.b, sg.bm = v.bytes, m end
end
for seg, sg in pairs(segs) do
  if sg.a and sg.b and sg.bm > sg.am then
    local g = (sg.b - sg.a) / 1024
    P(("memory: segment from min %d: %.0f KB @%d -> %.0f KB @%d (%+.0f KB)"):format(seg, sg.a / 1024, sg.am, sg.b / 1024, sg.bm, g))
    if g > BUDGET.memGrowthKB then fail(("addon memory grew %.0f KB between minutes %d and %d > %d"):format(g, sg.am, sg.bm, BUDGET.memGrowthKB)) end
  end
end
collectgarbage("collect")
P(("lua state: %.1f MB (4 clients)"):format(collectgarbage("count") / 1024))

-- message rate
for _, c in ipairs(order) do
  local perMin = {}
  for _, t in ipairs(c.sends) do local m = math.floor((t - T0) / 60) perMin[m] = (perMin[m] or 0) + 1 end
  local mx, mxm = 0, 0
  for m, n in pairs(perMin) do if n > mx then mx, mxm = n, m end end
  local avg = #c.sends / opts.minutes
  P(("comms %-10s %4d msgs, %.1f/min avg, max %d (min %d)"):format(c.name, #c.sends, avg, mx, mxm))
  local gm, gmx, gmm = {}, 0, 0
  for _, t in ipairs(c.grp or {}) do local m = math.floor((t - T0) / 60) gm[m] = (gm[m] or 0) + 1 end
  for m, n in pairs(gm) do if m >= 1 and not (R.joinMin or {})[m] and n > gmx then gmx, gmm = n, m end end
  P(("  groupe %-8s %4d msgs GRP, max %d in a steady minute (min %d)"):format("", #(c.grp or {}), gmx, gmm))
  if gmx > BUDGET.groupMsgsPerMin then fail(("%s sent %d « GRP » messages in minute %d > %d (≤ 1 / 5 s)"):format(c.name, gmx, gmm, BUDGET.groupMsgsPerMin)) end
  if mx > BUDGET.msgsPerMinMax then fail(("%s sent %d addon messages in minute %d > %d"):format(c.name, mx, mxm, BUDGET.msgsPerMinMax)) end
  if avg > BUDGET.msgsPerMinAvg then fail(("%s sends %.1f addon messages/min > %d"):format(c.name, avg, BUDGET.msgsPerMinAvg)) end
end
local cs = main.T and main.T.commStats
if cs then P(("comms stats (main, since last reload): sent %d throttled %d dropped %d"):format(cs.sent, cs.throttled, cs.dropped)) end

-- chat lines not asked for (the player typed /tai hv and /tai macros: those may print)
local chat = 0
for _, l in ipairs(main.M.output) do
  if not l:find("HV", 1, true) and not l:find("macro", 1, true) and not l:find("Macro", 1, true) then chat = chat + 1
    if opts.verbose then P("  chat | " .. l) end end
end
P(("chat: %d unsolicited lines since the last reload"):format(chat))
if chat > BUDGET.chatLinesMax then fail(("%d unsolicited chat lines > %d"):format(chat, BUDGET.chatLinesMax)) end

-- TAINT: blocked actions (the AddOns list counter « Échec des actions d'interface »), all clients
local blockedN = 0
for _, c in ipairs(order) do blockedN = blockedN + (c.blocked or 0) end
P(("blocked actions: %d (budget %d)"):format(blockedN, BUDGET.blockedMax))
for fn, n in pairs(R.blockedBy or {}) do P(("  blocked %s x%d"):format(fn, n)) end
if blockedN > BUDGET.blockedMax then fail(("%d blocked actions > %d"):format(blockedN, BUDGET.blockedMax)) end

-- the session really happened
local p = main.M.player
P(("player: level %d, %d quests in log, %d free slots, money %d"):format(p.level, #main.M.quests, countFree(main.M), p.money))
if p.level < 27 then fail("no level-up happened") end
if opts.verbose then for _, l in ipairs(S.nowLog) do P("  now " .. l) end end

for _, e in ipairs(R.errors) do P(("ERROR [%s %s] x%d %s"):format(e.who, e.label, e.n, e.msg:sub(1, 400))) end
for _, e in ipairs(R.swallowed) do P(("ERROR swallowed [%s %s] x%d %s"):format(e.who, e.label, e.n, e.msg:sub(1, 400))) end
for _, v in ipairs(R.violations) do P(("VIOLATION [%s %s] x%d %s"):format(v.who, v.label, v.n, v.msg)) end
for k, w in pairs(R.leaks) do P(("LEAK global %s (%s)"):format(tostring(k), w)) end
for _, f in ipairs(R.fails) do P("FAIL " .. f) end
local bad = #R.errors + #R.swallowed + #R.violations + #R.fails
for _ in pairs(R.leaks) do bad = bad + 1 end
P(("RESULT %s session class=%s flavor=%s errors=%d swallowed=%d violations=%d fails=%d"):format(bad == 0 and "ok" or "fail",
  opts.class, opts.flavor, #R.errors, #R.swallowed, #R.violations, #R.fails))
io.write(table.concat(out, "\n"), "\n")
os.exit(bad == 0 and 0 or 1)
