-- TreeAI — module SelfTest (agent AUDIT) : « /tai test » vérifie EN JEU chaque famille d'API dont TreeAI dépend
-- (✓ / ✗ / – par point), la santé de chaque module (actif, dernière erreur, coût CPU) et écrit un rapport
-- compact dans TreeAIDB.diag.selftest (lu par l'appli TreeAI, à envoyer pour un correctif).
-- Garde aussi les 30 dernières erreurs Lua de nos modules dans TreeAIDB.diag.errors (module + message).
-- Lecture seule : aucun appel protégé, aucune requête HV (ReplicateItems a un délai de 15 min : jamais lancé
-- ici), aucune macro créée. Lua 5.1, aucune globale, toutes les API testées (pcall).
local T = _G.TreeAI
if not T or not T.RegisterModule then return end
local G = _G
local API = T.API or {}

local MAX_ERRORS = 30
local pendingErrors = {}   -- erreurs survenues avant le chargement de TreeAIDB

local function pass(ok, ...) if ok then return ... end return nil end
local function try(fn, ...) if type(fn) ~= "function" then return nil end return pass(pcall(fn, ...)) end
local function secret(v) return API.IsSecret and API.IsSecret(v) or false end
local function wall() return try(G.time) or try(G.GetServerTime) or 0 end
local function cut(s, n) s = tostring(s or "") if #s > n then return s:sub(1, n - 1) .. "…" end return s end

---------------------------------------------------------------------------------------------------
-- Erreurs Lua de nos modules -> TreeAIDB.diag.errors (30 dernières, doublons regroupés)
---------------------------------------------------------------------------------------------------
local function diag()
  local db = T.db or G.TreeAIDB
  if type(db) ~= "table" then return nil end
  db.diag = type(db.diag) == "table" and db.diag or {}
  return db.diag
end

local function moduleOf(ctx)
  ctx = tostring(ctx or "")
  local m = ctx:match("^([^:]+)")
  return (m and m ~= "") and m or "?"
end

local function storeError(e)
  local d = diag()
  if not d then pendingErrors[#pendingErrors + 1] = e; return end
  local list = type(d.errors) == "table" and d.errors or {}
  local last = list[#list]
  if last and last.msg == e.msg and last.module == e.module then
    last.n, last.t = (last.n or 1) + 1, e.t
  else
    list[#list + 1] = e
    while #list > MAX_ERRORS do table.remove(list, 1) end
  end
  d.errors = list
end

function T:RecordError(err, ctx)
  if secret(err) then err = "<valeur secrète>" end
  local msg = cut((tostring(err or "?"):gsub("^Interface\\AddOns\\TreeAI\\", "")), 300)
  local mod = moduleOf(ctx or T._ctx)
  if mod == "?" or mod == "action" then mod = msg:match("Modules[/\\](%w+)%.lua") or mod end   -- fichier fautif
  local e = { t = wall(), module = mod, ctx = cut(ctx or T._ctx or "", 60), msg = msg, n = 1,
              combat = (T.InCombat and T:InCombat()) or false }
  storeError(e)
  T._modErrors = T._modErrors or {}
  local me = T._modErrors[mod] or { n = 0 }
  me.n, me.last, me.t = me.n + 1, msg, e.t
  T._modErrors[mod] = me
end
T._errorHook = function(err, ctx) T:RecordError(err, ctx) end   -- appelé par safecall (Core)

---------------------------------------------------------------------------------------------------
-- Tests : chaque test rend status ("ok" | "ko" | "na" | "warn"), détail
---------------------------------------------------------------------------------------------------
local function ns(name) local t = rawget(G, name) return type(t) == "table" and t or nil end
local function fn(t, k) return t and type(t[k]) == "function" and t[k] or nil end
local function num(v) if secret(v) then return nil end return tonumber(v) end

local TESTS = {}
local function add(id, label, f) TESTS[#TESTS + 1] = { id = id, label = label, f = f } end

add("client", "Client / Forever", function()
  local v, b, _, iface = try(G.GetBuildInfo)
  local forever = type(rawget(G, "issecretvalue")) == "function"
  return "ok", ("%s (%s) interface %s%s"):format(tostring(v), tostring(b), tostring(iface),
    forever and " · valeurs secrètes actives (famille Forever/Midnight)" or "")
end)

add("questlog", "Journal de quêtes (C_QuestLog.GetInfo)", function()
  local QL = ns("C_QuestLog")
  if not (fn(QL, "GetNumQuestLogEntries") and fn(QL, "GetInfo")) then
    return G.GetQuestLogTitle and "warn" or "ko", "C_QuestLog absent" .. (G.GetQuestLogTitle and " (repli Era)" or "")
  end
  local n = num(try(QL.GetNumQuestLogEntries)) or 0
  local quests, objOK = 0, nil
  for i = 1, n do
    local q = try(QL.GetInfo, i)
    if type(q) == "table" and not q.isHeader and q.questID then
      quests = quests + 1
      if objOK == nil and fn(QL, "GetQuestObjectives") then objOK = type(try(QL.GetQuestObjectives, q.questID)) == "table" end
    end
  end
  if objOK == false then return "ko", quests .. " quêtes, GetQuestObjectives ne répond pas" end
  return "ok", ("%d quêtes%s"):format(quests, objOK and ", objectifs lisibles" or "")
end)

add("bags", "Sacs (C_Container)", function()
  local C = ns("C_Container")
  if not fn(C, "GetContainerNumSlots") then return G.GetContainerNumSlots and "warn" or "ko", "C_Container absent" end
  local slots = num(try(C.GetContainerNumSlots, 0)) or 0
  return slots > 0 and "ok" or "ko", ("sac à dos : %d cases"):format(slots)
end)

add("items", "Objets (C_Item)", function()
  local C = ns("C_Item")
  if not (fn(C, "GetItemInfo") or fn(C, "GetItemNameByID")) then
    return G.GetItemInfo and "warn" or "ko", "C_Item absent" .. (G.GetItemInfo and " (repli Era)" or "")
  end
  local n = try(C.GetItemNameByID or C.GetItemInfo, 6948)   -- Pierre de foyer
  return "ok", n and ("6948 = " .. tostring(n)) or "en cache au prochain essai"
end)

add("spells", "Sorts (C_Spell, C_SpellBook)", function()
  local S, SB = ns("C_Spell"), ns("C_SpellBook")
  if not fn(S, "GetSpellInfo") then
    return G.GetSpellInfo and "warn" or "ko", "C_Spell absent" .. (G.GetSpellInfo and " (repli Era)" or "")
  end
  local info = try(S.GetSpellInfo, 6603)   -- Attaque
  local known = fn(SB, "IsSpellKnown") and try(SB.IsSpellKnown, 6603)
  if type(info) ~= "table" then return "ko", "GetSpellInfo(6603) vide" end
  return "ok", ("%s%s"):format(tostring(info.name), SB and (known and ", connu" or ", C_SpellBook ok") or ", C_SpellBook absent")
end)

add("auras", "Auras (C_UnitAuras)", function()
  local U = ns("C_UnitAuras")
  if not fn(U, "GetAuraDataByIndex") then return G.UnitAura and "warn" or "ko", "C_UnitAuras absent" end
  local n = #(API.Auras and API.Auras("player", "HELPFUL") or {})
  return "ok", n .. " amélioration(s) lisible(s) sur toi"
end)

add("pet", "Familier (C_PetInfo)", function()
  if T:PlayerClass() ~= "HUNTER" then return "na", "pas chasseur" end
  local P = ns("C_PetInfo")
  if not fn(P, "GetPetHappiness") then return G.GetPetHappiness and "warn" or "ko", "C_PetInfo absent" end
  local h = try(P.GetPetHappiness)
  local food = API.PetFoodTypes and API.PetFoodTypes() or {}
  return "ok", ("humeur %s, régime : %s"):format(tostring(h or "-"), #food > 0 and table.concat(food, ", ") or "-")
end)

add("skills", "Compétences (C_SkillInfo)", function()
  local lines = API.SkillLines and API.SkillLines() or {}
  if #lines == 0 then return "ko", "aucune ligne (C_SkillInfo / GetSkillLineInfo absents ?)" end
  return "ok", #lines .. " lignes (armes, métiers)"
end)

add("map", "Carte et position (C_Map)", function()
  local M = ns("C_Map")
  local map = fn(M, "GetBestMapForUnit") and num(try(M.GetBestMapForUnit, "player"))
  if not map then return "ko", "GetBestMapForUnit(player) vide" end
  local pos = fn(M, "GetPlayerMapPosition") and try(M.GetPlayerMapPosition, map, "player")
  local pin = fn(M, "CanSetUserWaypointOnMap") and try(M.CanSetUserWaypointOnMap, map)
  return pos and "ok" or "warn", ("carte %d, position %s, repère %s"):format(map, pos and "ok" or "absente (instance ?)",
    pin and "possible" or "non")
end)

add("auction", "Hôtel des ventes (C_AuctionHouse)", function()
  local A = ns("C_AuctionHouse")
  if not A then return (G.QueryAuctionItems and "warn" or "ko"), "C_AuctionHouse absent" end
  local rep = fn(A, "ReplicateItems") and fn(A, "GetNumReplicateItems")
  local ready = fn(A, "IsThrottledMessageSystemReady") and try(A.IsThrottledMessageSystemReady)
  local shown = G.AuctionHouseFrame and try(G.AuctionHouseFrame.IsShown, G.AuctionHouseFrame)
  return rep and "ok" or "ko", ("ReplicateItems %s, HV %s%s"):format(rep and "présent" or "ABSENT",
    shown and "ouvert" or "fermé", shown and (ready and ", limiteur prêt" or ", limiteur occupé") or "")
end)

add("macros", "Macros : écriture permise maintenant ?", function()
  if not (G.CreateMacro and G.EditMacro) then return "ko", "CreateMacro absent" end
  local g, c = try(G.GetNumMacros)
  local count = ("%s/%s générales, %s/%s perso"):format(tostring(g or "?"), tostring(G.MAX_ACCOUNT_MACROS or 120),
    tostring(c or "?"), tostring(G.MAX_CHARACTER_MACROS or 18))
  if T:InCombat() or try(G.InCombatLockdown) then return "warn", "non : en combat · " .. count end
  if T.InUserAction and not T:InUserAction() then return "warn", "seulement pendant un clic / une commande · " .. count end
  local d = diag()
  local blocked = d and type(d.blocked) == "table" and #d.blocked or 0
  return "ok", "oui (commande du joueur, hors combat) · " .. count .. (blocked > 0 and (" · " .. blocked .. " action(s) bloquée(s) notée(s)") or "")
end)

add("comms", "Messages d'addon (C_ChatInfo)", function()
  local C = ns("C_ChatInfo")
  if not fn(C, "SendAddonMessage") then return G.SendAddonMessage and "warn" or "ko", "C_ChatInfo absent" end
  local reg = fn(C, "IsAddonMessagePrefixRegistered") and try(C.IsAddonMessagePrefixRegistered, T.PREFIX)
  local restricted = fn(C, "AreOutgoingAddonChatMessagesRestricted") and try(C.AreOutgoingAddonChatMessagesRestricted)
  if restricted then return "warn", "envoi bloqué ici (donjon / combat de boss)" end
  local me = try(G.UnitName, "player")
  local res
  if me and not secret(me) then   -- message à soi-même (ignoré par TreeAI à la réception) : teste l'envoi réel
    local ok, r = pcall(C.SendAddonMessage, T.PREFIX, "0~SELFTEST~0~1/1~r", "WHISPER", me)
    res = ok and (r == nil and "ok" or tostring(r)) or "erreur"
  end
  local Enum = rawget(G, "Enum")
  local okCode = Enum and Enum.SendAddonMessageResult and Enum.SendAddonMessageResult.Success
  local good = res == "ok" or res == "true" or res == "0" or (okCode ~= nil and res == tostring(okCode))
  return (good and reg ~= false) and "ok" or "ko", ("préfixe %s %s, envoi : %s"):format(T.PREFIX,
    reg == nil and "?" or (reg and "enregistré" or "NON enregistré"), tostring(res or "-"))
end)

add("questie", "QuestieDB (contrat)", function()
  local C = T.modules and T.modules.Compass
  local db = C and C.Questie and try(C.Questie)
  if not db then
    if rawget(G, "QuestieLoader") or rawget(G, "LibQuestieDB") then return "ko", "Questie chargé mais base illisible" end
    return "na", "Questie / QuestieDB non installé"
  end
  local name = fn(db, "QueryQuestSingle") and try(db.QueryQuestSingle, 7, "name")
  return name and "ok" or "warn", name and ("quête 7 = " .. tostring(name)) or "QueryQuestSingle sans réponse"
end)

add("spf", "Shortest Path Forever", function()
  local C = T.modules and T.modules.Compass
  local A = C and C.SPF and C.SPF()
  if not A then return "na", "non installé" end
  local nav = type(A.Navigate) == "function"
  return "ok", "API.Estimate présent" .. (nav and ", Navigate présent" or ", Navigate absent")
end)

add("tomtom", "TomTom", function()
  local TT = rawget(G, "TomTom")
  if type(TT) ~= "table" then return "na", "non installé" end
  return type(TT.AddWaypoint) == "function" and "ok" or "ko", "AddWaypoint " .. (type(TT.AddWaypoint) == "function" and "présent" or "absent")
end)

add("nameplates", "Barres de nom (C_NamePlate)", function()
  local N = ns("C_NamePlate")
  if not fn(N, "GetNamePlates") then return "ko", "C_NamePlate.GetNamePlates absent" end
  local list = try(N.GetNamePlates)
  return "ok", (type(list) == "table" and #list or 0) .. " visible(s)"
end)

add("secrets", "Valeurs secrètes (combat)", function()
  if type(rawget(G, "issecretvalue")) ~= "function" then return "na", "client sans valeurs secrètes" end
  local hp = try(G.UnitHealth, "player")
  local tgt = try(G.UnitExists, "target") and try(G.UnitHealth, "target")
  return "ok", ("issecretvalue présent · tes PV %s · PV cible %s%s"):format(secret(hp) and "secrets" or "lisibles",
    tgt == nil and "-" or (secret(tgt) and "secrets" or "lisibles"), T:InCombat() and " (en combat)" or " (hors combat)")
end)

add("combatlog", "Journal de combat (CLEU)", function()
  if T.CombatLogAllowed and not T:CombatLogAllowed() then
    return "ok", "désactivé volontairement (interdit aux addons sur Forever : sinon « TreeAI a été bloqué »)"
  end
  return "ok", "autorisé sur ce client"
end)

add("settings", "Options (Settings)", function()
  local S = rawget(G, "Settings")
  if type(S) == "table" and type(S.RegisterCanvasLayoutCategory) == "function" then return "ok", "Settings moderne" end
  if G.InterfaceOptions_AddCategory then return "warn", "ancien InterfaceOptions" end
  return "ko", "aucune API d'options"
end)

add("tooltips", "Infobulles (TooltipDataProcessor)", function()
  local P = rawget(G, "TooltipDataProcessor")
  if type(P) == "table" and type(P.AddTooltipPostCall) == "function" then return "ok", "AddTooltipPostCall présent" end
  return "warn", "repli HookScript (ancien client)"
end)

add("talents", "Talents", function()
  local CT = ns("C_ClassTalents")
  if fn(CT, "HasUnspentTalentPoints") then
    local has, a, b = try(CT.HasUnspentTalentPoints)
    return "ok", has and ("points libres : " .. ((num(a) or 0) + (num(b) or 0))) or "aucun point libre"
  end
  if G.UnitCharacterPoints or G.GetUnspentTalentPoints then return "warn", "API Era" end
  return "ko", "aucune API de points de talent"
end)

---------------------------------------------------------------------------------------------------
-- Santé des modules + rapport
---------------------------------------------------------------------------------------------------
local ICON = {
  ok = "|TInterface\\RaidFrame\\ReadyCheck-Ready:0|t", ko = "|TInterface\\RaidFrame\\ReadyCheck-NotReady:0|t",
  warn = "|TInterface\\RaidFrame\\ReadyCheck-Waiting:0|t", na = "|cff888888–|r",
}
local MARK = { ok = "✓", ko = "✗", warn = "!", na = "–" }

local function moduleHealth()
  local out = {}
  local C = T.modules and T.modules.Comfort
  local cstats = C and type(C.stats) == "table" and C.stats or {}
  for name, mod in T:IterModules() do
    local st = cstats[name] or (T._stats and T._stats[name]) or {}
    local er = T._modErrors and T._modErrors[name] or {}
    out[#out + 1] = {
      name = name, enabled = (mod.IsEnabled and mod:IsEnabled()) and true or false, active = mod._active and true or false,
      errors = er.n or 0, last = er.last, ms = st.ms and math.floor(st.ms * 10 + 0.5) / 10 or nil, calls = st.calls,
    }
  end
  return out
end

local function addonUsage()
  local upd = (G.C_AddOns and G.C_AddOns.UpdateAddOnMemoryUsage) or G.UpdateAddOnMemoryUsage
  local mem = (G.C_AddOns and G.C_AddOns.GetAddOnMemoryUsage) or G.GetAddOnMemoryUsage
  try(upd)
  local kb = num(try(mem, "TreeAI"))
  local cpuGet = (G.C_AddOns and G.C_AddOns.GetAddOnCPUUsage) or G.GetAddOnCPUUsage
  try(G.UpdateAddOnCPUUsage)
  local cpu = num(try(cpuGet, "TreeAI"))
  return kb and math.floor(kb + 0.5) or nil, (cpu and cpu > 0) and math.floor(cpu + 0.5) or nil
end

local M = T:RegisterModule("SelfTest", {
  title = "Autotest",
  defaults = { enabled = true },
  options = {   -- boutons (page d'options du module) : tout se fait aussi sans commande
    { type = "button", label = "Lancer l'autotest", onClick = function() T.SelfTest.Run(false) end },
    { type = "button", label = "Voir les dernières erreurs", onClick = function() T.SelfTest.PrintErrors() end },
  },
})
T.SelfTest = M

function M.Run(quiet)
  local items, counts = {}, { ok = 0, ko = 0, warn = 0, na = 0 }
  for _, t in ipairs(TESTS) do
    local ok, st, detail = pcall(t.f)
    if not ok then st, detail = "ko", "erreur : " .. cut(st, 120) end
    if not MARK[st] then st = "ko" end
    if secret(detail) then detail = "<secret>" end
    detail = cut(detail, 160)
    counts[st] = counts[st] + 1
    items[#items + 1] = { id = t.id, label = t.label, status = st, detail = detail }
  end
  local mods = moduleHealth()
  local kb, cpu = addonUsage()
  local d = diag() or {}
  local nerr = type(d.errors) == "table" and #d.errors or 0
  local lines = {}
  for _, it in ipairs(items) do lines[#lines + 1] = MARK[it.status] .. " " .. it.id .. ": " .. it.detail end
  for _, m in ipairs(mods) do
    if m.errors > 0 or not m.active then
      lines[#lines + 1] = ("%s module %s: %s, %d erreur(s)%s"):format(m.errors > 0 and "✗" or "–", m.name,
        m.active and "actif" or "inactif", m.errors, m.last and (" — " .. cut(m.last, 100)) or "")
    end
  end
  local v, b, _, iface = try(G.GetBuildInfo)
  local report = {
    t = wall(), version = T.version, build = tostring(v) .. "." .. tostring(b), interface = iface,
    forever = type(rawget(G, "issecretvalue")) == "function", combat = T:InCombat(),
    ok = counts.ok, ko = counts.ko, warn = counts.warn, na = counts.na,
    items = items, modules = mods, memoryKB = kb, cpuMS = cpu, errors = nerr,
    text = table.concat(lines, "\n"),
  }
  d.selftest = report
  if M.PushTab then pcall(M.PushTab) end
  if not quiet then
    T:Print(("Autotest TreeAI %s : %d ok, %d à vérifier, %d en échec, %d sans objet."):format(tostring(T.version),
      counts.ok, counts.warn, counts.ko, counts.na))
    for _, it in ipairs(items) do T:Print(ICON[it.status] .. " " .. it.label .. " — " .. it.detail) end
    local bad = 0
    for _, m in ipairs(mods) do
      if m.errors > 0 then
        bad = bad + 1
        T:Print(ICON.ko .. (" module %s : %d erreur(s), dernière : %s"):format(m.name, m.errors, cut(m.last, 120)))
      end
    end
    T:Print(("Modules : %d, en erreur : %d · mémoire %s Ko%s · erreurs gardées : %d (/tai erreurs)."):format(#mods, bad,
      tostring(kb or "?"), cpu and (", CPU " .. cpu .. " ms") or "", nerr))
    T:Print("Rapport enregistré : fais /reload puis envoie-le depuis TreeAI (page WoW).")
  end
  return report
end

function M.PrintErrors()
  local d = diag() or {}
  local list = type(d.errors) == "table" and d.errors or {}
  if #list == 0 then T:Print("Aucune erreur TreeAI gardée.") return end
  T:Print(("%d erreur(s) TreeAI (les plus récentes en bas) :"):format(#list))
  for i = math.max(1, #list - 9), #list do
    local e = list[i]
    T:Print(("[%s] %s%s"):format(tostring(e.module), cut(e.msg, 160), (e.n or 1) > 1 and (" ×" .. e.n) or ""))
  end
end

-- Onglet « Infos » : une ligne cliquable lance l'autotest (+ dernier résultat, erreurs gardées)
function M.PushTab()
  if type(T.SetTabLines) ~= "function" then return end
  local d = diag() or {}
  local r = type(d.selftest) == "table" and d.selftest or nil
  local nerr = type(d.errors) == "table" and #d.errors or 0
  local last = r and (" (dernier : %d ok, %d à vérifier, %d en échec)"):format(r.ok or 0, r.warn or 0, r.ko or 0) or ""
  local lines = {
    -- un bouton de l'onglet (pas une ligne qui prend la place d'un conseil) ; une ligne seulement s'il y a un souci
    { text = "Autotest TreeAI : vérifier le jeu et les modules", label = "Autotest", control = true, prio = 1,
      tooltip = { "Vérifie le jeu et les modules" .. last .. ".", "Écrit un rapport que l'appli TreeAI lit (après /reload)." },
      onClick = function() M.Run(false); M.PushTab() end },
  }
  if r and (r.ko or 0) > 0 then
    lines[#lines + 1] = { text = ("Relance l'autotest : %d point(s) en échec (clic)"):format(r.ko), prio = 2,
      onClick = function() M.Run(false); M.PushTab() end }
  end
  if nerr > 0 then
    lines[#lines + 1] = { text = ("Vois les %d erreurs TreeAI gardées (clic)"):format(nerr), prio = 2,
      onClick = function() M.PrintErrors() end }
  end
  T:SetTabLines("info", lines, "selftest")
end

function M:OnEnable() M.PushTab() end
function M:AutoRefresh()
  local d = diag() or {}
  local n = type(d.errors) == "table" and #d.errors or 0
  if n ~= M._lastN then M._lastN = n; M.PushTab() end
end

function M:OnInit()
  if #pendingErrors > 0 then
    local p = pendingErrors
    pendingErrors = {}
    for _, e in ipairs(p) do storeError(e) end
  end
end

T:RegisterSlash("test", function() M.Run(false) end, "autotest : API du jeu, modules, rapport pour TreeAI")
T:RegisterSlash("erreurs", function() M.PrintErrors() end, "dernières erreurs Lua de TreeAI")
T:RegisterSlash("demo", function()
  T:Suggest("core:test1", "Exemple : conseil d'information.", 1, { ttl = 20 })
  T:Suggest("core:test2", "Exemple : conseil utile.", 2, { ttl = 20 })
  T:Suggest("core:test3", "Exemple : alerte urgente !", 3, { ttl = 20, sound = false })
end, "afficher des suggestions d'exemple")
