-- TreeAI — module Comfort (agent X2) : confort (FEATURES50 n° 48–50), informatif uniquement.
--  48 ce qui a changé depuis la dernière connexion (niveau, XP de repos gagnée, or, sacs, zone, quêtes,
--     version de l'addon, temps d'absence) — instantané par personnage, écrit à la déconnexion
--  49 nouveaux sorts appris + emplacement de barre conseillé (case libre, touche) — jamais posé par l'addon
--  50 /tai perf : coût CPU (chronométrage de nos propres fonctions de module, + GetAddOnCPUUsage si le
--     profilage est actif) et mémoire (GetAddOnMemoryUsage) ; ralentissement automatique des minuteries
--     TreeAI quand les FPS < 30 ou que TreeAI coûte > 2 ms par image (et en ville, en option).
-- Aucune action à la place du joueur. Lua 5.1, aucune globale, toutes les API testées.
local T = _G.TreeAI
if not T or not T.RegisterModule then return end
local API = T.API or {}
local G = _G

local M = T:RegisterModule("Comfort", {
  title = "Confort",
  defaults = { enabled = true, changes = true, newSpells = true, timing = false, autoSlow = true, cityThrottle = true },
  charDefaults = { snap = nil, spells = nil },
  options = {
    { key = "changes", type = "toggle", label = "Ce qui a changé depuis la dernière connexion", section = "Connexion" },
    { key = "newSpells", type = "toggle", label = "Nouveaux sorts : emplacement de barre conseillé", section = "Sorts" },
    { key = "timing", type = "toggle", label = "Mesurer le coût de chaque module (/tai perf, après /reload)", section = "Performances" },
    { key = "autoSlow", type = "toggle", label = "Ralentir TreeAI si FPS < 30 ou > 2 ms par image" },
    { key = "cityThrottle", type = "toggle", label = "Ralentir aussi en ville / auberge (hors combat)" },
  },
})
T.Comfort = M
local ADDON = "TreeAI"

local function pass(ok, ...) if ok then return ... end return nil end
local function try(fn, ...) if type(fn) ~= "function" then return nil end return pass(pcall(fn, ...)) end
local function secret(v) local f = rawget(G, "issecretvalue") return type(f) == "function" and try(f, v) and true or false end
local function sv(v) if v == nil or secret(v) then return nil end return v end
local function now() return try(G.GetTime) or 0 end
local function opt(k) return M.db and M.db[k] end
local function wall() return try(G.time) or try(G.GetServerTime) or 0 end
local function money(c)
  c = math.floor(math.abs(tonumber(c) or 0))
  local g, s, k = math.floor(c / 10000), math.floor(c / 100) % 100, c % 100
  if g > 0 then return ("%d po %d pa"):format(g, s) end
  if s > 0 then return ("%d pa %d pc"):format(s, k) end
  return ("%d pc"):format(k)
end
local function dur(sec)
  sec = math.floor(tonumber(sec) or 0)
  local d, h, m = math.floor(sec / 86400), math.floor(sec / 3600) % 24, math.floor(sec / 60) % 60
  if d > 0 then return ("%d j %d h"):format(d, h) end
  if h > 0 then return ("%d h %02d"):format(h, m) end
  return ("%d min"):format(math.max(1, m))
end
local function fmt(n) n = math.floor((tonumber(n) or 0) + 0.5) local s = tostring(math.abs(n)):reverse():gsub("(%d%d%d)", "%1 "):reverse() return (n < 0 and "-" or "") .. (s:gsub("^ ", "")) end

