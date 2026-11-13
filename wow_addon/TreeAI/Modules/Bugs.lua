-- TreeAI — module Bugs (agent EXPORT) : « /tai erreurs » (alias « /tai export », « /tai bug ») rassemble TOUT le diagnostic dans
-- TreeAIDB.diag.export = { at, version, build, interface, lines = {...} } : erreurs Lua (diag.errors), actions
-- bloquées par le jeu (diag.blocked : fonction, contexte, nombre), autotest (diag.selftest), macros (diag.macros),
-- coût des modules (/tai perf), réglages, modules actifs, autres addons (nom + version) et une note.
-- Un addon ne peut pas écrire de fichier : au /reload le jeu sauvegarde TreeAIDB, et l'appli TreeAI Coach en
-- tire « TreeAI-erreurs.txt ». Capture aussi les erreurs Lua hors safecall dont la pile cite TreeAI, en CHAÎNANT
-- le gestionnaire d'erreurs existant (jamais remplacé : on appelle toujours le précédent).
-- Lecture seule : aucun appel protégé. Lua 5.1, aucune globale.
local tonumber = function(v) return _G.tonumber(v) end   -- (4.0.8) tonumber(f()) : un 2e retour de f n'est jamais une base
local T = _G.TreeAI
if not (T and T.RegisterModule) then return end
local G = _G

local MAX_LINES, MAX_BYTES = 240, 40000

