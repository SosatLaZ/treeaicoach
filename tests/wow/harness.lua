-- harness.lua — load a WoW addon (its TOC, in order) on top of wowmock.lua and drive it with events.
-- Lua 5.1:  lua5.1 tests/wow/harness.lua [options] <AddonDir | file.toc>
--   --flavor modern|legacy   API flavour of the mock (default modern)
--   --savedvars FILE          Lua file that defines the SavedVariables (as WoW would restore them)
--   --dump FILE               write the SavedVariables at PLAYER_LOGOUT, WoW-style, to FILE
--   --scenario FILE           extra Lua steps: the file returns function(M, ctx) (ctx.check(cond, msg))
--   --allow NAME              extra allowed global (repeatable)
--   --class CLASS --level N   player class file (HUNTER, MAGE…) and level
--   --no-storm                skip the generic event storm (only load + login + logout)
--   --geometry SPEC           real-client frame geometry + login race (tests/wow/geometry.lua; SPEC = default | k=v,…)
--   -q                        quiet: only the summary
-- Generic: discovers the TOC and its files at run time; a missing file is SKIPPED with a note
-- (other modules may not be merged yet). Exit 0 = ok, 1 = failure (Lua error, ToS/API violation,
-- global leak, unserialisable SavedVariables), 2 = usage / nothing to test.

local unpack = unpack or table.unpack
local argv = arg or {}
local here = (argv[0] or "harness.lua"):match("^(.*)[/\\][^/\\]*$") or "."

local opts = { flavor = "modern", allow = {}, storm = true, profile = "tout" }
local target
do
  local i = 1
  while i <= #argv do
    local a = argv[i]
    if a == "--flavor" then opts.flavor = argv[i + 1]; i = i + 1
    elseif a == "--savedvars" then opts.savedvars = argv[i + 1]; i = i + 1
    elseif a == "--dump" then opts.dump = argv[i + 1]; i = i + 1
    elseif a == "--scenario" then opts.scenario = argv[i + 1]; i = i + 1
    elseif a == "--allow" then opts.allow[argv[i + 1]] = true; i = i + 1
    elseif a == "--class" then opts.class = argv[i + 1]; i = i + 1
    elseif a == "--level" then opts.level = tonumber(argv[i + 1]); i = i + 1
    elseif a == "--no-storm" then opts.storm = false
    elseif a == "--profile" then opts.profile = argv[i + 1]; i = i + 1
    elseif a == "--geometry" then opts.geometry = argv[i + 1]; i = i + 1
    elseif a == "-q" then opts.quiet = true
    else target = a end
    i = i + 1
  end
end
if not target then
  io.stderr:write("usage: lua5.1 harness.lua [options] <AddonDir|file.toc>\n")
  os.exit(2)
end
-- FIDELITY: TREEAI_WOW_FORCE_FLAVOR=forever runs every scenario of the suite on the faithful Forever mock;
-- TREEAI_WOW_RUNLOG=FILE appends each run's arguments (to replay them: tests/wow/test_forever_fidelity.py)
do
  local force = os.getenv("TREEAI_WOW_FORCE_FLAVOR")
  if force and force ~= "" then opts.flavor = force end
  local log = os.getenv("TREEAI_WOW_RUNLOG")
  if log and log ~= "" then
    local f = io.open(log, "a")
    if f then
      local parts = {}
      for i = 1, #argv do parts[#parts + 1] = argv[i] end
      f:write(table.concat(parts, "\t"), "\n")
      f:close()
    end
  end
end

