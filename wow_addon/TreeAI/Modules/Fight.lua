-- TreeAI — module Fight (agent X2) : aides de combat PvE, INFORMATIVES uniquement (FEATURES50 n° 26–35).
--  26 compteur de pull (barres de nom autour de toi, élites comprises)
--  27 élite qui patrouille vers toi (distance qui diminue alors que tu ne bouges pas)
--  28 monstres qui fuient (émotes observées, mémorisées par nom de monstre)
--  29 récap de mort + conseil (UNIT_COMBAT, barres de nom, journal de combat seulement s'il existe)
--  30 contrôle PV / mana (et familier) avant d'engager
--  31 sort à interrompre sur la cible (UNIT_SPELLCAST_*), avec ton interruption prête
--  32 minuteur des contrôles posés par toi / ton familier (piège, mouton, assommer…) + rupture
--  33 familier qui perd l'aggro (menace)
--  34 DPS perdu à cause de l'humeur du familier (chasseur)
--  35 conseils de tirs entre deux tirs automatiques (chasseur)
-- WoW Forever n'envoie plus le journal de combat aux addons (valeurs « secrètes ») : tout passe par les
-- événements UNIT_* / C_UnitAuras / barres de nom, chaque valeur testée avec issecretvalue ; le journal
-- de combat (Classic Era) n'est qu'un bonus facultatif. PvP : le cœur filtre déjà (Suggest). Rien n'est
-- fait à la place du joueur : aucun sort, aucune cible, aucun marqueur.
-- Urgent (prio 3) = encadré d'alerte du coin ; rien dans le chat.
local T = _G.TreeAI
if not T or not T.RegisterModule then return end
local API = T.API or {}
local G = _G

local M = T:RegisterModule("Fight", {
  title = "Combat (PvE)",
  defaults = {
    enabled = true, pull = true, pullMin = 3, patrol = true, flee = true, death = true,
    prepull = true, hpPull = 60, manaPull = 40, interrupt = true, cc = true, ccFrame = true,
    petAggro = true, petMood = true, shotTips = true,
  },
  charDefaults = { deaths = {} },
  options = {
    { key = "pull", type = "toggle", label = "Compteur de pull (barres de nom)", section = "Avant le pull" },
    { key = "pullMin", type = "range", label = "Alerte à partir de N monstres", min = 2, max = 6, step = 1 },
    { key = "patrol", type = "toggle", label = "Élite qui patrouille vers toi" },
    { key = "prepull", type = "toggle", label = "Contrôle PV / mana avant d'engager" },
    { key = "hpPull", type = "range", label = "Seuil PV avant pull (%)", min = 30, max = 90, step = 5 },
    { key = "manaPull", type = "range", label = "Seuil mana avant pull (%)", min = 10, max = 80, step = 5 },
    { key = "interrupt", type = "toggle", label = "Sort à interrompre sur la cible", section = "Pendant le combat" },
    { key = "cc", type = "toggle", label = "Minuteur des contrôles (piège, mouton…)" },
    { key = "ccFrame", type = "toggle", label = "Petit cadre de compte à rebours des contrôles" },
    { key = "flee", type = "toggle", label = "Monstres qui fuient" },
    { key = "petAggro", type = "toggle", label = "Familier qui perd l'aggro" },
    { key = "death", type = "toggle", label = "Récap de mort + conseil", section = "Après" },
    { key = "petMood", type = "toggle", label = "Chasseur : DPS perdu (humeur du familier)", section = "Chasseur" },
    { key = "shotTips", type = "toggle", label = "Chasseur : tirs entre deux tirs auto" },
  },
})
T.Fight = M
-- onglet « Infos » (pas de nouvel onglet : le panneau reste étroit) ; bloc de lignes source "Fight"
local TAB = "info"

---------------------------------------------------------------------------------------------------
-- utilitaires défensifs
---------------------------------------------------------------------------------------------------
local function pass(ok, ...) if ok then return ... end return nil end
local function try(fn, ...) if type(fn) ~= "function" then return nil end return pass(pcall(fn, ...)) end
local function secret(v) local f = rawget(G, "issecretvalue") return type(f) == "function" and try(f, v) and true or false end
local function sv(v) if v == nil or secret(v) then return nil end return v end
local function now() return try(G.GetTime) or 0 end
local function opt(k) return M.db and M.db[k] end
local function inCombat() return T:InCombat() end
local function spellName(id, fb) return (API.SpellName and API.SpellName(id)) or fb end
local function knows(id) return API.IsSpellKnown and API.IsSpellKnown(id) or false end
local function isClass(c) return T:PlayerClass() == c end
local function exists(u) return sv(try(G.UnitExists, u)) and true or false end
local function pct(u, power)
  local cur = sv(try(power and G.UnitPower or G.UnitHealth, u))
  local mx = sv(try(power and G.UnitPowerMax or G.UnitHealthMax, u))
  if not cur or not mx or mx <= 0 then return nil end
  return math.floor(cur / mx * 100 + 0.5)
end
local function hostile(u)
  if not exists(u) or sv(try(G.UnitIsPlayer, u)) then return false end
  if sv(try(G.UnitIsDead, u)) then return false end
  return sv(try(G.UnitCanAttack, "player", u)) and true or false
end
local function unitCombat(u) return sv(try(G.UnitAffectingCombat, u)) and true or false end
local function targetsMe(u) return sv(try(G.UnitIsUnit, u .. "target", "player")) and true or false end
local function cdReady(id)
  local s, d
  if API.SpellCooldown then s, d = API.SpellCooldown(id) end
  if s == nil and G.GetSpellCooldown then s, d = try(G.GetSpellCooldown, spellName(id) or id) end
  s, d = sv(s), sv(d)
  if s == nil then return true end
  return s == 0 or (s + (d or 0) - now()) < 1
end
local function isElite(c) return c == "elite" or c == "rareelite" or c == "worldboss" end
M._try, M._sv = try, sv

--- Liste des barres de nom (jetons d'unité). C_NamePlate si dispo, sinon nameplate1..40.
function M.PlateUnits()
  local out = {}
  local np = G.C_NamePlate
  local list = np and np.GetNamePlates and try(np.GetNamePlates)
  if type(list) == "table" then
    for _, f in ipairs(list) do
      local u = type(f) == "table" and (f.namePlateUnitToken or (type(f.UnitFrame) == "table" and f.UnitFrame.unit))
      if type(u) == "string" then out[#out + 1] = u end
    end
    if #out > 0 then return out end
  end
  for i = 1, 40 do
    local u = "nameplate" .. i
    if exists(u) then out[#out + 1] = u end
  end
  return out
end

--- Monstres hostiles visibles : { unit, guid, name, class, combat, me, dist }
function M.ScanPlates()
  local res = {}
  for _, u in ipairs(M.PlateUnits()) do
    if hostile(u) then
      res[#res + 1] = {
        unit = u, guid = sv(try(G.UnitGUID, u)), name = sv(try(G.UnitName, u)) or "?",
        class = sv(try(G.UnitClassification, u)) or "normal", combat = unitCombat(u), me = targetsMe(u),
        dist = API.Distance and sv(API.Distance(u)) or nil,
        level = sv(try(G.UnitLevel, u)),
      }
    end
  end
  return res
end

local lines = {}  -- lignes de l'onglet « Infos » : clé -> {text, prio}
local function setLine(k, text, prio) lines[k] = text and { text = text, prio = prio or 1 } or nil end

---------------------------------------------------------------------------------------------------
-- 26/27. pull + élites en patrouille
---------------------------------------------------------------------------------------------------
local patrolSeen = {}  -- guid -> {dist, warned}
function M:CheckPull()
  if inCombat() or not hostile("target") or unitCombat("target") then
    M:Clear("pull"); setLine("pull", nil) return
  end
  local plates = M.ScanPlates()
  local n, elites, busy = 0, 0, 0
  for _, p in ipairs(plates) do
    if p.combat then busy = busy + 1
    else
      n = n + 1
      if isElite(p.class) then elites = elites + 1 end
    end
  end
  if n == 0 then n = 1 end -- la cible elle-même (barres de nom masquées)
  local txt = ("Pull : %d monstre%s autour de toi"):format(n, n > 1 and "s" or "")
  if elites > 0 then txt = txt .. (" dont %d élite%s"):format(elites, elites > 1 and "s" or "") end
  if busy > 0 then txt = txt .. (" (%d déjà en combat)"):format(busy) end
  local big = n >= (tonumber(opt("pullMin")) or 3) or elites > 0
  if big then txt = txt .. " — tire celui qui est isolé, recule pour le combattre." end
  setLine("pull", txt, big and 2 or 1)
  if opt("pull") and big then M:Suggest("pull", txt, 2, { ttl = 15, cooldown = 30, tab = TAB })
  else M:Clear("pull") end
end

function M:CheckPatrol()
  if not opt("patrol") or inCombat() then return end
  local t = now()
  local standing = (sv(try(G.GetUnitSpeed, "player")) or 0) == 0
  for _, p in ipairs(M.ScanPlates()) do
    if p.guid and isElite(p.class) and not p.combat then
      local st = patrolSeen[p.guid]
      local d = p.dist
      if st and d and (st.dist == nil or d < st.dist - 4) and (not st.warned or t - st.warned > 60) then
        st.warned = t
        local where = ("~%d m"):format(math.floor(d))
        if standing then
          M:Suggest("patrol", ("Élite en patrouille vers toi : %s (%s) — écarte-toi ou attends qu'il passe."):format(p.name, where),
            d <= 12 and 3 or 2, { ttl = 12, cooldown = 20, tab = TAB })
        else
          M:Suggest("patrol", ("Élite devant : %s (%s)."):format(p.name, where), 2, { ttl = 10, cooldown = 20, tab = TAB })
        end
      end
      patrolSeen[p.guid] = { dist = d, warned = st and st.warned, t = t }
    end
  end
  for g, st in pairs(patrolSeen) do if t - (st.t or 0) > 120 then patrolSeen[g] = nil end end
end

---------------------------------------------------------------------------------------------------
-- 28. fuyards (émotes « … tente de s'enfuir »)
---------------------------------------------------------------------------------------------------
local FLEE_PAT = { "s'enfuit", "s’enfuit", "enfuir", "prend la fuite", "flee", "run away", "runs away" }
function M.IsFleeEmote(msg)
  msg = type(msg) == "string" and msg:lower() or ""
  for _, p in ipairs(FLEE_PAT) do if msg:find(p, 1, true) then return true end end
  return false
end
function M:CHAT_MSG_MONSTER_EMOTE(_, msg, sender)
  if not opt("flee") or not M.IsFleeEmote(msg) then return end
  local name = type(sender) == "string" and sender ~= "" and sender or "Le monstre"
  local f = M.db.fleeSeen or {}
  M.db.fleeSeen = f
  f[name] = math.min(999, (f[name] or 0) + 1)
  local n = 0 for _ in pairs(f) do n = n + 1 end
  if n > 200 then for k in pairs(f) do f[k] = nil; n = n - 1; if n <= 150 then break end end end
  local slow = isClass("HUNTER") and " (Trait de choc / Coupure d'ailes)" or ""
  M:Suggest("flee", name .. " s'enfuit : ralentis-le" .. slow .. " avant qu'il ramène des amis !", 2,
    { ttl = 8, cooldown = 5, tab = TAB })
end
local function fleeHint()
  if not opt("flee") or not hostile("target") then return end
  local name = sv(try(G.UnitName, "target"))
  local f = M.db and M.db.fleeSeen
  if name and f and f[name] then
    setLine("flee", ("%s fuit vers 20 %% PV (vu %d fois) : garde un ralentissement."):format(name, f[name]), 1)
  else setLine("flee", nil) end
end

---------------------------------------------------------------------------------------------------
-- 29. récap de mort
---------------------------------------------------------------------------------------------------
local hits = {}      -- { t, amount, crit, src? }
local fightStart, startHP = nil, nil
local function pushHit(amount, crit, src)
  hits[#hits + 1] = { t = now(), amount = amount or 0, crit = crit, src = src }
  while #hits > 40 do table.remove(hits, 1) end
end

function M:UNIT_COMBAT(_, unit, action, indicator, amount)
  if unit ~= "player" then return end
  action, amount = sv(action), sv(amount)
  if action == "WOUND" and type(amount) == "number" and amount > 0 then
    pushHit(amount, sv(indicator) == "CRITICAL")
    M._lastWound = now()
  end
end

--- Conseil à partir d'un récap (fonction pure, testée).
--- r = { attackers, elites, total, biggest, maxHP, duration, startHP, sources = {name=dmg}, env }
function M.DeathAdvice(r)
  r = r or {}
  if r.env then return "Attention au terrain (chute, noyade, lave)" end
  if (r.elites or 0) > 0 and (r.attackers or 0) <= 1 then
    return "Élite trop fort seul : reviens à plusieurs"
  end
  if (r.attackers or 0) >= 3 then return ("%d monstres sur toi : tire-les un par un"):format(r.attackers) end
  if r.maxHP and r.biggest and r.biggest >= r.maxHP * 0.3 then
    return "Gros coup (" .. r.biggest .. ") : interromps ou casse la ligne de vue"
  end
  if r.startHP and r.startHP < 60 then return ("Engagé à %d %% PV : mange avant le pull"):format(r.startHP) end
  if (r.attackers or 0) == 2 then return "2 monstres : contrôle l'add (piège, mouton…)" end
  if r.duration and r.duration > 25 then return "Combat long : potion plus tôt, fuis sous 30 % PV" end
  return "Bois une potion ou bande-toi sous 35 % PV"
end

function M:BuildRecap()
  local t = now()
  local total, biggest, n = 0, 0, 0
  local sources = M._sources or {}
  for _, h in ipairs(hits) do
    if t - h.t <= 12 then
      total, n = total + h.amount, n + 1
      if h.amount > biggest then biggest = h.amount end
    end
  end
  local attackers, elites, names = 0, 0, {}
  local killer, killerLvl
  for _, p in ipairs(M.ScanPlates()) do
    if p.me or (p.combat and p.guid) then
      attackers = attackers + 1
      if isElite(p.class) then elites = elites + 1 end
      if #names < 3 and p.name ~= "?" then names[#names + 1] = p.name end
      if not killer and p.me and p.name ~= "?" then killer, killerLvl = p.name, tonumber(p.level) end
    end
  end
  if exists("target") and hostile("target") then
    local tn = sv(try(G.UnitName, "target"))
    if attackers == 0 then
      attackers = 1
      if isElite(sv(try(G.UnitClassification, "target"))) then elites = 1 end
      if tn then names[1] = tn end
    end
    if not killer and tn then killer, killerLvl = tn, tonumber(sv(try(G.UnitLevel, "target"))) end
  end
  killer = killer or names[1]
  for src in pairs(sources) do if #names < 3 then names[#names + 1] = src end end
  return {
    time = try(G.time) or 0, attackers = attackers, elites = elites, total = total, biggest = biggest, hits = n,
    killer = killer, killerLvl = (killerLvl and killerLvl > 0) and killerLvl or nil,
    maxHP = sv(try(G.UnitHealthMax, "player")), duration = fightStart and math.floor(t - fightStart) or nil,
    startHP = startHP, names = names, env = M._env and (t - M._env) < 5 or nil,
    zone = try(G.GetZoneText),
  }
end

--- Texte du récap (fonction pure, testée) : jamais de chiffre qu'on n'a pas. Sans dégâts mesurés
--- (WoW Forever : pas de journal de combat) -> « Tué par X (niv N) » + un conseil.
function M.RecapText(r)
  r = r or {}
  local who
  if r.killer then
    who = "Tué par " .. r.killer .. (r.killerLvl and (" (niv " .. r.killerLvl .. ")") or "")
    if (r.attackers or 0) > 1 then who = who .. (" + %d"):format(r.attackers - 1) end
  elseif r.env then who = "Mort (chute / noyade / lave)"
  else who = "Mort" end
  if r.advice and r.advice ~= "" then return who .. " — " .. r.advice end
  return who
end

--- sans dégâts mesurés, le conseil ne s'appuie pas sur « gros coup » / durée inconnus
local function adviceFor(r)
  if not ((r.hits or 0) > 0 and (r.total or 0) > 0) then
    r = { env = r.env, elites = r.elites, attackers = r.attackers, startHP = r.startHP, duration = r.duration }
  end
  return M.DeathAdvice(r)
end
M.AdviceFor = adviceFor

function M:PLAYER_DEAD()
  if not opt("death") then return end
  local r = self:BuildRecap()
  r.advice = adviceFor(r)
  local txt = M.RecapText(r)
  local measured = (r.hits or 0) > 0 and (r.total or 0) > 0
  local list = M.char and M.char.deaths
  if type(list) == "table" then
    list[#list + 1] = { time = r.time, attackers = r.attackers, elites = r.elites, total = r.total,
      biggest = r.biggest, advice = r.advice, zone = r.zone }
    while #list > 20 do table.remove(list, 1) end
  end
  M.lastRecap = r
  setLine("death", txt, 2)
  local tip = { "Récap de mort", "Attaquants : " .. r.attackers .. (r.elites > 0 and (" (" .. r.elites .. " élite)") or "") }
  if measured then
    tip[#tip + 1] = "Dégâts subis (12 s) : " .. r.total .. " en " .. r.hits .. " coups"
      .. ((r.biggest or 0) > 0 and (", plus gros " .. r.biggest) or "")
  end
  if r.startHP then tip[#tip + 1] = "PV au début du combat : " .. r.startHP .. " %" end
  tip[#tip + 1] = r.advice
  M:Suggest("death", txt, 2, { ttl = 300, cooldown = 1, tab = TAB, death = true, tooltip = tip })
  hits, M._sources = {}, {}
end

---------------------------------------------------------------------------------------------------
-- 30. avant le pull
---------------------------------------------------------------------------------------------------
function M:CheckPrePull()
  if not opt("prepull") or inCombat() or not hostile("target") or unitCombat("target") then M:Clear("prepull") return end
  local hp = pct("player")
  local ptype = sv(try(G.UnitPowerType, "player"))
  local mana = (ptype == 0) and pct("player", true) or nil
  local bad = {}
  if hp and hp < (tonumber(opt("hpPull")) or 60) then bad[#bad + 1] = "PV " .. hp .. " %" end
  if mana and mana < (tonumber(opt("manaPull")) or 40) then bad[#bad + 1] = "mana " .. mana .. " %" end
  if exists("pet") and not sv(try(G.UnitIsDead, "pet")) then
    local php = pct("pet")
    if php and php < 50 then bad[#bad + 1] = "familier " .. php .. " %" end
  end
  if #bad == 0 then M:Clear("prepull") return end
  M:Suggest("prepull", "Avant d'engager : " .. table.concat(bad, ", ") .. " — récupère d'abord (manger / boire / soigner).",
    2, { ttl = 10, cooldown = 20, tab = TAB })
end

---------------------------------------------------------------------------------------------------
-- 31. interruptions
---------------------------------------------------------------------------------------------------
local INTERRUPTS = {
  WARRIOR = { 6552, 72 }, ROGUE = { 1766 }, MAGE = { 2139 }, SHAMAN = { 8042 }, PRIEST = { 15487 },
  DRUID = { 16979 }, HUNTER = { 19503, 19577 }, PALADIN = { 853 }, WARLOCK = {},
}
local DANGER = { "soin", "soins", "guérison", "rénovation", "heal", "peur", "fear", "métamorphose", "sommeil" }
local function dangerous(name)
  name = type(name) == "string" and name:lower() or ""
  for _, d in ipairs(DANGER) do if name:find(d, 1, true) then return true end end
  return false
end
function M.ReadyInterrupt()
  for _, id in ipairs(INTERRUPTS[T:PlayerClass() or ""] or {}) do
    if knows(id) and cdReady(id) then return spellName(id) end
  end
end
function M:OnCastStart(unit)
  if not opt("interrupt") or unit ~= "target" or not hostile(unit) then return end
  local name, _, _, _, endMS, _, _, notInt = try(G.UnitCastingInfo, unit)
  if not sv(name) then
    local cn, _, _, _, cend, _, cnot = try(G.UnitChannelInfo, unit)
    name, endMS, notInt = cn, cend, cnot
  end
  name, endMS, notInt = sv(name), sv(endMS), sv(notInt)
  if not name or notInt == true then return end
  local left = endMS and (endMS / 1000 - now()) or 2
  local ready = M.ReadyInterrupt()
  local danger = dangerous(name)
  local who = sv(try(G.UnitName, unit)) or "la cible"
  if ready then
    M:Suggest("interrupt", ("Interromps « %s » (%s) : %s prêt !"):format(name, who, ready), danger and 3 or 2,
      { ttl = math.max(1, math.min(10, left)), cooldown = 1, tab = TAB })
  elseif danger then
    M:Suggest("interrupt", ("%s incante « %s » : casse la ligne de vue ou tape fort."):format(who, name), 2,
      { ttl = math.max(1, math.min(10, left)), cooldown = 1, tab = TAB })
  end
end
function M:OnCastStop(unit) if unit == "target" then M:Clear("interrupt") end end

---------------------------------------------------------------------------------------------------
-- 32. contrôles (piège, mouton, assommer…) : minuteur + rupture
---------------------------------------------------------------------------------------------------
-- [spellId] = durée PvE (s) ; le nom localisé couvre aussi les rangs inconnus
local CC = {
  [3355] = 10, [14308] = 15, [14309] = 20,                       -- Piège givrant (effet)
  [118] = 20, [12824] = 30, [12825] = 40, [12826] = 50, [28272] = 50, [28271] = 50, -- Métamorphose
  [6770] = 25, [2070] = 35, [11297] = 45,                        -- Assommer
  [2637] = 20, [18657] = 30, [18658] = 40,                       -- Hibernation
  [1513] = 10, [14326] = 15, [14327] = 20,                       -- Effrayer une bête
  [5782] = 10, [6213] = 15, [6215] = 20,                         -- Peur
  [9484] = 30, [9485] = 40, [10955] = 50,                        -- Entraves des morts-vivants
  [2094] = 10, [710] = 20, [18647] = 30, [6358] = 15, [19386] = 12, [339] = 12, [1425] = 12,
}
local CC_FALLBACK = { ["Effet Piège givrant"] = 20, ["Piège givrant"] = 20, ["Métamorphose"] = 50,
  ["Assommer"] = 45, ["Hibernation"] = 40, ["Effrayer une bête"] = 20, ["Peur"] = 20, ["Cécité"] = 10,
  ["Bannir"] = 30, ["Entraves des morts-vivants"] = 50, ["Piqûre de wyverne"] = 12, ["Séduction"] = 15 }
local ccNames
local function ccByName()
  if ccNames then return ccNames end
  ccNames = {}
  for k, v in pairs(CC_FALLBACK) do ccNames[k] = v end
  for id, d in pairs(CC) do
    local n = spellName(id)
    if n and (not ccNames[n] or ccNames[n] < d) then ccNames[n] = d end
  end
  return ccNames
end
M.CC = CC

local active = {}   -- guid -> { name, spell, expires, start, unit, warned }
function M.FindCC(unit)
  if not API.Auras then return nil end
  local names = ccByName()
  for _, a in ipairs(API.Auras(unit, "HARMFUL") or {}) do
    local id, nm, src = sv(a.spellId), sv(a.name), sv(a.source)
    local d = (id and CC[id]) or (nm and names[nm])
    if d and (src == nil or src == "player" or src == "pet") then
      local exp = sv(a.expires)
      local dur = sv(a.duration)
      if not exp or exp <= 0 then exp, dur = nil, d end
      return nm or "?", exp, dur or d
    end
  end
end

local ccFrame
local function ccFrameUpdate()
  if not ccFrame then return end
  local t, best = now(), nil
  for _, c in pairs(active) do if not best or c.expires < best.expires then best = c end end
  if not best or not opt("ccFrame") then ccFrame:Hide() return end
  ccFrame.text:SetText(("%s — %s : %d s"):format(best.spell, best.name, math.max(0, math.ceil(best.expires - t))))
end
local function ensureCCFrame()
  if ccFrame or not G.CreateFrame then return ccFrame end
  ccFrame = G.CreateFrame("Frame", nil, G.UIParent)
  if ccFrame.SetClampedToScreen then ccFrame:SetClampedToScreen(true) end
  ccFrame:SetSize(220, 20)
  ccFrame:SetPoint("CENTER", G.UIParent, "CENTER", 0, -140)
  ccFrame.text = ccFrame:CreateFontString(nil, "OVERLAY", "GameFontNormal")
  ccFrame.text:SetPoint("CENTER")
  if T.Theme then T.Theme:Skin(ccFrame, "hud"); T.Theme:Font(ccFrame.text, "M") end  -- thème commun
  local acc = 0
  ccFrame:SetScript("OnUpdate", function(_, el)
    acc = acc + (el or 0)
    if acc < 0.2 then return end
    acc = 0
    M:TickCC()
  end)
  ccFrame:Hide()
  return ccFrame
end

function M:TickCC()
  local t = now()
  for guid, c in pairs(active) do
    local left = c.expires - t
    if left <= 3 and left > 0 and not c.warned then
      c.warned = true
      M:Suggest("ccend", ("Fin du contrôle dans %d s : %s (%s)"):format(math.ceil(left), c.name, c.spell), 3,
        { ttl = 4, cooldown = 3, tab = TAB })
    end
    if left < -2 then active[guid] = nil end
  end
  ccFrameUpdate()
  if next(active) == nil then setLine("cc", nil) end
end

function M:ScanCC(unit)
  if not opt("cc") or not exists(unit) or sv(try(G.UnitIsPlayer, unit)) then return end
  local guid = sv(try(G.UnitGUID, unit))
  if not guid then return end
  local spell, exp, dur = M.FindCC(unit)
  local t = now()
  local c = active[guid]
  if spell then
    if not c or c.spell ~= spell or (exp and math.abs(exp - c.expires) > 1) then
      active[guid] = { name = sv(try(G.UnitName, unit)) or "?", spell = spell, expires = exp or (t + dur), start = t }
      if ensureCCFrame() and opt("ccFrame") then ccFrame:Show() end
      setLine("cc", ("Contrôle : %s sur %s (%d s)"):format(spell, active[guid].name, math.floor((exp or (t + dur)) - t)), 1)
    end
  elseif c then
    active[guid] = nil
    if sv(try(G.UnitIsDead, unit)) then return end
    if t < c.expires - 1.5 then
      M:Suggest("ccbreak", ("Contrôle rompu : %s (%s) — %d s trop tôt !"):format(c.name, c.spell, math.floor(c.expires - t)), 3,
        { ttl = 6, cooldown = 2, tab = TAB })
    end
    M:Clear("ccend")
  end
  ccFrameUpdate()
end
function M.ActiveCC() return active end

---------------------------------------------------------------------------------------------------
-- 33. familier qui perd l'aggro
---------------------------------------------------------------------------------------------------
function M:CheckPetAggro()
  if not opt("petAggro") or not inCombat() or not exists("pet") or sv(try(G.UnitIsDead, "pet")) then M:Clear("petaggro") return end
  if not hostile("target") then return end
  local tanking = sv(try(G.UnitDetailedThreatSituation, "player", "target"))
  if tanking == nil then tanking = targetsMe("target") end
  if tanking then
    local growl = knows(2649) and spellName(2649, "Grondement") or nil
    M:Suggest("petaggro", "Le familier a perdu l'aggro : arrête de tirer 2 s" .. (growl and (", « " .. growl .. " » du familier") or "")
      .. " ou recule vers lui.", 3, { ttl = 6, cooldown = 15, tab = TAB })
  else
    M:Clear("petaggro")
  end
end

---------------------------------------------------------------------------------------------------
-- 34. humeur du familier → DPS perdu (Classic : heureux 125 %, content 100 %, mécontent 75 %)
---------------------------------------------------------------------------------------------------
function M.PetDPS()
  local lo, hi = try(G.UnitDamage, "pet")
  local speed = try(G.UnitAttackSpeed, "pet")
  lo, hi, speed = sv(lo), sv(hi), sv(speed)
  if type(lo) == "number" and type(hi) == "number" and type(speed) == "number" and speed > 0 then
    return (lo + hi) / 2 / speed
  end
  local lvl = sv(try(G.UnitLevel, "pet"))
  return lvl and lvl > 0 and (lvl * 1.1 + 2) or nil -- ordre de grandeur Classic
end
function M.MoodLoss(happiness, dps)
  -- dps = DPS actuel ; renvoie DPS gagné si heureux (positif)
  if not dps or not happiness then return nil end
  local mult = (happiness == 1 and 0.75) or (happiness == 2 and 1.0) or 1.25
  return math.floor(dps / mult * 1.25 - dps + 0.5)
end
function M:CheckPetMood()
  local happiness = API.PetHappiness or G.GetPetHappiness   -- C_PetInfo sur Forever
  if not opt("petMood") or not isClass("HUNTER") or not exists("pet") or not happiness then
    setLine("mood", nil) M:Clear("mood") return
  end
  local h = sv(try(happiness))
  if not h or h >= 3 then setLine("mood", nil) M:Clear("mood") return end
  local gain = M.MoodLoss(h, M.PetDPS())
  local txt = (h == 1 and "Familier mécontent : −25 % de ses dégâts" or "Familier seulement content : heureux = +25 % de dégâts")
    .. (gain and gain > 0 and (" (~" .. gain .. " DPS)") or "") .. " — nourris-le hors combat."
  setLine("mood", txt, h == 1 and 2 or 1)
  if not inCombat() then M:Suggest("mood", txt, h == 1 and 2 or 1, { ttl = 30, cooldown = 300, tab = TAB }) end
end

---------------------------------------------------------------------------------------------------
-- 35. tirs entre deux tirs auto
---------------------------------------------------------------------------------------------------
function M.ShotTip(speed, known)
  speed = tonumber(speed) or 0
  if speed <= 0 then return nil end
  local inst = {}
  for _, k in ipairs({ "arcane", "serpent", "multi", "conc" }) do if known[k] then inst[#inst + 1] = known[k] end end
  local parts = {}
  if #inst > 0 then
    parts[#parts + 1] = ("Tirs auto toutes les %.1f s : lance %s juste APRÈS un tir auto (instantané, pas de perte)"):format(
      speed, table.concat(inst, " / "))
  else
    parts[#parts + 1] = ("Tirs auto toutes les %.1f s : ne bouge pas pendant la courte incantation"):format(speed)
  end
  if known.aimed then
    if speed >= 2.8 then parts[#parts + 1] = "« " .. known.aimed .. " » en ouverture (arme lente : rentable)"
    else parts[#parts + 1] = "évite « " .. known.aimed .. " » en plein combat (arme rapide : tu perds des tirs)" end
  end
  return table.concat(parts, " ; ") .. "."
end
function M:UpdateShotTip()
  if not opt("shotTips") or not isClass("HUNTER") then setLine("shots", nil) return end
  local speed = sv(try(G.UnitRangedDamage, "player"))
  local known = {}
  for k, id in pairs({ arcane = 3044, serpent = 1978, multi = 2643, conc = 5116, aimed = 19434 }) do
    if knows(id) then known[k] = spellName(id) end
  end
  local tip = M.ShotTip(speed, known)
  setLine("shots", tip, 1)
  return tip
end

---------------------------------------------------------------------------------------------------
-- journal de combat (Classic Era uniquement : bonus, absent sur Forever)
---------------------------------------------------------------------------------------------------
function M:COMBAT_LOG_EVENT_UNFILTERED()
  if not G.CombatLogGetCurrentEventInfo then return end
  local ev = { try(G.CombatLogGetCurrentEventInfo) }
  local sub, srcName, dstGUID = sv(ev[2]), sv(ev[5]), sv(ev[8])
  if not sub or dstGUID ~= sv(try(G.UnitGUID, "player")) then return end
  if sub == "ENVIRONMENTAL_DAMAGE" then M._env = now() return end
  if sub == "SWING_DAMAGE" or sub == "SPELL_DAMAGE" or sub == "RANGE_DAMAGE" or sub == "SPELL_PERIODIC_DAMAGE" then
    local amount = tonumber(sv(sub == "SWING_DAMAGE" and ev[12] or ev[15])) or 0
    if srcName then
      M._sources = M._sources or {}
      M._sources[srcName] = (M._sources[srcName] or 0) + amount
    end
  end
end

---------------------------------------------------------------------------------------------------
-- onglet « Infos », boucle, cycle de vie
---------------------------------------------------------------------------------------------------
local ORDER = { "death", "pull", "cc", "flee", "mood", "shots" }
local lastPush = 0
function M:PushLines(force)
  local t = now()
  if not force and t - lastPush < 0.5 then return end
  lastPush = t
  local out = {}
  for _, k in ipairs(ORDER) do
    local l = lines[k]
    if l then out[#out + 1] = { text = l.text, prio = l.prio } end
  end
  if T.SetTabLines then try(T.SetTabLines, T, TAB, out, "Fight") end
end
M.Lines = lines

function M:Tick()
  if inCombat() then
    M:CheckPetAggro()
  else
    M:CheckPull(); M:CheckPatrol(); M:CheckPrePull(); fleeHint()
  end
  M:PushLines()
end

function M:AutoRefresh()
  M:CheckPetMood(); M:UpdateShotTip()
  if not inCombat() then M:CheckPull(); M:CheckPrePull() end
  M:PushLines(true)
end

local function startTicker()
  if M._ticker then M._ticker:Cancel() end
  local every = T.perfSlow and 2 or 1
  M._tickEvery = every
  M._ticker = T:NewTicker(every, function()
    if (T.perfSlow and 2 or 1) ~= M._tickEvery then startTicker() return end
    if M:IsEnabled() then M:Tick() end
  end)
end

function M:OnEnable()
  self:RegisterEvent("PLAYER_TARGET_CHANGED", function()
    M:Clear("interrupt"); M:CheckPull(); M:CheckPrePull(); fleeHint(); M:ScanCC("target"); M:PushLines()
  end)
  self:RegisterEvent("NAME_PLATE_UNIT_ADDED", function(_, _, u) M:ScanCC(u) end)
  self:RegisterEvent("UNIT_AURA", function(_, _, u)
    if u == "target" or u == "focus" or (type(u) == "string" and u:find("^nameplate")) then M:ScanCC(u) end
  end)
  self:RegisterEvent("UNIT_COMBAT")
  self:RegisterEvent("PLAYER_DEAD")
  self:RegisterEvent("CHAT_MSG_MONSTER_EMOTE")
  for _, e in ipairs({ "UNIT_SPELLCAST_START", "UNIT_SPELLCAST_CHANNEL_START" }) do
    self:RegisterEvent(e, function(_, _, u) M:OnCastStart(u) end)
  end
  for _, e in ipairs({ "UNIT_SPELLCAST_STOP", "UNIT_SPELLCAST_INTERRUPTED", "UNIT_SPELLCAST_FAILED",
      "UNIT_SPELLCAST_SUCCEEDED", "UNIT_SPELLCAST_CHANNEL_STOP" }) do
    self:RegisterEvent(e, function(_, _, u) M:OnCastStop(u) end)
  end
  self:RegisterEvent("UNIT_THREAT_SITUATION_UPDATE", function() M:CheckPetAggro() end)
  self:RegisterEvent("UNIT_HAPPINESS", function() M:CheckPetMood() end)
  self:RegisterEvent("START_AUTOREPEAT_SPELL", function()
    local tip = M:UpdateShotTip()
    if tip then M:Suggest("shots", tip, 1, { ttl = 20, cooldown = 900, tab = TAB }) end
  end)
  if G.CombatLogGetCurrentEventInfo then self:RegisterEvent("COMBAT_LOG_EVENT_UNFILTERED") end
  self:On("COMBAT_START", function()
    fightStart, startHP = now(), pct("player")
    M:Clear("pull"); M:Clear("prepull"); M:Clear("patrol")
  end)
  self:On("COMBAT_END", function() M:Clear("petaggro"); fightStart = nil end)
  self:On("SUMMARY", function() M:WriteSummary() end)
  startTicker()
  M:AutoRefresh()
end

function M:OnDisable()
  if M._ticker then M._ticker:Cancel(); M._ticker = nil end
  if ccFrame then ccFrame:Hide() end
  for k in pairs(lines) do lines[k] = nil end
  M:PushLines(true)
end

function M:WriteSummary()
  local s = T:Summary()
  if type(s) ~= "table" then return end
  local d = M.char and M.char.deaths or {}
  local last = d[#d]
  local nf = 0 for _ in pairs(M.db and M.db.fleeSeen or {}) do nf = nf + 1 end
  s.fight = { deaths = #d, lastDeath = last and { attackers = last.attackers, elites = last.elites,
    total = last.total, advice = last.advice, zone = last.zone, time = last.time } or nil, fleeKnown = nf }
end

T:RegisterSlash("mort", function()
  local r = M.lastRecap
  if not r then M:Print("Aucune mort enregistrée cette session.") return end
  local nums = ((r.hits or 0) > 0 and (r.total or 0) > 0) and (" (%d dégâts en 12 s)"):format(r.total) or ""
  M:Print("Dernière mort : " .. M.RecapText(r) .. nums)
end, "récap de la dernière mort")
