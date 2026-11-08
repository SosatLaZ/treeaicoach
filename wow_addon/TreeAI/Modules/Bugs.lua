-- TreeAI — module Bugs (agent EXPORT) : « /tai erreurs » (alias « /tai export », « /tai bug ») rassemble TOUT le diagnostic dans
-- TreeAIDB.diag.export = { at, version, build, interface, lines = {...} } : erreurs Lua (diag.errors), actions
-- bloquées par le jeu (diag.blocked : fonction, contexte, nombre), autotest (diag.selftest), macros (diag.macros),
-- coût des modules (/tai perf), réglages, modules actifs, autres addons (nom + version) et une note.
-- Un addon ne peut pas écrire de fichier : au /reload le jeu sauvegarde TreeAIDB, et l'appli TreeAI Coach en
-- tire « TreeAI-erreurs.txt ». Capture aussi les erreurs Lua hors safecall dont la pile cite TreeAI, en CHAÎNANT
-- le gestionnaire d'erreurs existant (jamais remplacé : on appelle toujours le précédent).
-- Lecture seule : aucun appel protégé. Lua 5.1, aucune globale.
local T = _G.TreeAI
if not (T and T.RegisterModule) then return end
local G = _G

local MAX_LINES, MAX_BYTES = 200, 40000

local function pass(ok, ...) if ok then return ... end return nil end
local function try(fn, ...) if type(fn) ~= "function" then return nil end return pass(pcall(fn, ...)) end
local function wall() return try(G.time) or try(G.GetServerTime) or 0 end
local function secret(v) return T.API and T.API.IsSecret and T.API.IsSecret(v) or false end
local function str(v, n)
  if secret(v) then return "<secret>" end
  local s = tostring(v == nil and "" or v):gsub("|c%x%x%x%x%x%x%x%x", ""):gsub("|r", ""):gsub("[\r\n]+", " ")
  n = n or 200
  if #s > n then s = s:sub(1, n - 3) .. "..." end
  return s
end
local function stamp(t)
  t = tonumber(t)
  if not t or t <= 0 then return "?" end
  return try(G.date, "%d/%m %H:%M", t) or tostring(t)
end
local function diag()
  local db = T.db or G.TreeAIDB
  if type(db) ~= "table" then return nil end
  db.diag = type(db.diag) == "table" and db.diag or {}
  return db.diag
end

local M = T:RegisterModule("Bugs", {
  title = "Erreurs (export)",
  defaults = { enabled = true },
  options = {
    { type = "button", label = "Préparer le fichier d'erreurs", onClick = function() T.Bugs.Build(false) end },
  },
})
T.Bugs = M

---------------------------------------------------------------------------------------------------
-- Erreurs Lua hors safecall : on chaîne le gestionnaire (BugSack, Blizzard…) et on ne garde que les nôtres
---------------------------------------------------------------------------------------------------
local busy = false
function M.OnLuaError(err)
  if busy then return end
  busy = true
  pcall(function()
    local msg = str(err, 400)
    local stack = try(G.debugstack, 3) or ""
    if type(stack) ~= "string" then stack = "" end
    if not (msg:find("TreeAI", 1, true) or stack:find("AddOns[/\\]TreeAI[/\\]")) then return end
    if T.RecordError then
      local mod = msg:match("Modules[/\\](%w+)%.lua") or stack:match("Modules[/\\](%w+)%.lua") or "jeu"
      T:RecordError(msg, mod .. ":handler")
    end
  end)
  busy = false
end

function M.HookErrors()
  if M._hooked then return end
  local get, set = G.geterrorhandler, G.seterrorhandler
  if type(get) ~= "function" or type(set) ~= "function" then return end
  local prev = try(get)
  M._hooked = true
  M._prevHandler = prev
  try(set, function(err, ...)
    M.OnLuaError(err)
    if type(prev) == "function" then return prev(err, ...) end
  end)
end