local function pass(ok, ...) if ok then return ... end return nil end
local function try(fn, ...) if type(fn) ~= "function" then return nil end return pass(pcall(fn, ...)) end
local function wall() return try(G.time) or try(G.GetServerTime) or 0 end
local function secret(v) return T.API and T.API.IsSecret and T.API.IsSecret(v) or false end
local function str(v, n)
  if secret(v) then return "<secret>" end
  local s = tostring(v == nil and "" or v):gsub("|c%x%x%x%x%x%x%x%x", ""):gsub("|r", ""):gsub("[\r\n]+", " ")
  n = n or 200
  return T.Cut(s, n, "...")         -- (4.0) Core : n caractères UTF-8 (rapport à copier : « ... » ASCII)
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
    { type = "button", label = "Préparer le fichier d'erreurs", onClick = function() T.Bugs.Clicked() end },
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
    -- (4.0.8) notre propre gestionnaire est dans la pile : sans ce filtre, TOUTES les erreurs (Questie, OpenMap…)
    -- étaient gardées comme « [Bugs] ». On retire nos lignes de gestionnaire, et une alerte de taint d'un autre addon
    -- (« Lua Taint: Questie ») n'est jamais la nôtre.
    stack = stack:gsub("[^\n]*Modules[/\\]Bugs%.lua[^\n]*\n?", "")
    local taintOf = msg:match("Lua Taint:%s*([%w_%-]+)")
    if taintOf and taintOf ~= "TreeAI" then return end
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
-- (5.0 STABLE) Sonde d'API : quelles fonctions du jeu existent sur CE client et la forme de ce qu'elles rendent
-- (nombre de retours, type des premiers, noms des champs d'une table) — jamais une valeur, jamais un nom de joueur.
-- Lectures seules (aucune fonction d'action n'est appelée : pour elles, seulement « présente / absente »), hors combat.
---------------------------------------------------------------------------------------------------
local function at(path)   -- "C_QuestLog.GetInfo" → fonction (ou nil)
  local v = G
  for part in path:gmatch("[^%.]+") do
    if type(v) ~= "table" then return nil end
    v = rawget(v, part)
    if v == nil then return nil end
  end
  return v
end
local function shape(v, depth)
  if secret(v) then return "secret" end
  local tv = type(v)
  if tv ~= "table" then return tv end
  if depth and depth > 0 then return "table" end
  local keys = {}
  for k, x in pairs(v) do
    if type(k) == "string" then keys[#keys + 1] = k .. ":" .. shape(x, 1) elseif #keys < 1 then keys[#keys + 1] = "[" .. type(k) .. "]:" .. shape(x, 1) end
    if #keys >= 40 then break end
  end
  table.sort(keys)
  for i = #keys, 13, -1 do keys[i] = nil end
  return "table{" .. table.concat(keys, ", ") .. "}"
end
local function packN(ok, ...) return ok, select("#", ...), ... end
-- { chemin, arguments… } : appelée ; { chemin, exists = true } : présence seulement (action, ou argument inconnu)
M.PROBE = {
  { section = "Talents" },
  { "GetNumTalentTabs" }, { "GetTalentTabInfo", 1 }, { "GetNumTalents", 1 }, { "GetTalentInfo", 1, 1 },
  { "GetActiveTalentGroup" }, { "GetNumTalentGroups" }, { "UnitCharacterPoints", "player" },
  { "GetSpecialization" }, { "GetSpecializationInfo", 1 }, { "GetNumSpecializations" },
  { "C_SpecializationInfo.GetSpecialization" }, { "C_SpecializationInfo.GetSpecializationInfo", 1 },
  { "C_SpecializationInfo.GetNumSpecializationsForClassID", exists = true }, { "C_SpecializationInfo.GetTalentInfo", exists = true },
  { "C_SpecializationInfo.GetActiveSpecGroup" },
  { "C_ClassTalents.GetActiveConfigID" }, { "C_ClassTalents.GetConfigIDsBySpecID", exists = true },
  { "C_Traits.GetConfigInfo", exists = true }, { "C_Traits.GetTreeNodes", exists = true }, { "C_Traits.GetNodeInfo", exists = true },
  { "IsSpellKnown", 75 }, { "IsPlayerSpell", 75 }, { "C_SpellBook.IsSpellKnown", 75 }, { "C_SpellBook.IsSpellInSpellBook", 75 },
  { "C_Spell.GetSpellInfo", 75 }, { "GetSpellInfo", 75 }, { "GetNumSpellTabs" }, { "C_SpellBook.GetNumSpellBookSkillLines" },
  { section = "Inspection" },
  { "CanInspect", "player" }, { "NotifyInspect", exists = true }, { "ClearInspectPlayer", exists = true },
  { "GetInspectSpecialization", exists = true }, { "GetInventoryItemLink", "player", 16 }, { "GetInventoryItemID", "player", 16 },
  { "C_PaperDollInfo.GetInspectItemLevel", exists = true }, { "GetAverageItemLevel" },
  { section = "Repères et carte" },
  { "C_Map.GetBestMapForUnit", "player" }, { "C_Map.GetPlayerMapPosition", exists = true }, { "C_Map.SetUserWaypoint", exists = true },
  { "C_Map.HasUserWaypoint" }, { "C_Map.ClearUserWaypoint", exists = true }, { "C_Map.CanSetUserWaypointOnMap", exists = true },
  { "C_SuperTrack.SetSuperTrackedUserWaypoint", exists = true }, { "C_SuperTrack.IsSuperTrackingUserWaypoint" },
  { "C_SuperTrack.GetSuperTrackedQuestID" }, { "UiMapPoint.CreateFromCoordinates", exists = true },
  { "C_Navigation.GetDistance" }, { "UnitPosition", "player" }, { "GetPlayerFacing" }, { "C_TaxiMap.GetAllTaxiNodes", exists = true },
  { section = "Infobulles" },
  { "TooltipDataProcessor.AddTooltipPostCall", exists = true }, { "C_TooltipInfo.GetUnit", exists = true },
  { "C_TooltipInfo.GetBagItem", exists = true }, { "C_TooltipInfo.GetHyperlink", exists = true },
  { "GameTooltip.GetUnit", exists = true }, { "GameTooltip.GetItem", exists = true }, { "GameTooltip.SetUnit", exists = true },
  { section = "Sacs" },
  { "C_Container.GetContainerNumSlots", 0 }, { "C_Container.GetContainerItemInfo", 0, 1 }, { "C_Container.GetContainerItemLink", 0, 1 },
  { "C_Container.GetContainerItemQuestInfo", 0, 1 }, { "C_Container.GetContainerNumFreeSlots", 0 },
  { "C_Container.UseContainerItem", exists = true }, { "C_Container.PickupContainerItem", exists = true },
  { "GetContainerNumSlots", 0 }, { "GetContainerItemInfo", 0, 1 }, { "UseContainerItem", exists = true },
  { "C_Item.GetItemInfo", 6948 }, { "GetItemInfo", 6948 }, { "C_Item.GetItemInfoInstant", 6948 }, { "C_Item.GetItemCount", 6948 },
  { "GetItemCount", 6948 }, { "C_Item.GetItemStats", exists = true }, { "GetItemStats", exists = true },
  { section = "Quêtes" },
  { "C_QuestLog.GetNumQuestLogEntries" }, { "C_QuestLog.GetInfo", 1 }, { "C_QuestLog.GetQuestObjectives", "quest" },
  { "C_QuestLog.IsComplete", "quest" }, { "C_QuestLog.GetNextWaypoint", "quest" }, { "C_QuestLog.GetQuestsOnMap", exists = true },
  { "C_QuestLog.GetTitleForQuestID", exists = true }, { "C_QuestLog.IsQuestFlaggedCompleted", 1 }, { "C_QuestLog.GetAllCompletedQuestIDs", exists = true },
  { "C_QuestLog.UnitIsRelatedToActiveQuest", exists = true }, { "C_QuestLog.GetQuestWatchType", exists = true },
  { "C_QuestLog.AddQuestWatch", exists = true }, { "C_QuestLog.RemoveQuestWatch", exists = true }, { "C_QuestLog.GetNumQuestWatches" },
  { "C_QuestLog.GetQuestTagInfo", exists = true }, { "C_QuestLog.IsFailed", exists = true }, { "GetQuestLogTitle", 1 },
  { "GetNumQuestLogEntries" }, { "GetQuestLogLeaderBoard", exists = true }, { "GetQuestsCompleted", exists = true },
  { "IsQuestFlaggedCompleted", 1 }, { "GetQuestUiMapID", exists = true }, { "UnitIsQuestBoss", exists = true },
  { section = "Divers" },
  { "issecretvalue", exists = true }, { "C_NamePlate.GetNamePlates", exists = true }, { "C_NamePlate.GetNamePlateForUnit", exists = true },
  { "C_UnitAuras.GetAuraDataByIndex", exists = true }, { "UnitAura", exists = true }, { "C_AddOns.GetAddOnMetadata", exists = true },
  { "C_Timer.After", exists = true }, { "debugprofilestop" }, { "GetNumGroupMembers" }, { "C_CombatLog.IsCombatLogRestricted" },
}
function M.ProbeAPI()
  local out = {}
  local quest
  local QL = rawget(G, "C_QuestLog")
  if type(QL) == "table" and QL.GetInfo and QL.GetNumQuestLogEntries then
    for i = 1, math.min(tonumber((try(QL.GetNumQuestLogEntries))) or 0, 40) do
      local info = try(QL.GetInfo, i)
      if type(info) == "table" and not info.isHeader and info.questID and not secret(info.questID) then quest = info.questID break end
    end
  end
  for _, e in ipairs(M.PROBE) do
    local path = e[1]
    if e.section then
      out[#out + 1] = "-- " .. e.section
    else
      local fn = at(path)
      if type(fn) ~= "function" then
        out[#out + 1] = path .. " : " .. (fn == nil and "absente" or ("présente (" .. type(fn) .. ")"))
      elseif e.exists then
        out[#out + 1] = path .. " : présente"
      else
        local args, skip = {}, false
        for i = 2, #e do
          local a = e[i]
          if a == "quest" then a = quest if not a then skip = true end end
          args[#args + 1] = a
        end
        if skip then
          out[#out + 1] = path .. " : présente (journal vide : pas appelée)"
        else
          local r = { packN(pcall(fn, unpack(args, 1, #args))) }
          if not r[1] then
            out[#out + 1] = path .. " : présente, erreur à l'appel"
          else
            local n, parts = r[2], {}
            for i = 1, math.min(n, 5) do parts[i] = shape(r[2 + i]) end
            out[#out + 1] = ("%s : %d retour%s%s"):format(path, n, n > 1 and "s" or "", n > 0 and (" (" .. table.concat(parts, ", ")
              .. (n > 5 and ", …" or "") .. ")") or "")
          end
        end
      end
    end
  end
  return out
end
--- garde la sonde dans TreeAIDB.diag.api (une fois par version et par client ; /tai erreurs la refait)
function M.StoreProbe(force)
  local d = diag()
  if not d or (T.InCombat and T:InCombat()) then return nil end
  local v, b = try(G.GetBuildInfo)
  local key = str(T.version, 20) .. "|" .. str(v or "?", 20) .. "." .. str(b or "?", 12)
  if not force and type(d.api) == "table" and d.api.key == key then return d.api end
  local ok, lines = pcall(M.ProbeAPI)
  if not ok or type(lines) ~= "table" then return nil end
  for i = 1, #lines do lines[i] = str(lines[i], 260) end
  d.api = { key = key, t = wall(), lines = lines }
  return d.api
end

---------------------------------------------------------------------------------------------------
-- (5.0 STABLE) « /tai pics » : les pics de lenteur relevés par le cœur (TreeAI.Spikes), dans la fenêtre — jamais le chat
---------------------------------------------------------------------------------------------------
local ICON = "Interface\\Icons\\"
local picsHub = { group = "outils", id = "pics", icon = ICON .. "INV_Misc_PocketWatch_02", title = "Pics de lenteur",
  desc = "Quand TreeAI a ralenti le jeu : quelle fonction, combien de ms, en combat ou non, combien de joueurs autour",
  tip = "Clic : ouvrir le relevé (il part aussi dans /tai erreurs)",
  fn = function() M.ShowSpikes() end }
function M.SpikeLines(nTop, nLast)
  local Sp = T.Spikes
  if not (Sp and Sp.Lines) then return { "Pics de lenteur : relevé indisponible." } end
  local ok, l = pcall(Sp.Lines, nTop, nLast)
  return (ok and type(l) == "table") and l or { "Pics de lenteur : relevé illisible." }
end
function M.RegisterPage()
  T.HUB_EXTRA = type(T.HUB_EXTRA) == "table" and T.HUB_EXTRA or {}
  local have = false
  for _, e in ipairs(T.HUB_EXTRA) do if e == picsHub then have = true end end
  if not have then T.HUB_EXTRA[#T.HUB_EXTRA + 1] = picsHub end
  local W = T.Window
  if not (W and W.AddPage) or M._page then return end
  M._page = true
  local page = { id = "pics", hidden = true, parent = "settings", label = "Pics de lenteur", icon = picsHub.icon,
    title = "Pics de lenteur", desc = "Quand TreeAI a ralenti le jeu, et quelle fonction",
    render = function(Ly)
      Ly.text("TreeAI chronomètre chacun de ses travaux. Un appel de plus de 8 ms, ou une image où TreeAI dépasse 12 ms, est noté ici "
        .. "(fonction, travail, en combat ou non, barres de nom, groupe). Gardé pour « /tai erreurs » (TreeAI-erreurs.txt).", "S", "muted")
      local WD = T.Watchdog
      local asleep = WD and WD.Asleep and WD.Asleep() or {}
      local btns = { { "Effacer le relevé", function() if T.Spikes and T.Spikes.Reset then T.Spikes.Reset() end end,
        "Repart de zéro (le relevé repart aussi de zéro à chaque nouvelle version)" } }
      if #asleep > 0 then
        btns[#btns + 1] = { "Relancer les fonctions en pause", function() WD.WakeAll() end,
          "Rallume ce que TreeAI a coupé pour garder le jeu fluide (sinon : au prochain /reload)" }
      end
      if Ly.buttons then Ly.buttons(btns, #btns) end
      for _, l in ipairs(M.SpikeLines(30, 30)) do Ly.text(l, "S") end
    end }
  W.AddPage(page)
  local P = W.PAGES   -- page cachée (ouverte depuis Outils / la ligne « mise en pause ») : rangée avant « Options »
  if type(P) == "table" then
    for i = #P, 1, -1 do if P[i] == page then table.remove(P, i) break end end
    local pos = #P + 1
    for i, q in ipairs(P) do if type(q) == "table" and q.id == "options" then pos = i break end end
    table.insert(P, pos, page)
  end
end
--- ouvre le relevé dans la fenêtre ; sans fenêtre (module coupé) : au pied du panneau, jamais dans le chat
function M.ShowSpikes()
  M.RegisterPage()
  local W = T.Window
  if W and W.Open and W.pageIndex and W.pageIndex.pics and (not W.IsEnabled or W:IsEnabled()) then W.Open("pics") return true end
  local l = M.SpikeLines(3, 0)
  if T.ClickNote then T:ClickNote(l[1] .. (l[3] and (" " .. l[3]) or "")) end
  return false
end
T.ShowSpikes = function() return M.ShowSpikes() end

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

  -- (ZONEFIX 4.0.1) journal de la boussole et du trajet (Modules/NavTrace.lua) : les 25 dernières décisions ici, tout
  -- l'anneau (300) dans la section « Trajet / boussole » que l'appli écrit depuis diag.nav
  local NTr = T.NavTrace
  for _, s in ipairs(NTr and NTr.ExportLines and try(NTr.ExportLines, 25) or {}) do add(s) end
  -- (5.0 STABLE) pics de lenteur relevés en jeu (cœur : TreeAI.Spikes) et sonde d'API de ce client
  add("== Pics de lenteur (/tai pics)")
  for _, s in ipairs(M.SpikeLines(15, 15)) do add(s) end
  local api = M.StoreProbe(true)   -- gardée à part (diag.api) : l'appli l'écrit dans sa propre section du fichier
  if type(api) == "table" and type(api.lines) == "table" then
    add(("== API du client : %d fonctions sondées (section « API du client » de TreeAI-erreurs.txt)"):format(#api.lines))
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

--- (4.0) bouton / ligne « Erreurs » : le fichier préparé, la réponse au pied de la fenêtre (jamais le chat) ;
--- /tai erreurs garde la réponse dans le chat (commande tapée)
function M.Clicked()
  M.Build(true)
  if T.ClickNote then T:ClickNote("Fichier d'erreurs prêt : fais /reload, TreeAI l'écrit dans TreeAI-erreurs.txt.") end
end

function M.PushTab()
  if type(T.SetTabLines) ~= "function" then return end
  T:SetTabLines("info", {
    { text = "Erreurs pour TreeAI : préparer le fichier", label = "Erreurs", control = true, prio = 1,
      tooltip = { "Rassemble erreurs, blocages et autotest.", "Puis /reload : TreeAI écrit TreeAI-erreurs.txt." },
      onClick = function() M.Clicked() end },
  }, "bugs")
end

function M:OnEnable()
  M.PushTab()
  M.RegisterPage()
  -- la sonde d'API une fois par version / client, au calme (≈ 100 lectures) : dans les SavedVariables même sans /tai erreurs
  if T.After then T:After(20, function() if not (T.InCombat and T:InCombat()) then M.StoreProbe(false) end end) end
end

M.HookErrors()
if T.On then T:On("READY", "Bugs:page", function() M.RegisterPage() end) end
T:RegisterSlash("pics", function(rest)
  local k = tostring(rest or ""):lower():gsub("%s+", "")
  if k == "reset" or k == "effacer" then if T.Spikes and T.Spikes.Reset then T.Spikes.Reset() end end
  if k == "reveil" or k == "réveil" or k == "relancer" then if T.Watchdog and T.Watchdog.WakeAll then T.Watchdog.WakeAll() end end
  M.ShowSpikes()
end, "pics de lenteur relevés par TreeAI (fenêtre) · /tai pics effacer · /tai pics relancer")
local function slashFn(rest) M.Build(false, rest) end
T:RegisterSlash("erreurs", slashFn, "préparer TreeAI-erreurs.txt (puis /reload)")
T:RegisterSlash("bug", slashFn, "comme /tai erreurs")
-- /tai export : le fichier d'erreurs ET l'export du planificateur (module Export, hors combat)
T:RegisterSlash("export", function(rest)
  M.Build(false, rest)
  local X = T.Export
  if X and X.Rebuild and not (T.InCombat and T:InCombat()) then pcall(X.Rebuild, X) end
end, "erreurs + données du planificateur pour TreeAI Coach (puis /reload)")