local infoLines = {}
local function pushInfo()
  local out = {}
  for _, k in ipairs({ "changes", "spells", "perf" }) do
    for _, l in ipairs(infoLines[k] or {}) do out[#out + 1] = l end
  end
  if T.SetTabLines then try(T.SetTabLines, T, "info", out, "Comfort") end
end

---------------------------------------------------------------------------------------------------
-- 48. changements depuis la dernière connexion
---------------------------------------------------------------------------------------------------
local function questCount()
  local q = G.C_QuestLog
  local n
  if q and q.GetNumQuestLogEntries then
    local _, quests = try(q.GetNumQuestLogEntries)
    n = quests
  elseif G.GetNumQuestLogEntries then
    local _, quests = try(G.GetNumQuestLogEntries)
    n = quests
  end
  return sv(n)
end

function M.Snapshot()
  local free, total
  if API.FreeSlots then free, total = API.FreeSlots() end
  return {
    time = wall(), level = sv(try(G.UnitLevel, "player")), xp = sv(try(G.UnitXP, "player")),
    xpMax = sv(try(G.UnitXPMax, "player")), rested = sv(try(G.GetXPExhaustion)) or 0,
    money = sv(API.Money and API.Money() or try(G.GetMoney)), bagsFree = free, bagsTotal = total,
    zone = try(G.GetRealZoneText) or try(G.GetZoneText), quests = questCount(), version = T.version,
  }
end

--- Pur : liste de changements lisibles entre deux instantanés
function M.Diff(prev, cur)
  local out = {}
  if type(prev) ~= "table" or type(cur) ~= "table" then return out end
  if prev.time and cur.time and cur.time > prev.time then
    out[#out + 1] = "Absent " .. dur(cur.time - prev.time)
  end
  if prev.version and cur.version and prev.version ~= cur.version then
    out[#out + 1] = ("TreeAI mis à jour : %s → %s"):format(prev.version, cur.version)
  end
  if prev.level and cur.level and cur.level ~= prev.level then
    out[#out + 1] = ("Niveau %d → %d"):format(prev.level, cur.level)
  end
  if prev.level == cur.level and (cur.rested or 0) > (prev.rested or 0) + 50 then
    local gain = cur.rested - (prev.rested or 0)
    local pctTxt = (cur.xpMax and cur.xpMax > 0) and (" (%d %% d'un niveau)"):format(math.floor(gain / cur.xpMax * 100 + 0.5)) or ""
    out[#out + 1] = ("Repos : +%s XP bonus%s"):format(fmt(gain), pctTxt)
  end
  if prev.money and cur.money and cur.money ~= prev.money then
    local d = cur.money - prev.money
    out[#out + 1] = ("Or : %s%s"):format(d > 0 and "+" or "−", money(d))
  end
  if prev.bagsFree and cur.bagsFree and cur.bagsFree ~= prev.bagsFree then
    out[#out + 1] = ("Sacs : %d → %d cases libres"):format(prev.bagsFree, cur.bagsFree)
  end
  if prev.quests and cur.quests and cur.quests ~= prev.quests then
    out[#out + 1] = ("Quêtes : %d → %d"):format(prev.quests, cur.quests)
  end
  if prev.zone and cur.zone and prev.zone ~= cur.zone then
    out[#out + 1] = ("Zone : %s → %s"):format(prev.zone, cur.zone)
  end
  return out
end

function M:ShowChanges()
  if not M.char then return end
  local prev = M.char.snap
  local cur = M.Snapshot()
  M.lastChanges = {}
  if opt("changes") and type(prev) == "table" then
    local list = M.Diff(prev, cur)
    M.lastChanges = list
    local lines = {}
    for _, s in ipairs(list) do lines[#lines + 1] = { text = "Depuis la dernière fois : " .. s, prio = 1 } end
    infoLines.changes = lines
    if #list > 0 then
      local short = {}
      for i = 1, math.min(3, #list) do short[#short + 1] = list[i] end
      M:Suggest("changes", "Depuis ta dernière connexion : " .. table.concat(short, " · "), 1,
        { ttl = 90, cooldown = 3600, tab = "info", tooltip = list })
    end
  end
  M.char.snap = cur
  pushInfo()
end

function M:SaveSnapshot() if M.char then M.char.snap = M.Snapshot() end end

---------------------------------------------------------------------------------------------------
-- 49. nouveaux sorts + case de barre conseillée
---------------------------------------------------------------------------------------------------
--- Noms des sorts actifs du grimoire (rangs confondus) : C_SpellBook (moderne) ou GetSpellBookItemName.
function M.SpellbookNames()
  local names, n = {}, 0
  local SB = G.C_SpellBook
  if SB and SB.GetNumSpellBookSkillLines and SB.GetSpellBookSkillLineInfo and SB.GetSpellBookItemInfo then
    local bank = (G.Enum and G.Enum.SpellBookSpellBank and G.Enum.SpellBookSpellBank.Player) or 0
    for i = 1, (try(SB.GetNumSpellBookSkillLines) or 0) do
      local info = try(SB.GetSpellBookSkillLineInfo, i)
      if type(info) == "table" then
        local off, num = info.itemIndexOffset or 0, info.numSpellBookItems or 0
        for j = off + 1, off + num do
          local it = try(SB.GetSpellBookItemInfo, j, bank)
          if type(it) == "table" and it.name and not it.isPassive and not it.isOffSpec then
            if not names[it.name] then names[it.name] = it.spellID or true; n = n + 1 end
          end
        end
      end
    end
    if n > 0 then return names, n end
  end
  if not G.GetNumSpellTabs or not G.GetSpellBookItemName then return names, n end
  local bt = G.BOOKTYPE_SPELL or "spell"
  local last = 0
  for tab = 1, (try(G.GetNumSpellTabs) or 0) do
    local _, _, off, num = try(G.GetSpellTabInfo, tab)
    if off and num then last = math.max(last, off + num) end
  end
  for i = 1, math.min(last, 400) do
    local name = try(G.GetSpellBookItemName, i, bt)
    if name and not try(G.IsPassiveSpell, i, bt) and not names[name] then names[name] = true; n = n + 1 end
  end
  return names, n
end

local BARS = { -- { premier, dernier, libellé, préfixe de raccourci }
  { 1, 12, "barre principale", "ACTIONBUTTON", 0 },
  { 61, 72, "barre en bas à gauche", "MULTIACTIONBAR1BUTTON", 60 },
  { 49, 60, "barre en bas à droite", "MULTIACTIONBAR2BUTTON", 48 },
}
--- Case libre conseillée : texte « barre principale, case 7 (touche 7) » ou nil
function M.FreeSlot()
  local has = API.HasAction or (G.HasAction and function(s) return try(G.HasAction, s) end)  -- C_ActionBar sur Forever
  if not has then return nil end
  for _, b in ipairs(BARS) do
    for s = b[1], b[2] do
      if not has(s) then
        local idx = s - b[5]
        local key = try(G.GetBindingKey, b[4] .. idx)
        return ("%s, case %d%s"):format(b[3], idx, key and (" (touche " .. key .. ")") or ""), s
      end
    end
  end
end

function M:CheckSpells()
  if not M.char then return end
  local names, n = M.SpellbookNames()
  if n == 0 then return end
  local known = M.char.spells
  if type(known) ~= "table" then M.char.spells = names return end -- première fois : on mémorise sans annoncer
  local new = {}
  for name in pairs(names) do if not known[name] then new[#new + 1] = name end end
  table.sort(new)
  local cnt = 0
  local saved = {}
  for k, v in pairs(names) do cnt = cnt + 1 if cnt <= 500 then saved[k] = v end end
  M.char.spells = saved
  if #new == 0 or not opt("newSpells") then return end
  local slot = M.FreeSlot()
  local lines = {}
  for i, name in ipairs(new) do
    if i > 5 then break end
    local txt = "Nouveau sort : « " .. name .. " »" .. (slot and (" — place-le : " .. slot) or " — glisse-le depuis le grimoire (P)")
    lines[#lines + 1] = { text = txt, prio = 2 }
    if i == 1 then M:Suggest("spell", txt .. (#new > 1 and (" (+" .. (#new - 1) .. " autres)") or ""), 2,
      { ttl = 300, cooldown = 10, tab = "info" }) end
  end
  infoLines.spells = lines
  M.lastNew = new
  pushInfo()
end

---------------------------------------------------------------------------------------------------
-- 50. performances : chronométrage par module + ralentissement automatique
---------------------------------------------------------------------------------------------------
local SKIP = { IsEnabled = true, RegisterEvent = true, UnregisterEvent = true, UnregisterAllEvents = true,
  RegisterUnitEvent = true, On = true, Suggest = true, Clear = true, Print = true, Debug = true, OnInit = true }
local stats = {}          -- module -> { ms, calls, max }
local wrapped = setmetatable({}, { __mode = "k" })
local clock = G.debugprofilestop
M.stats = stats

local function finish(st, t0, ...)
  local d = clock() - t0
  st.ms, st.calls = st.ms + d, st.calls + 1
  if d > st.max then st.max = d end
  return ...
end
local function wrapFn(st, fn)
  local w = function(...) return finish(st, clock(), fn(...)) end
  wrapped[w] = true
  return w
end

--- Chronomètre les fonctions des modules (appelé à OnInit, avant leur OnEnable) ; transparent.
function M.InstrumentModules()
  if type(clock) ~= "function" then return 0 end
  local n = 0
  for _, name in ipairs(T.moduleOrder or {}) do
    local mod = T.modules[name]
    if type(mod) == "table" then
      stats[name] = stats[name] or { ms = 0, calls = 0, max = 0 }
      for k, v in pairs(mod) do
        if type(k) == "string" and type(v) == "function" and not SKIP[k] and not wrapped[v] and k:sub(1, 2) ~= "__" then
          mod[k] = wrapFn(stats[name], v)
          n = n + 1
        end
      end
    end
  end
  return n
end

local function totalMS() local s = 0 for _, st in pairs(stats) do s = s + st.ms end return s end
local function addonCPU()
  local upd = G.UpdateAddOnCPUUsage
  local get = (G.C_AddOns and G.C_AddOns.GetAddOnCPUUsage) or G.GetAddOnCPUUsage
  if type(get) ~= "function" then return nil end
  try(upd)
  return sv(try(get, ADDON))
end
local function addonMem()
  local upd = (G.C_AddOns and G.C_AddOns.UpdateAddOnMemoryUsage) or G.UpdateAddOnMemoryUsage
  local get = (G.C_AddOns and G.C_AddOns.GetAddOnMemoryUsage) or G.GetAddOnMemoryUsage
  if type(get) ~= "function" then return nil end
  try(upd)
  return sv(try(get, ADDON))
end

--- Pur : décision de ralentissement. state = { bad, good, slow } ; renvoie slow (bool)
function M.SlowDecision(state, fps, msPerFrame, resting)
  local bad = (fps and fps > 0 and fps < 30) or (msPerFrame and msPerFrame > 2)
  local good = (not fps or fps >= 40) and (not msPerFrame or msPerFrame < 1)
  if bad then state.bad, state.good = (state.bad or 0) + 1, 0
  elseif good then state.good, state.bad = (state.good or 0) + 1, 0 end
  if not state.slow and (state.bad or 0) >= 2 then state.slow = true
  elseif state.slow and (state.good or 0) >= 5 then state.slow = false end
  return state.slow or (resting and true) or false
end

local perf = { bad = 0, good = 0, slow = false, lastMS = 0, lastCPU = nil, lastT = nil, msPerFrame = 0, fps = 0 }
M.perf = perf

function M:SetSlow(on)
  local s = T.settings
  if not s then return end
  if on and not T.perfSlow then
    local base = tonumber(s.refresh) or 3
    M.db.baseRefresh = base
    if base > 0 then s.refresh = math.min(12, base * 2) end
    T.perfSlow = true
    T:Fire("PERF_SLOW", true)
  elseif not on and T.perfSlow then
    if M.db.baseRefresh then s.refresh = M.db.baseRefresh end
    M.db.baseRefresh = nil
    T.perfSlow = false
    T:Fire("PERF_SLOW", false)
  end
end

function M:PerfTick()
  local t = now()
  local fps = sv(try(G.GetFramerate)) or 0
  local ms, cpu = totalMS(), addonCPU()
  if perf.lastT and t > perf.lastT then
    local dt = t - perf.lastT
    local spent = ms - perf.lastMS
    if cpu and perf.lastCPU and cpu > perf.lastCPU then spent = math.max(spent, cpu - perf.lastCPU) end
    local frames = math.max(1, fps * dt)
    perf.msPerFrame, perf.fps = spent / frames, fps
    if opt("autoSlow") then
      local resting = opt("cityThrottle") and sv(try(G.IsResting)) and not T:InCombat()
      M:SetSlow(M.SlowDecision(perf, fps, perf.msPerFrame, resting))
    elseif T.perfSlow then
      M:SetSlow(false)
    end
  end
  perf.lastT, perf.lastMS, perf.lastCPU = t, ms, cpu
end

function M:PerfReport()
  local out = {}
  local mem = addonMem()
  local cpu = addonCPU()
  local up = math.max(1, now() - (M._t0 or now()))
  out[#out + 1] = ("TreeAI : %s Ko de mémoire · %.2f ms/image · %d FPS%s"):format(
    mem and fmt(mem) or "?", perf.msPerFrame or 0, math.floor((perf.fps or 0) + 0.5),
    T.perfSlow and " · RALENTI (minuteries ×2)" or "")
  if cpu and cpu > 0 then out[#out + 1] = ("CPU total (profilage) : %s ms"):format(fmt(cpu)) end
  local list = {}
  for name, st in pairs(stats) do if st.calls > 0 then list[#list + 1] = { name = name, st = st } end end
  table.sort(list, function(a, b) return a.st.ms > b.st.ms end)
  for i, e in ipairs(list) do
    if i > 8 then break end
    out[#out + 1] = ("  %s : %.1f ms (%.2f ms/min, %d appels, pic %.1f ms)"):format(
      e.name, e.st.ms, e.st.ms / up * 60, e.st.calls, e.st.max)
  end
  if #list == 0 then out[#out + 1] = "  (chronométrage par module : option « Mesurer le coût » puis /reload)" end
  local lines = {}
  for _, s in ipairs(out) do lines[#lines + 1] = { text = s, prio = 1 } end
  infoLines.perf = lines
  pushInfo()
  return out
end

---------------------------------------------------------------------------------------------------
-- cycle de vie
---------------------------------------------------------------------------------------------------
function M:OnInit()
  if opt("timing") then M.instrumented = M.InstrumentModules() end
  -- une valeur ralentie restée dans la sauvegarde (plantage) est rétablie
  if M.db and M.db.baseRefresh and T.settings then T.settings.refresh = M.db.baseRefresh; M.db.baseRefresh = nil end
end

function M:OnEnable()
  M._t0 = now()
  M:ShowChanges()
  T:After(3, function() M:CheckSpells() end)
  local pending = false
  local function spellsLater()
    if pending then return end
    pending = true
    T:After(1, function() pending = false M:CheckSpells() end)
  end
  self:RegisterEvent("SPELLS_CHANGED", spellsLater)
  self:RegisterEvent("LEARNED_SPELL_IN_TAB", spellsLater)
  self:RegisterEvent("LEARNED_SPELL_IN_SKILL_LINE", spellsLater)   -- nom Forever / Mainline
  self:RegisterEvent("PLAYER_LOGOUT", function() M:SetSlow(false) M:SaveSnapshot() end)
  self:On("SUMMARY", function() M:WriteSummary() end)
  if M._perfTicker then M._perfTicker:Cancel() end
  M._perfTicker = T:NewTicker(2, function() if M:IsEnabled() then M:PerfTick() end end)
end

function M:OnDisable()
  if M._perfTicker then M._perfTicker:Cancel(); M._perfTicker = nil end
  M:SetSlow(false)
end

local lastSnap = 0
function M:AutoRefresh()
  local t = now()
  if t - lastSnap >= 60 then lastSnap = t M:SaveSnapshot() end -- filet si la déconnexion n'écrit rien
end

function M:WriteSummary()
  local s = T:Summary()
  if type(s) ~= "table" then return end
  s.comfort = { changes = M.lastChanges, newSpells = M.lastNew, msPerFrame = perf.msPerFrame,
    slow = T.perfSlow and true or false }
end

T:RegisterSlash("perf", function()
  if T.PrintPerf then T:PrintPerf() end   -- (LITE) le cœur mesure tout ; Confort ajoute FPS / ms par image
  if M:IsEnabled() then for _, line in ipairs(M:PerfReport()) do T:Print(line) end end
end, "coût CPU / mémoire de TreeAI par module")
T:RegisterSlash("nouveau", function()
  local l = M.lastChanges or {}
  if #l == 0 then T:Print("Rien de neuf depuis la dernière connexion.") return end
  for _, s in ipairs(l) do T:Print(s) end
end, "ce qui a changé depuis la dernière connexion")