local notes = {}
local function note(s) notes[#notes + 1] = s end
local function say(s) if not opts.quiet then io.write(s, "\n") end end

local function exists(p) local f = io.open(p, "r") if f then f:close() return true end return false end
local function readAll(p) local f = io.open(p, "rb") if not f then return nil end local s = f:read("*a") f:close() return s end
local function listDir(dir)
  local out = {}
  local p = io.popen('ls -1 "' .. dir .. '" 2>/dev/null')
  if p then for l in p:lines() do out[#out + 1] = l end p:close() end
  return out
end

-- locate the TOC ---------------------------------------------------------------------------------
local tocPath, addonDir, addonName
if target:match("%.toc$") then
  tocPath = target
  addonDir = target:match("^(.*)[/\\][^/\\]*$") or "."
  addonName = (target:match("([^/\\]+)%.toc$")):gsub("_[%a]+$", "")
else
  addonDir = target:gsub("[/\\]+$", "")
  addonName = addonDir:match("([^/\\]+)$")
  local tocs = {}
  for _, f in ipairs(listDir(addonDir)) do if f:match("%.toc$") then tocs[#tocs + 1] = f end end
  table.sort(tocs)
  local pick
  for _, pref in ipairs({ addonName .. ".toc", addonName .. "_Vanilla.toc", addonName .. "_Classic.toc" }) do
    for _, f in ipairs(tocs) do if f == pref then pick = pick or f end end
  end
  pick = pick or tocs[1]
  if not pick then
    io.write("SKIP: no .toc in ", addonDir, "\n", "RESULT skip\n")
    os.exit(2)
  end
  if #tocs > 1 then note("several TOCs (" .. table.concat(tocs, ", ") .. "), using " .. pick) end
  tocPath = addonDir .. "/" .. pick
end

-- parse the TOC (+ simple XML <Script file>/<Include file>) -----------------------------------
local meta, files = {}, {}
local function addXml(rel, depth)
  local path = addonDir .. "/" .. rel
  local src = readAll(path)
  if not src then note("SKIP missing file " .. rel) return end
  local base = rel:match("^(.*)/[^/]*$")
  local found = false
  for tag, file in src:gmatch("<(%a+)%s+file%s*=%s*\"([^\"]+)\"") do
    file = file:gsub("\\", "/")
    local full = base and (base .. "/" .. file) or file
    if tag == "Script" then files[#files + 1] = full; found = true
    elseif tag == "Include" and depth < 8 then addXml(full, depth + 1); found = true end
  end
  if src:find("<Frame") or src:find("<Button") then note("XML frames in " .. rel .. " are not simulated") end
  if not found then note("XML " .. rel .. " has no <Script>/<Include>") end
end
do
  local src = readAll(tocPath)
  if not src then io.write("FAIL: cannot read ", tocPath, "\n") os.exit(1) end
  src = src:gsub("^\239\187\191", "")
  for line in (src .. "\n"):gmatch("([^\r\n]*)\r?\n") do
    local k, v = line:match("^##%s*([^:]+):%s*(.-)%s*$")
    if k then
      meta[k] = v
    elseif not line:match("^%s*#") and line:match("%S") then
      local f = line:gsub("^%s+", ""):gsub("%s+$", ""):gsub("\\", "/")
      if f:lower():match("%.xml$") then addXml(f, 0)
      elseif f:lower():match("%.lua$") then files[#files + 1] = f
      else note("ignored TOC line: " .. f) end
    end
  end
end
local svNames = {}
for _, key in ipairs({ "SavedVariables", "SavedVariablesPerCharacter" }) do
  for n in (meta[key] or ""):gmatch("[%w_]+") do svNames[#svNames + 1] = n end
end

-- mock -------------------------------------------------------------------------------------------
local M = dofile(here .. "/wowmock.lua")
M.addonName = addonName
M.Install(opts.flavor)
-- PLACEBUG: frames anchored / measured like the real client, login race (UIParent unsized, UI scale after login)
if opts.geometry then dofile(here .. "/geometry.lua")(M, opts.geometry) end
M.tocMeta = { [addonName] = meta }
if opts.class then M.player.class = opts.class:upper() end
if opts.level then M.player.level = opts.level end
if M.player.class ~= "HUNTER" then M.pet.exists = false end

local allowed = { WowMock = true }
-- FIDELITY (forever): an API name the real client lacks, set back by a scenario that simulates it, is not a leak
for _, n in ipairs(M.pruned or {}) do if not n:find(".", 1, true) then allowed[n] = true end end
allowed[addonName] = true
for _, n in ipairs(svNames) do allowed[n] = true end
for n in pairs(opts.allow) do allowed[n] = true end
local function isAllowed(k)
  if type(k) ~= "string" then return false end
  if allowed[k] then return true end
  if k:match("^SLASH_[%w_]+%d+$") or k:match("^BINDING_HEADER_") or k:match("^BINDING_NAME_") then return true end
  return false
end

local before = {}
for k in pairs(_G) do before[k] = true end
local leaks, nilReads = {}, {}
local function where(level)
  local info = debug.getinfo(level, "Sl")
  return info and (tostring(info.short_src) .. ":" .. tostring(info.currentline)) or "?"
end
setmetatable(_G, {
  __newindex = function(t, k, v)
    if not isAllowed(k) and not M.namedFrames[k] then leaks[k] = leaks[k] or where(3) end
    rawset(t, k, v)
  end,
  __index = function(_, k)
    if type(k) == "string" and not nilReads[k] and not allowed[k] then nilReads[k] = where(3) end
    return nil
  end,
})

-- load files -------------------------------------------------------------------------------------
local addonTable = {}
local loaded, skipped = 0, 0
say(("== %s (%s) flavor=%s Interface=%s"):format(addonName, tocPath, opts.flavor, tostring(meta.Interface)))
for _, rel in ipairs(files) do
  local path = addonDir .. "/" .. rel
  if not exists(path) then
    skipped = skipped + 1
    note("SKIP missing file " .. rel)
  else
    local chunk, err = loadfile(path)
    if not chunk then
      M.errors[#M.errors + 1] = { label = "syntax " .. rel, msg = tostring(err) }
    else
      if M.SafeCall("load " .. rel, chunk, addonName, addonTable) then loaded = loaded + 1 end
    end
  end
end

-- SavedVariables restored after the files ran, before ADDON_LOADED (as WoW does)
if opts.savedvars then
  local chunk, err = loadfile(opts.savedvars)
  if not chunk then
    M.errors[#M.errors + 1] = { label = "savedvars", msg = tostring(err) }
  else
    local env = {}
    setfenv(chunk, env)
    if M.SafeCall("savedvars", chunk) then
      for _, n in ipairs(svNames) do rawset(_G, n, env[n]) end
    end
  end
end

-- TreeAI module profile (LITE): scenarios test every module, so the default here is « tout » (everything on,
-- the one-time « essentiel » switch already done); --profile essentiel = what a new player gets.
if addonName == "TreeAI" then   -- (3.9.5) « essentiel » is opt-in: seeded only when asked
  local db = rawget(_G, "TreeAIDB")
  if type(db) ~= "table" then db = {}; rawset(_G, "TreeAIDB", db) end
  -- --profile sur : nothing seeded, the addon's one-time « mode sûr » migration runs (what a new player gets)
  if db.profileV == nil and opts.profile ~= "sur" then
    db.profileV, db.moduleProfile = 999, (opts.profile == "essentiel") and "essentiel" or "tout"
  end
  -- (ONBOARD) the first-run « Visite guidée » counts as seen (the storm still runs it via /tai visite ;
  -- tour_scenario.lua clears it to test the first run) ; --profile sur = a new player: nothing seeded
  if db.tour == nil and opts.profile ~= "sur" then db.tour = { done = 999 } end
end

-- drive ------------------------------------------------------------------------------------------
local fired = {}
local function fire(ev, ...) fired[ev] = true return M.FireEvent(ev, ...) end
local ctx = { addonName = addonName, addonTable = addonTable, meta = meta, fire = fire, notes = notes }
function ctx.check(cond, msg)
  if not cond then M.errors[#M.errors + 1] = { label = "check", msg = tostring(msg) .. " (" .. where(3) .. ")" } end
  return cond
end
function ctx.out(pattern)
  for _, l in ipairs(M.output) do if l:find(pattern) then return l end end
end

fire("ADDON_LOADED", addonName)
fire("VARIABLES_LOADED")
fire("SPELLS_CHANGED")
fire("PLAYER_LOGIN")
fire("PLAYER_ENTERING_WORLD", true, false)
-- the player picked « essentiel » (Plus → Modules): modules off as that choice does it
if opts.profile == "essentiel" and rawget(_G, "TreeAI") and TreeAI.SetModuleProfile then TreeAI:SetModuleProfile("essentiel") end
M.Advance(2)

local function clickAll()
  for i = 1, #M.frames do
    local f = M.frames[i]
    -- (LITE) « Plus → Modules » rows (module on / off, profile) are left alone: the scenarios need their modules
    local id = type(f.entry) == "table" and type(f.entry.id) == "string" and f.entry.id or ""
    if f.__scripts.OnClick and f.__kind ~= "GameTooltip" and not id:match("^mod:") and id ~= "core:profile" then
      f:Click("LeftButton")
    end
  end
end

local function storm()
  M.group = {
    { name = "Amie", class = "PRIEST", level = 25, health = 700, healthMax = 800, online = true },
    { name = "Compagnon", class = "WARRIOR", level = 27, health = 1200, healthMax = 1300, online = true },
    { name = "Camarade", class = "MAGE", level = 26, health = 600, healthMax = 700, online = false },
  }
  fire("GROUP_ROSTER_UPDATE")
  fire("PLAYER_GUILD_UPDATE", "player"); fire("GUILD_ROSTER_UPDATE")
  fire("PARTY_MEMBERS_CHANGED")
  for _, u in ipairs({ "player", "pet" }) do
    fire("UNIT_HEALTH", u); fire("UNIT_HEALTH_FREQUENT", u); fire("UNIT_MAXHEALTH", u)
    fire("UNIT_POWER_UPDATE", u, "MANA"); fire("UNIT_POWER_FREQUENT", u, "MANA"); fire("UNIT_AURA", u)
  end
  fire("UNIT_PET", "player"); fire("PET_BAR_UPDATE"); fire("UNIT_HAPPINESS", "pet"); fire("PET_UI_UPDATE")
  fire("BAG_UPDATE", 0); fire("BAG_UPDATE_DELAYED"); fire("UNIT_INVENTORY_CHANGED", "player")
  fire("PLAYER_EQUIPMENT_CHANGED", 18, true); fire("UPDATE_INVENTORY_DURABILITY")
  fire("PLAYER_XP_UPDATE", "player"); fire("UPDATE_EXHAUSTION"); fire("PLAYER_UPDATE_RESTING")
  fire("ZONE_CHANGED_NEW_AREA"); fire("ZONE_CHANGED"); fire("ZONE_CHANGED_INDOORS")
  fire("QUEST_LOG_UPDATE"); fire("QUEST_ACCEPTED", 1, 1001); fire("UNIT_QUEST_LOG_CHANGED", "player")
  fire("QUEST_WATCH_UPDATE", 1); fire("QUEST_TURNED_IN", 1002, 1500, 100)
  fire("PLAYER_MONEY"); fire("CHAT_MSG_SYSTEM", "Vous êtes maintenant reposé.")
  fire("UI_ERROR_MESSAGE", 50, "Vos sacs sont pleins.")
  fire("LEARNED_SPELL_IN_TAB", 1130, 2); fire("CHARACTER_POINTS_CHANGED", 1)
  fire("ACTIVE_TALENT_GROUP_CHANGED", 2, 1)
  -- merchant
  M.SetMerchant(true); fired.MERCHANT_SHOW = true
  clickAll()
  M.Advance(1)
  M.SetMerchant(false); fired.MERCHANT_CLOSED = true
  -- target + fight
  M.target = { exists = true, name = "Ours enragé", level = 27, health = 800, healthMax = 800, hostile = true,
    classification = "normal", creatureType = "Bête", guid = "Creature-0-0-0-0-2351-0001" }
  fire("PLAYER_TARGET_CHANGED"); fire("UNIT_TARGET", "player")
  M.EnterCombat(); fired.PLAYER_REGEN_DISABLED = true
  M.autoRepeat = true
  fire("START_AUTOREPEAT_SPELL")
  for k = 1, 3 do
    fire("UNIT_SPELLCAST_SUCCEEDED", "player", "Cast-3-0-0-0-75-" .. k, 75)
    M.CombatLog("SPELL_CAST_SUCCESS", 75, "Tir automatique", 1)
    M.CombatLog("RANGE_DAMAGE", 75, "Tir automatique", 1, 120, -1, 1, 0, 0, 0, nil, nil, nil)
    M.Advance(2.9, 0.1)
  end
  fire("UNIT_SPELLCAST_START", "player", "Cast-x", 19434)
  fire("UNIT_SPELLCAST_STOP", "player", "Cast-x", 19434)
  M.CombatLog("SWING_DAMAGE", 85, -1, 1, 0, 0, 0, nil, nil, nil)
  M.CombatLog("SPELL_AURA_APPLIED", 1130, "Marque du chasseur", 64, "DEBUFF")
  M.CombatLog("SPELL_INTERRUPT", 2139, "Contresort", 64, 133, "Boule de feu", 4)
  M.pet.health = 50
  fire("UNIT_HEALTH", "pet")
  M.player.health = 150
  fire("UNIT_HEALTH", "player")
  fire("UNIT_THREAT_SITUATION_UPDATE", "party1")
  -- slash commands IN combat (macro edits must be refused by the addon, not attempted)
  for _, sc in ipairs(M.SlashCommands()) do
    for _, sub in ipairs({ "", "macros", "install" }) do M.RunSlash(sc.cmd .. " " .. sub) end
  end
  M.CombatLog("UNIT_DIED", nil, nil, nil)
  M.pet.dead = true
  fire("UNIT_PET", "player")
  fire("STOP_AUTOREPEAT_SPELL")
  M.autoRepeat = false
  M.LeaveCombat(); fired.PLAYER_REGEN_ENABLED = true
  M.target = { exists = false }
  fire("PLAYER_TARGET_CHANGED")
  M.player.health, M.pet.health, M.pet.dead = 900, 500, false
  M.Advance(5)
  -- level up, death
  M.player.level = M.player.level + 1
  fire("PLAYER_LEVEL_UP", M.player.level, 100, 50, 0, 1, 0, 0, 0, 0)
  fire("PLAYER_DEAD"); fire("PLAYER_ALIVE"); fire("PLAYER_UNGHOST")
  -- comms from a friend with every registered prefix
  for p in pairs(M.prefixes) do
    M.DeliverAddon(p, "HELLO", "PARTY", "Amie-Royaume")
    M.DeliverAddon(p, "\001garbage|with|pipes\002", "PARTY", "Amie-Royaume")
    M.DeliverAddon(p, "", "GUILD", "Compagnon-Royaume")
  end
  fired.CHAT_MSG_ADDON = true
  -- slash commands out of combat
  local subs = { "", "help", "show", "hide", "toggle", "status", "config", "options", "macros", "install",
    "debug", "lock", "unlock", "test" }
  for _, sc in ipairs(M.SlashCommands()) do
    for _, sub in ipairs(subs) do M.RunSlash(sc.cmd .. " " .. sub) end
  end
  clickAll()
  M.Advance(30, 0.5)
  -- the clicks above also hit « Plus → Modules » lines (one click = one module on / off): scenarios start from
  -- the profile they asked for
  local T = rawget(_G, "TreeAI")
  if T and T.SetModuleProfile and addonName == "TreeAI" then T:SetModuleProfile((opts.profile == "tout" or opts.profile == "sur") and opts.profile or "essentiel"); M.Advance(2, 0.5) end
  -- (ZONEFIX) …and from the defaults of what one click toggles (route paused, zone and mode of the route, the card,
  -- the compass): the storm clicked every row, some once, some twice — the end state depended on how many rows
  local mods = T and addonName == "TreeAI" and T.modules
  if mods then
    local R, Z, W, C = mods.Route, mods.ZonePlan, mods.Window, mods.Compass
    if R and R.char then R.char.paused = false end
    if Z and Z.char then Z.char.mode, Z.char.zones, Z.char.style, Z.char.ignore, Z.char.extra = "auto", {}, 1, {}, {} end
    if Z and Z.db then Z.db.autoWatch = true end
    if W and W.db then W.db.card = true end
    if C and C.db then C.db.shown = true end
    M.Advance(2, 0.5)
  end
  -- (MACRO3) …and the « Pause / Reprendre » of the route (page action, title, rows): how many times it is hit depends
  -- on how many pages / pooled buttons exist, so scenarios start with the route running, whatever the window holds
  local R = T and addonName == "TreeAI" and T.Route
  if R and type(R.char) == "table" and R.char.paused and R.TogglePause then pcall(R.TogglePause, R); M.Advance(1, 0.5) end
end

if opts.storm then storm() end

-- FIDELITY (forever): a scenario calling TreeAI:UserAction(fn) directly stands for the player's click on a line:
-- that call (and only a call coming from the scenario file) runs as one hardware event, as the click would
if opts.flavor == "forever" and opts.scenario and addonName == "TreeAI" then
  local T = rawget(_G, "TreeAI")
  if type(T) == "table" and type(T.UserAction) == "function" then
    local ua = T.UserAction
    local scen = opts.scenario:gsub("^.*[/\\]", "")
    T.UserAction = function(self, ...)
      local info = debug.getinfo(2, "S")
      local src = info and tostring(info.short_src) or ""
      if src:sub(-#scen) == scen and not M.hardware then return M.Hw(ua, self, ...) end
      return ua(self, ...)
    end
  end
end

if opts.scenario then
  local chunk, err = loadfile(opts.scenario)
  if not chunk then
    M.errors[#M.errors + 1] = { label = "scenario", msg = tostring(err) }
  else
    local ok, fn = pcall(chunk)
    if ok and type(fn) == "function" then M.SafeCall("scenario", fn, M, ctx)
    else M.errors[#M.errors + 1] = { label = "scenario", msg = "scenario must return function(M, ctx)" } end
  end
end

fire("PLAYER_LEAVING_WORLD")
fire("PLAYER_LOGOUT")
setmetatable(_G, nil)
-- FIDELITY (forever): a secret used as a table key (an immediate error in the real client) — scan what the addon
-- can reach; secret booleans handed to addon code (a boolean test on one errors in the client: listed, not failed)
if opts.flavor == "forever" and M.ScanSecretKeys then
  local roots = {}
  for k, v in pairs(_G) do if allowed[k] and type(v) == "table" and k ~= "WowMock" then roots[#roots + 1] = v end end
  roots[#roots + 1] = addonTable
  for _, path in ipairs(M.ScanSecretKeys(roots)) do
    M.violations[#M.violations + 1] = { kind = "secret", msg = "secret value used as a table key: " .. path, where = "?" }
  end
  local seenB = {}
  for _, b in ipairs(M.secretBooleans or {}) do
    local key = b.fn .. "@" .. b.where
    if not seenB[key] then
      seenB[key] = true
      local line = "secret boolean from " .. b.fn .. " at " .. b.where .. " (an if / not on it errors in the real client)"
      if os.getenv("TREEAI_WOW_SECRETBOOL") then io.write("SECRETBOOL ", line, "\n") else note(line) end
    end
  end
end

-- SavedVariables serialisation (WoW-like) -----------------------------------------------------
local svOut, svProblems = {}, {}
local function keyRepr(k)
  if type(k) == "string" then return ("[%q]"):format(k) end
  return "[" .. tostring(k) .. "]"
end
local function ser(v, indent, path, seen)
  local t = type(v)
  if t == "string" then return ("%q"):format(v):gsub("\\\n", "\\n") end
  if t == "number" then
    if v ~= v or v == math.huge or v == -math.huge then svProblems[#svProblems + 1] = path .. " is NaN/inf" return "0" end
    return tostring(v)
  end
  if t == "boolean" then return tostring(v) end
  if t == "nil" then return "nil" end
  if t == "table" then
    if seen[v] then svProblems[#svProblems + 1] = path .. " is a cycle / shared table" return "nil" end
    seen[v] = true
    local keys = {}
    for k in pairs(v) do
      if type(k) == "string" or type(k) == "number" or type(k) == "boolean" then keys[#keys + 1] = k
      else svProblems[#svProblems + 1] = path .. " has a " .. type(k) .. " key" end
    end
    table.sort(keys, function(a, b)
      if type(a) == type(b) and type(a) ~= "boolean" then return a < b end
      return type(a) < type(b)
    end)
    local parts = { "{\n" }
    for _, k in ipairs(keys) do
      local val = v[k]
      local tv = type(val)
      if tv == "function" or tv == "userdata" or tv == "thread" then
        svProblems[#svProblems + 1] = path .. "." .. tostring(k) .. " is a " .. tv .. " (WoW drops it)"
      else
        parts[#parts + 1] = indent .. "\t" .. keyRepr(k) .. " = " .. ser(val, indent .. "\t", path .. "." .. tostring(k), seen) .. ",\n"
      end
    end
    parts[#parts + 1] = indent .. "}"
    seen[v] = nil
    return table.concat(parts)
  end
  svProblems[#svProblems + 1] = path .. " is a " .. t .. " (WoW drops it)"
  return "nil"
end
for _, n in ipairs(svNames) do
  svOut[#svOut + 1] = "\n" .. n .. " = " .. ser(rawget(_G, n), "", n, {}) .. "\n"
end
if opts.dump then
  local f = io.open(opts.dump, "wb")
  if f then f:write(table.concat(svOut)) f:close() else note("cannot write " .. opts.dump) end
end

-- leaks: anything new in _G (covers rawset too) ---------------------------------------------
local namedOdd = {}
for k in pairs(_G) do
  if not before[k] and not isAllowed(k) then
    if M.namedFrames[k] then
      if not tostring(k):find(addonName, 1, true) then namedOdd[#namedOdd + 1] = k end
    else
      leaks[k] = leaks[k] or "?"
    end
  end
end

-- report -----------------------------------------------------------------------------------------
local leakList = {}
for k, w in pairs(leaks) do leakList[#leakList + 1] = tostring(k) .. " (" .. w .. ")" end
table.sort(leakList)
local registered = {}
for i = 1, #M.frames do for ev in pairs(M.frames[i].__events) do registered[ev] = true end end
local unfired = {}
for ev in pairs(registered) do if not fired[ev] then unfired[#unfired + 1] = ev end end
table.sort(unfired)
local nilList = {}
for k, w in pairs(nilReads) do nilList[#nilList + 1] = k .. " (" .. w .. ")" end
table.sort(nilList)
local unm = {}
for k in pairs(M.unmockedMethods) do unm[#unm + 1] = k end
table.sort(unm)

say(("files: %d loaded, %d skipped (of %d in TOC)"):format(loaded, skipped, #files))
for _, n in ipairs(notes) do say("note: " .. n) end
local slashes = {}
for _, sc in ipairs(M.SlashCommands()) do slashes[#slashes + 1] = sc.cmd end
say("slash: " .. (#slashes > 0 and table.concat(slashes, " ") or "-"))
say(("frames: %d, addon messages sent: %d, macros: %d global / %d char"):format(#M.frames, #M.sentAddon,
  #M.macros.global, #M.macros.char))
if #unfired > 0 then say("info: events registered but not fired by the harness: " .. table.concat(unfired, ", ")) end
if #nilList > 0 then say("info: nil globals read (feature tests or unmocked API): " .. table.concat(nilList, ", ")) end
if #unm > 0 then say("info: unmocked widget methods (no-op): " .. table.concat(unm, ", ")) end
if #namedOdd > 0 then say("warn: named frames without the addon prefix: " .. table.concat(namedOdd, ", ")) end
if not opts.quiet and #M.output > 0 then
  say("chat output (first 12):")
  for i = 1, math.min(12, #M.output) do say("  | " .. M.output[i]) end
end
for _, e in ipairs(M.errors) do io.write("ERROR [", e.label, "] ", e.msg, "\n") end
for _, v in ipairs(M.violations) do io.write("VIOLATION [", v.kind, "] ", v.msg, " @ ", v.where, "\n") end
for _, l in ipairs(leakList) do io.write("LEAK global ", l, "\n") end
for _, p in ipairs(svProblems) do io.write("SAVEDVARS ", p, "\n") end

local fail = #M.errors + #M.violations + #leakList + #svProblems
io.write(("RESULT %s addon=%s flavor=%s loaded=%d skipped=%d errors=%d violations=%d leaks=%d savedvars=%d\n"):format(
  fail == 0 and "ok" or "fail", addonName, opts.flavor, loaded, skipped, #M.errors, #M.violations, #leakList, #svProblems))
os.exit(fail == 0 and 0 or 1)
