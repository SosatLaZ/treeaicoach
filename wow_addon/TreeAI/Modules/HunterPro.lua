-- TreeAI — module HunterPro (agent X2) : compléments chasseur INFORMATIFS (FEATURES50 n° 36–40).
--  36 meilleur familier à apprivoiser selon ton niveau et ta zone + rangs de techniques apprenables
--     (complète le conseiller d'apprivoisement de Boost, qui juge les rangs du familier ACTUEL)
--  37 budget de munitions pour finir le niveau (tirs par monstre observés × monstres restants)
--     (Predict donne « vides dans N min » ; ici c'est par niveau)
--  38 barre de piège : armé (60 s) / temps de recharge / déclenché
--  39 % de réussite de Feindre la mort (résisté = coups encore reçus juste après)
--  40 temps passé dans chaque aspect pendant les combats (par combat et par session)
-- Aucun sort lancé, aucune touche : uniquement du texte, une petite barre et l'encadré d'alerte (prio 3).
-- Forever : pas de journal de combat pour les addons → UNIT_SPELLCAST_SUCCEEDED / UNIT_COMBAT / UNIT_AURA,
-- valeurs « secrètes » ignorées (issecretvalue). Le journal de combat (Classic Era) reste un bonus.
local T = _G.TreeAI
if not T or not T.RegisterModule then return end
local API = T.API or {}
local G = _G

local M = T:RegisterModule("HunterPro", {
  title = "Chasseur +",
  classes = { HUNTER = true },
  defaults = { enabled = true, petZone = true, ammoBudget = true, trapBar = true, feign = true, aspectTime = true,
    trapPos = nil },
  charDefaults = { fd = { tries = 0, ok = 0 }, aspect = {}, ammoPerKill = {} },
  options = {
    { key = "petZone", type = "toggle", label = "Meilleur familier ici (niveau / zone / rangs)", section = "Familier" },
    { key = "ammoBudget", type = "toggle", label = "Budget de munitions pour finir le niveau", section = "Munitions" },
    { key = "trapBar", type = "toggle", label = "Barre de piège (armé / recharge / déclenché)", section = "Combat" },
    { key = "feign", type = "toggle", label = "Réussite de Feindre la mort (%)" },
    { key = "aspectTime", type = "toggle", label = "Temps passé dans chaque aspect" },
  },
})
T.HunterPro = M
local TAB = "info"  -- bloc de lignes source "HunterPro" dans « Infos »

local function pass(ok, ...) if ok then return ... end return nil end
local function try(fn, ...) if type(fn) ~= "function" then return nil end return pass(pcall(fn, ...)) end
local function secret(v) local f = rawget(G, "issecretvalue") return type(f) == "function" and try(f, v) and true or false end
local function sv(v) if v == nil or secret(v) then return nil end return v end
local function now() return try(G.GetTime) or 0 end
local function opt(k) return M.db and M.db[k] end
local function inCombat() return T:InCombat() end
local function spellName(id, fb) return (API.SpellName and API.SpellName(id)) or fb end
local function knows(id) return API.IsSpellKnown and API.IsSpellKnown(id) or false end
local function level() return sv(try(G.UnitLevel, "player")) or 1 end
local function fmt(n) n = math.floor((tonumber(n) or 0) + 0.5) local s = tostring(n):reverse():gsub("(%d%d%d)", "%1 "):reverse() return (s:gsub("^ ", "")) end

local lines = {}
local function setLine(k, text, prio) lines[k] = text and { text = text, prio = prio or 1 } or nil end
local ORDER = { "trap", "fd", "aspect", "ammo", "pet" }
local lastPush = 0
function M:PushLines(force)
  local t = now()
  if not force and t - lastPush < 0.5 then return end
  lastPush = t
  local out = {}
  for _, k in ipairs(ORDER) do if lines[k] then out[#out + 1] = { text = lines[k].text, prio = lines[k].prio } end end
  if T.SetTabLines then try(T.SetTabLines, T, TAB, out, "HunterPro") end
end
M.Lines = lines

---------------------------------------------------------------------------------------------------
-- 36. familier : rangs apprenables + familles par zone (niveaux Classic ≈, à vérifier dans Forever)
---------------------------------------------------------------------------------------------------
-- niveaux d'apprentissage des rangs (Classic) ; [nom] = { niveaux... }
M.RANKS = {
  ["Griffe"] = { 1, 8, 16, 24, 32, 40, 48, 56 }, ["Morsure"] = { 1, 8, 16, 24, 32, 40, 48, 56 },
  ["Rôder"] = { 30, 40, 50 }, ["Célérité"] = { 30, 40, 50 }, ["Plongeon"] = { 30, 40, 50 },
  ["Charge"] = { 1, 12, 24, 36, 48, 60 }, ["Hurlement furieux"] = { 10, 24, 40, 56 },
  ["Hurlement"] = { 8, 24, 48, 56 }, ["Piétinement"] = { 30, 40, 50 },
}
-- familles -> techniques et rôle
M.FAMILIES = {
  Chat = { "Griffe", "Morsure", "Rôder", "Célérité", role = "meilleurs dégâts en solo" },
  Loup = { "Morsure", "Hurlement furieux", "Célérité", role = "bon en groupe (Hurlement furieux)" },
  Sanglier = { "Morsure", "Charge", role = "robuste, Charge pour engager" },
  Ours = { "Griffe", "Morsure", role = "le plus solide (tank)" },
  Raptor = { "Griffe", "Morsure", role = "dégâts, fragile" },
  Gorille = { "Piétinement", role = "tank multi-cibles" },
  Hibou = { "Griffe", "Hurlement", "Plongeon", role = "dégâts + réduction d'attaque" },
  ["Chauve-souris"] = { "Morsure", "Hurlement", "Plongeon", role = "dégâts + réduction d'attaque" },
  ["Crocilisque"] = { "Morsure", role = "très solide" },
  ["Araignée"] = { "Morsure", role = "moyen (toile)" },
  ["Hyène"] = { "Morsure", "Célérité", role = "moyen" },
  ["Scorpide"] = { "Griffe", role = "solide (poison)" },
  ["Charognard"] = { "Morsure", "Hurlement", "Plongeon", role = "moyen" },
}
-- zones FR -> { famille, niv min, niv max } (Classic, ordre de grandeur)
M.ZONES = {
  ["Forêt d'Elwynn"] = { { "Loup", 5, 8 }, { "Sanglier", 3, 7 }, { "Ours", 8, 10 } },
  ["Dun Morogh"] = { { "Ours", 6, 10 }, { "Sanglier", 4, 9 }, { "Loup", 5, 9 } },
  ["Loch Modan"] = { { "Ours", 11, 18 }, { "Sanglier", 11, 16 }, { "Araignée", 12, 16 } },
  ["Marche de l'Ouest"] = { { "Charognard", 14, 17 }, { "Hyène", 10, 15 } },
  ["Les Carmines"] = { { "Loup", 17, 22 }, { "Araignée", 16, 20 } },
  ["Bois de la Pénombre"] = { { "Loup", 20, 30 }, { "Araignée", 20, 28 }, { "Ours", 22, 26 } },
  ["Les Paluns"] = { { "Raptor", 21, 29 }, { "Crocilisque", 21, 28 } },
  ["Contreforts de Hautebrande"] = { { "Ours", 20, 25 }, { "Chat", 23, 26 }, { "Araignée", 20, 22 } },
  ["Montagnes d'Alterac"] = { { "Ours", 32, 38 }, { "Araignée", 30, 36 } },
  ["Hautes-terres d'Arathi"] = { { "Raptor", 30, 37 }, { "Chat", 31, 35 }, { "Charognard", 30, 36 } },
  ["Vallée de Strangleronce"] = { { "Chat", 30, 43 }, { "Raptor", 32, 40 }, { "Gorille", 33, 42 } },
  ["Terres ingrates"] = { { "Charognard", 35, 45 }, { "Crocilisque", 37, 42 } },
  ["Les Tarides"] = { { "Chat", 12, 20 }, { "Raptor", 12, 18 }, { "Hyène", 12, 20 }, { "Sanglier", 12, 17 } },
  ["Orneval"] = { { "Loup", 20, 30 }, { "Ours", 20, 30 }, { "Araignée", 20, 26 } },
  ["Sombrivage"] = { { "Ours", 12, 20 }, { "Chat", 13, 18 } },
  ["Forêt des Pins-Argentés"] = { { "Loup", 10, 18 }, { "Ours", 14, 18 } },
  ["Serres-Rocheuses"] = { { "Chat", 22, 26 }, { "Araignée", 20, 26 } },
  ["Mille pointes"] = { { "Scorpide", 26, 34 }, { "Charognard", 25, 32 } },
  ["Désolace"] = { { "Scorpide", 30, 39 }, { "Hyène", 32, 38 } },
  ["Marécage d'Âprefange"] = { { "Raptor", 35, 45 }, { "Crocilisque", 36, 44 } },
  ["Tanaris"] = { { "Hyène", 40, 47 }, { "Scorpide", 41, 48 } },
  ["Féralas"] = { { "Gorille", 40, 47 }, { "Chat", 41, 45 } },
}

--- Rang le plus haut apprenable à ce niveau (index, niveau requis) et prochain palier.
function M.RankFor(ability, lvl)
  local r = M.RANKS[ability]
  if not r then return nil end
  local best, bestLvl, nextLvl
  for i, l in ipairs(r) do
    if l <= lvl then best, bestLvl = i, l elseif not nextLvl then nextLvl = l end
  end
  return best, bestLvl, nextLvl
end

--- Conseil pur : zone, niveau → texte, meilleure famille
function M.PetAdvice(zone, lvl)
  lvl = tonumber(lvl) or 1
  local list = M.ZONES[zone or ""] or {}
  local pick
  local pref = { Chat = 5, Loup = 4, Sanglier = 3, Raptor = 3, Ours = 3, Gorille = 3, Hibou = 2,
    ["Chauve-souris"] = 2, ["Crocilisque"] = 2, ["Scorpide"] = 2, ["Hyène"] = 1, ["Araignée"] = 1, ["Charognard"] = 1 }
  for _, e in ipairs(list) do
    -- apprivoisable : niveau du monstre ≤ le tien ; utile : pas plus de 5 niveaux sous toi
    local top = math.min(e[3], lvl)
    if e[2] <= lvl and top >= lvl - 6 then
      local sc = (pref[e[1]] or 1) * 10 + top
      if not pick or sc > pick.score then pick = { fam = e[1], lo = e[2], hi = top, score = sc } end
    end
  end
  local ranks = {}
  local fam = pick and M.FAMILIES[pick.fam] or M.FAMILIES.Chat
  for _, ab in ipairs(fam) do
    local idx, need, nxt = M.RankFor(ab, lvl)
    if idx then ranks[#ranks + 1] = ab .. " " .. idx .. (nxt and (" (rang " .. (idx + 1) .. " au niv " .. nxt .. ")") or "") end
  end
  local txt
  if pick then
    txt = ("Familier conseillé ici : %s niv %d–%d (%s)"):format(pick.fam, pick.lo, pick.hi, fam.role or "")
  else
    txt = "Pas de famille repérée pour ta zone : un Chat reste le meilleur DPS solo"
  end
  if #ranks > 0 then txt = txt .. " — rangs apprenables : " .. table.concat(ranks, ", ") end
  return txt .. ".", pick and pick.fam
end

function M:UpdatePet()
  if not opt("petZone") then setLine("pet", nil) return end
  local zone = try(G.GetRealZoneText) or try(G.GetZoneText)
  local txt = M.PetAdvice(zone, level())
  setLine("pet", txt, 1)
  return txt
end

---------------------------------------------------------------------------------------------------
-- 37. budget de munitions pour le niveau
---------------------------------------------------------------------------------------------------
function M.AmmoCount()
  local H = T.Hunter
  if H and H.AmmoInfo then
    local ok, info = pcall(H.AmmoInfo, H)
    if ok and type(info) == "table" then
      local n = math.max(tonumber(info.equipped) or 0, tonumber(info.total) or 0)
      if info.needs or n > 0 then return n end
    end
  end
  return sv(try(G.GetInventoryItemCount, "player", 0))
end

--- Pur : kills restants × munitions par kill → besoin, manque, piles de 200
function M.AmmoBudget(remainingXP, killXP, perKill, have)
  remainingXP, killXP = tonumber(remainingXP) or 0, tonumber(killXP) or 0
  if remainingXP <= 0 or killXP <= 0 then return nil end
  local kills = math.ceil(remainingXP / killXP)
  local need = kills * (tonumber(perKill) or 8)
  local miss = math.max(0, need - (tonumber(have) or 0))
  return { kills = kills, need = need, missing = miss, stacks = math.ceil(miss / 200) }
end

local ammoAtKill
local function avg(l) local s, n = 0, 0 for _, v in ipairs(l or {}) do s, n = s + v, n + 1 end return n > 0 and s / n or nil end
function M:OnKill()
  local n = M.AmmoCount()
  if not n then return end
  if ammoAtKill and n < ammoAtKill and ammoAtKill - n <= 60 then
    local l = M.char.ammoPerKill
    l[#l + 1] = ammoAtKill - n
    while #l > 30 do table.remove(l, 1) end
  end
  ammoAtKill = n
end

function M:UpdateAmmo()
  if not opt("ammoBudget") then setLine("ammo", nil) M:Clear("ammo") return end
  local have = M.AmmoCount()
  if not have then setLine("ammo", nil) return end
  local cur, max = sv(try(G.UnitXP, "player")) or 0, sv(try(G.UnitXPMax, "player")) or 0
  local rested = sv(try(G.GetXPExhaustion)) or 0
  local lvl = level()
  local killXP = 45 + 5 * lvl -- XP d'un monstre de ton niveau (formule Classic)
  local B = T.Boost
  if B and B.XPStats then
    local ok, r = pcall(B.XPStats, B)
    if ok and type(r) == "table" and tonumber(r.avgKill) then killXP = r.avgKill end
  end
  local remaining = max - cur
  if rested > 0 then remaining = math.max(remaining - math.min(rested, remaining / 2), remaining / 2) end
  local perKill = avg(M.char.ammoPerKill) or 8
  local b = M.AmmoBudget(remaining, killXP, perKill, have)
  if not b then setLine("ammo", nil) return end
  local txt = ("Munitions pour finir le niveau %d : ~%s (%d monstres × %.0f), tu en as %s"):format(
    lvl, fmt(b.need), b.kills, perKill, fmt(have))
  if b.missing > 0 then txt = txt .. (" → achète %d pile%s de 200"):format(b.stacks, b.stacks > 1 and "s" or "") end
  setLine("ammo", txt .. ".", b.missing > have and 2 or 1)
  if b.missing > 0 and b.missing > have and not inCombat() then
    M:Suggest("ammo", txt .. ".", 2, { ttl = 120, cooldown = 1800, tab = TAB })
  else M:Clear("ammo") end
  M.lastBudget = b
  return b
end

---------------------------------------------------------------------------------------------------
-- 38. barre de piège
---------------------------------------------------------------------------------------------------
local TRAPS = { 1499, 14310, 14311, 13795, 14302, 14303, 14304, 14305, 13809, 13813, 14316, 14317 }
local TRAP_FB = { ["Piège givrant"] = true, ["Piège d'immolation"] = true, ["Piège de givre"] = true,
  ["Piège explosif"] = true }
local trapNames
local function isTrap(id)
  if not trapNames then
    trapNames = {}
    for k in pairs(TRAP_FB) do trapNames[k] = true end
    for _, i in ipairs(TRAPS) do local n = spellName(i) if n then trapNames[n] = true end end
  end
  local n = id and spellName(id)
  return n and trapNames[n] and n or nil
end
M.IsTrap = isTrap

local trap -- { name, placed, armedUntil, cdUntil, triggered }
local bar
local function buildBar()
  if bar or not G.CreateFrame then return bar end
  bar = G.CreateFrame("StatusBar", nil, G.UIParent)
  bar:SetSize(170, 12)
  local p = M.db and M.db.trapPos
  if type(p) == "table" and p[1] then bar:SetPoint(p[1], G.UIParent, p[1], p[2] or 0, p[3] or 0)
  else bar:SetPoint("CENTER", G.UIParent, "CENTER", 0, -170) end
  if bar.SetStatusBarTexture then bar:SetStatusBarTexture("Interface\\TargetingFrame\\UI-StatusBar") end
  if bar.SetStatusBarColor then bar:SetStatusBarColor(0.3, 0.7, 1) end
  bar:SetMinMaxValues(0, 60)
  bar.text = bar:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
  bar.text:SetPoint("CENTER")
  bar:SetMovable(true); bar:EnableMouse(true)
  bar:RegisterForDrag("LeftButton")
  bar:SetScript("OnDragStart", function(s) if G.IsShiftKeyDown and G.IsShiftKeyDown() then s:StartMoving() end end)
  bar:SetScript("OnDragStop", function(s)
    s:StopMovingOrSizing()
    local pt, _, _, x, y = s:GetPoint()
    if M.db and pt then M.db.trapPos = { pt, math.floor(x or 0), math.floor(y or 0) } end
  end)
  local acc = 0
  bar:SetScript("OnUpdate", function(_, el) acc = acc + (el or 0) if acc >= 0.2 then acc = 0 M:TrapTick() end end)
  bar:Hide()
  return bar
end

function M.TrapText(tr, t)
  if not tr then return nil end
  local cd = tr.cdUntil and math.max(0, math.ceil(tr.cdUntil - t)) or 0
  local cdTxt = cd > 0 and (" · recharge %d s"):format(cd) or " · prêt"
  if tr.triggered then return ("%s déclenché !%s"):format(tr.name, cdTxt) end
  local left = math.max(0, math.ceil(tr.armedUntil - t))
  return ("%s armé : %d s%s"):format(tr.name, left, cdTxt)
end

function M:TrapTick()
  local t = now()
  if not trap then if bar then bar:Hide() end setLine("trap", nil) return end
  local over = (trap.triggered and t - trap.triggered > 3) or (not trap.triggered and t > trap.armedUntil)
  if over and (not trap.cdUntil or t > trap.cdUntil) then
    trap = nil
    if bar then bar:Hide() end
    setLine("trap", nil)
    return
  end
  local txt = M.TrapText(trap, t)
  setLine("trap", txt, 1)
  if bar and opt("trapBar") then
    bar:SetValue(trap.triggered and 0 or math.max(0, trap.armedUntil - t))
    bar.text:SetText(txt)
    bar:Show()
  end
end

function M:OnTrapCast(name)
  local t = now()
  local cd = 15
  local s, d
  if API.SpellCooldown then s, d = API.SpellCooldown(name) end
  s, d = sv(s), sv(d)
  if s and d and s > 0 and d > 1.5 then cd = s + d - t end
  trap = { name = name, placed = t, armedUntil = t + 60, cdUntil = t + cd }
  if opt("trapBar") then buildBar() end
  M:TrapTick()
end

function M:CheckTrapTrigger(unit)
  if not trap or trap.triggered or not API.Auras then return end
  for _, a in ipairs(API.Auras(unit, "HARMFUL") or {}) do
    local nm, src = sv(a.name), sv(a.source)
    if nm and (src == nil or src == "player") and (nm:find(trap.name, 1, true) or nm:find("Piège", 1, true)
        or nm:find("Trap", 1, true)) then
      trap.triggered = now()
      M:TrapTick()
      return true
    end
  end
end
function M.Trap() return trap end

---------------------------------------------------------------------------------------------------
-- 39. Feindre la mort : résultat
---------------------------------------------------------------------------------------------------
local fdPending
local FD = 5384
function M:FDLine()
  local fd = M.char and M.char.fd
  if not fd or (fd.tries or 0) == 0 then setLine("fd", nil) return nil end
  local p = math.floor(fd.ok / fd.tries * 100 + 0.5)
  local txt = ("Feindre la mort : %d %% de réussite (%d/%d)"):format(p, fd.ok, fd.tries)
  setLine("fd", txt, p < 70 and 2 or 1)
  return txt, p
end
function M:FDResult(ok)
  if not fdPending then return end
  fdPending = nil
  local fd = M.char.fd
  fd.tries = (fd.tries or 0) + 1
  if ok then fd.ok = (fd.ok or 0) + 1 end
  local txt = M:FDLine()
  if ok then
    M:Suggest("fd", "Feindre la mort réussi. " .. (txt or ""), 1, { ttl = 8, cooldown = 1, tab = TAB })
  else
    M:Suggest("fd", "Feindre la mort RÉSISTÉ : les monstres frappent encore — relève-toi et réagis !", 3,
      { ttl = 8, cooldown = 1, tab = TAB, tooltip = txt })
  end
end
function M:OnFeign()
  if not opt("feign") or not inCombat() then return end
  fdPending = { t = now() }
  local p = fdPending
  T:After(2.5, function() if fdPending == p then M:FDResult(true) end end)
end
function M:UNIT_COMBAT(_, unit, action, _, amount)
  if unit ~= "player" or not fdPending then return end
  local dt = now() - fdPending.t
  if sv(action) == "WOUND" and (tonumber(sv(amount)) or 0) > 0 and dt > 0.3 and dt <= 2.5 then M:FDResult(false) end
end
function M:COMBAT_LOG_EVENT_UNFILTERED()
  if not fdPending or not G.CombatLogGetCurrentEventInfo then return end
  local ev = { try(G.CombatLogGetCurrentEventInfo) }
  local sub, src = sv(ev[2]), sv(ev[4])
  if sub == "SPELL_MISSED" and src == sv(try(G.UnitGUID, "player")) and sv(ev[13]) == spellName(FD) then
    M:FDResult(false)
  end
end

---------------------------------------------------------------------------------------------------
-- 40. temps d'aspect
---------------------------------------------------------------------------------------------------
local ASPECTS = { faucon = 13165, singe = 13163, ["guépard"] = 5118, meute = 13159, ["bête"] = 13161, nature = 20043 }
local GOOD = { faucon = true, singe = true, nature = true, ["bête"] = true }
local fight = nil -- { [aspect] = sec, total }
function M.CurrentAspect()
  if not API.Aura then return "aucun" end
  for k, id in pairs(ASPECTS) do
    if API.Aura("player", id) then return k end
  end
  return "aucun"
end
function M.AspectReport(t)
  local total = 0
  for k, v in pairs(t) do if k ~= "total" then total = total + v end end
  if total <= 0 then return nil, 0 end
  local parts, bad = {}, 0
  local keys = {}
  for k in pairs(t) do if k ~= "total" then keys[#keys + 1] = k end end
  table.sort(keys, function(a, b) return t[a] > t[b] end)
  for _, k in ipairs(keys) do
    parts[#parts + 1] = ("%s %d %%"):format(k, math.floor(t[k] / total * 100 + 0.5))
    if not GOOD[k] then bad = bad + t[k] end
  end
  return table.concat(parts, " · "), math.floor(bad / total * 100 + 0.5)
end
function M:SampleAspect(dt)
  if not opt("aspectTime") or not inCombat() then return end
  fight = fight or {}
  local a = M.CurrentAspect()
  fight[a] = (fight[a] or 0) + (dt or 1)
  local s = M.char.aspect
  s[a] = (s[a] or 0) + (dt or 1)
end
function M:EndFight()
  local f = fight
  fight = nil
  if not f or not opt("aspectTime") then return end
  local total = 0 for _, v in pairs(f) do total = total + v end
  if total < 8 then return end
  local rep, bad = M.AspectReport(f)
  if not rep then return end
  local sess = M.AspectReport(M.char.aspect)
  setLine("aspect", "Aspects (dernier combat) : " .. rep .. (sess and (" — session : " .. sess) or ""), bad >= 30 and 2 or 1)
  -- the Hunter module's live « Aucun aspect actif » still shown: the post-fight report stays in the tab
  local live = T._suggestions and T._suggestions["Hunter:aspect"]
  local tnow = G.GetTime and G.GetTime() or 0
  live = live and live.prio == 2 and (not live.expires or live.expires > tnow)   -- « Aucun aspect actif »
  if bad >= 30 and not live then
    M:Suggest("aspect", ("Dernier combat : %d %% sans bon aspect (%s). Faucon pour tirer, Singe au corps à corps."):format(bad, rep),
      2, { ttl = 30, cooldown = 120, tab = TAB })
  end
end

---------------------------------------------------------------------------------------------------
-- cycle de vie
---------------------------------------------------------------------------------------------------
function M:AutoRefresh()
  if not inCombat() then M:UpdatePet(); M:UpdateAmmo() end
  M:FDLine()
  M:PushLines(true)
end

local function startTicker()
  if M._ticker then M._ticker:Cancel() end
  local every = T.perfSlow and 2 or 1
  M._every = every
  M._ticker = T:NewTicker(every, function()
    if (T.perfSlow and 2 or 1) ~= M._every then startTicker() return end
    if not M:IsEnabled() then return end
    M:SampleAspect(M._every)
    if not bar or not bar:IsShown() then M:TrapTick() end
    M:PushLines()
  end)
end

function M:OnEnable()
  self:RegisterEvent("UNIT_SPELLCAST_SUCCEEDED", function(_, _, unit, _, spellID)
    if unit ~= "player" then return end
    spellID = sv(spellID)
    if spellID == FD or (spellID and spellName(spellID) == spellName(FD)) then return M:OnFeign() end
    local tn = isTrap(spellID)
    if tn then M:OnTrapCast(tn) end
  end)
  self:RegisterEvent("UNIT_AURA", function(_, _, u)
    if u == "target" or (type(u) == "string" and u:find("^nameplate")) then M:CheckTrapTrigger(u) end
  end)
  self:RegisterEvent("UNIT_COMBAT")
  self:RegisterEvent("CHAT_MSG_COMBAT_XP_GAIN", function(_, _, msg)
    if type(msg) == "string" and (msg:find("meurt") or msg:find("dies") or msg:find("tué")) then M:OnKill() end
  end)
  self:RegisterEvent("ZONE_CHANGED_NEW_AREA", function() M:UpdatePet() M:PushLines() end)
  self:RegisterEvent("PLAYER_LEVEL_UP", function() M:UpdatePet() M:UpdateAmmo() M:PushLines() end)
  if G.CombatLogGetCurrentEventInfo then self:RegisterEvent("COMBAT_LOG_EVENT_UNFILTERED") end
  self:On("COMBAT_START", function() fight = {} ammoAtKill = ammoAtKill or M.AmmoCount() end)
  self:On("COMBAT_END", function() M:EndFight() M:PushLines(true) end)
  self:On("SUMMARY", function() M:WriteSummary() end)
  ammoAtKill = M.AmmoCount()
  startTicker()
  M:AutoRefresh()
end

function M:OnDisable()
  if M._ticker then M._ticker:Cancel(); M._ticker = nil end
  if bar then bar:Hide() end
  for k in pairs(lines) do lines[k] = nil end
  M:PushLines(true)
end

function M:WriteSummary()
  local s = T:Summary()
  if type(s) ~= "table" then return end
  local fd = M.char and M.char.fd or {}
  local sess = M.char and M.AspectReport(M.char.aspect or {})
  s.hunterPro = { fdTries = fd.tries or 0, fdOk = fd.ok or 0, aspects = sess,
    ammoPerKill = avg(M.char and M.char.ammoPerKill), ammoNeed = M.lastBudget and M.lastBudget.need or nil }
end

T:RegisterSlash("familier", function()
  M:Print(M:UpdatePet() or M.PetAdvice(try(G.GetZoneText), level()))
end, "meilleur familier à apprivoiser ici")