---------------------------------------------------------------------------------------------------
-- Construction du rapport
---------------------------------------------------------------------------------------------------
local function addonList()
  local out = {}
  local CA = type(G.C_AddOns) == "table" and G.C_AddOns or {}
  local num = try(CA.GetNumAddOns or G.GetNumAddOns) or 0
  local info = CA.GetAddOnInfo or G.GetAddOnInfo
  local meta = CA.GetAddOnMetadata or G.GetAddOnMetadata
  local loaded = CA.IsAddOnLoaded or G.IsAddOnLoaded
  for i = 1, math.min(tonumber(num) or 0, 120) do
    local name = try(info, i)
    if type(name) == "string" and name ~= "TreeAI" and try(loaded, name) then
      local v = try(meta, name, "Version")
      out[#out + 1] = str(name, 40) .. (v and (" " .. str(v, 20)) or "")
    end
  end
  table.sort(out)
  return out
end

function M.Collect(note)
  local L = {}
  local function add(s) if #L < MAX_LINES then L[#L + 1] = str(s, 300) end end
  local function addList(head, items)   -- longues listes : plusieurs lignes, rien de coupé
    local cur = ("%s (%d) : "):format(head, #items)
    for i, it in ipairs(items) do
      local piece = it .. (i < #items and ", " or "")
      if #cur + #piece > 280 then add(cur); cur = "  " end
      cur = cur .. piece
    end
    add(cur)
  end
  local d = diag() or {}
  local v, b, _, iface = try(G.GetBuildInfo)
  local tocv = try(G.C_AddOns and G.C_AddOns.GetAddOnMetadata or G.GetAddOnMetadata, "TreeAI", "Version")
  add(("TreeAI %s (toc %s) · client %s.%s · interface %s · forever %s"):format(str(T.version), str(tocv or "?"),
    str(v or "?"), str(b or "?"), str(iface or "?"), tostring(type(rawget(G, "issecretvalue")) == "function")))
  if note and note ~= "" then add("Note : " .. str(note, 200)) end

  local errs = type(d.errors) == "table" and d.errors or {}
  add(("== Erreurs Lua : %d"):format(#errs))
  for i = #errs, 1, -1 do   -- les plus récentes d'abord
    local e = errs[i]
    if type(e) == "table" then
      add(("[%s] %s ×%d%s — %s"):format(stamp(e.t), str(e.module, 30), tonumber(e.n) or 1,
        e.combat and " (combat)" or "", str(e.msg, 220)))
    end
  end

  local bl = type(d.blocked) == "table" and d.blocked or {}
  local byFn, order = {}, {}
  for i = #bl, 1, -1 do
    local e = bl[i]
    if type(e) == "table" then
      local k = str(e.func, 60) .. "|" .. str(e.kind, 20)
      if not byFn[k] then byFn[k] = { e = e, n = 0 }; order[#order + 1] = k end
      byFn[k].n = byFn[k].n + (tonumber(e.n) or 1)
    end
  end
  add(("== Actions bloquées : %d"):format(#bl))
  for _, k in ipairs(order) do
    local e, n = byFn[k].e, byFn[k].n
    add(("[%s] %s (%s) ×%d pendant %s%s"):format(stamp(e.t), str(e.func, 60), str(e.kind, 20), n,
      str(e.ctx or "?", 60), e.combat and " (combat)" or ""))
  end

  local st = type(d.selftest) == "table" and d.selftest or nil
  if st then
    add(("== Autotest du %s : %d ok, %d à vérifier, %d en échec"):format(stamp(st.t), tonumber(st.ok) or 0,
      tonumber(st.warn) or 0, tonumber(st.ko) or 0))
    for _, it in ipairs(type(st.items) == "table" and st.items or {}) do
      if type(it) == "table" and it.status ~= "ok" and it.status ~= "na" then
        add(("%s %s : %s"):format(str(it.status, 6), str(it.id, 20), str(it.detail, 160)))
      end
    end
  else
    add("== Autotest : jamais lancé (/tai test)")
  end

  local dm = type(d.macros) == "table" and d.macros or nil
  if dm then
    add(("== Macros (%s) : %s ok, %s ko"):format(stamp(dm.t), str(dm.ok or "?", 8), str(dm.ko or "?", 8)))
    for k, val in pairs(dm) do
      if k ~= "t" and k ~= "ok" and k ~= "ko" and type(val) ~= "table" then add(("  %s = %s"):format(str(k, 30), str(val, 120))) end
    end
  end

  add("== Coût des modules (/tai perf)")
  local perf = T.PerfLines and try(T.PerfLines, T) or {}
  for _, s in ipairs(type(perf) == "table" and perf or {}) do add(s) end

  local on, off = {}, {}
  for _, n in ipairs(T.moduleOrder or {}) do
    local m = T.modules and T.modules[n]
    if m then if m._active then on[#on + 1] = n else off[#off + 1] = n end end
  end
  addList("== Modules actifs", on)
  if #off > 0 then addList("== Modules éteints", off) end

  local db = T.db or G.TreeAIDB
  local set = type(db) == "table" and type(db.settings) == "table" and db.settings or {}
  local keys = {}
  for k, val in pairs(set) do if type(k) == "string" and type(val) ~= "table" then keys[#keys + 1] = k end end
  table.sort(keys)
  local parts = {}
  for _, k in ipairs(keys) do parts[#parts + 1] = k .. "=" .. str(set[k], 20) end
  addList("== Réglages", parts)
  addList("== Autres addons", addonList())

  -- plafond 40 Ko
  local total, cut = 0, #L
  for i = 1, #L do
    total = total + #L[i] + 1
    if total > MAX_BYTES then cut = i - 1; break end
  end
  for i = #L, cut + 1, -1 do L[i] = nil end
  return { at = wall(), version = str(T.version), toc = tocv and str(tocv) or nil, build = str(v) .. "." .. str(b),
           interface = iface, errors = #errs, blocked = #bl, lines = L }
end

function M.Build(quiet, note)
  if T.SelfTest and T.SelfTest.Run and not (T.InCombat and T:InCombat()) then pcall(T.SelfTest.Run, true) end
  local ok, rep = pcall(M.Collect, note)
  local d = diag()
  if not ok then rep = { at = wall(), version = str(T.version), lines = { "Échec de l'export : " .. str(rep, 200) } } end
  if d then d.export = rep end
  if not quiet then T:Print("Erreurs prêtes : fais /reload, TreeAI les écrit dans TreeAI-erreurs.txt") end
  if M.PushTab then pcall(M.PushTab) end
  return rep
end

function M.PushTab()
  if type(T.SetTabLines) ~= "function" then return end
  T:SetTabLines("info", {
    { text = "Erreurs pour TreeAI : préparer le fichier", label = "Erreurs", control = true, prio = 1,
      tooltip = { "Rassemble erreurs, blocages et autotest.", "Puis /reload : TreeAI écrit TreeAI-erreurs.txt." },
      onClick = function() M.Build(false) end },
  }, "bugs")
end

function M:OnEnable() M.PushTab() end

M.HookErrors()
local function slashFn(rest) M.Build(false, rest) end
T:RegisterSlash("erreurs", slashFn, "préparer TreeAI-erreurs.txt (puis /reload)")
T:RegisterSlash("bug", slashFn, "comme /tai erreurs")
-- /tai export : le fichier d'erreurs ET l'export du planificateur (module Export, hors combat)
T:RegisterSlash("export", function(rest)
  M.Build(false, rest)
  local X = T.Export
  if X and X.Rebuild and not (T.InCombat and T:InCombat()) then pcall(X.Rebuild, X) end
end, "erreurs + données du planificateur pour TreeAI Coach (puis /reload)")
