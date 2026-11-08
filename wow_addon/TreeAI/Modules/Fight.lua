-- TreeAI — module Combat (fichier Fight.lua, CLEAN 4.0) : seulement ce que le vrai client laisse voir.
--  1. « Pourquoi je suis mort » : contre quoi (nom, niveau, élite), depuis combien de temps tu te battais, avec quelle
--     vie au départ, noyade / épuisement (minuteurs du jeu) ; un conseil SEULEMENT s'il découle de ces faits.
--     Le nom du monstre est lu hors combat (ta cible avant le pull) : en combat, Forever le rend secret.
--  2. « Avant le pull » : tu cibles un monstre hors combat et ta vie (ou ta mana, ou la vie du familier) est basse →
--     une ligne « récupère d'abord ». Quand la barre de combat montre son indicateur « Prêt / Pas prêt », elle seule
--     en parle (même seuils).
-- Retiré en 4.0 (faux ou muet dans le vrai jeu) : compteur de pull et patrouilles (pas de position des monstres sur
-- Forever : les barres de nom portent à 40–60 m), sort à interrompre (incantation de la cible secrète en combat),
-- minuteur des contrôles (auras ennemies secrètes en combat), menace du familier (secrète), fuyards, « tirs entre
-- deux tirs auto » (générique), dégâts subis (journal de combat interdit sur Forever).
-- Rien n'est fait à la place du joueur. Sortie : Suggest (onglet « Infos »), jamais le chat. Lua 5.1, aucune globale.
local T = _G.TreeAI
if not T or not T.RegisterModule then return end
local G = _G

local M = T:RegisterModule("Fight", {
  title = "Combat",
  defaults = { enabled = true, death = true, prepull = true, hpPull = 60, manaPull = 40 },
  charDefaults = { deaths = {} },
  options = {
    { key = "death", type = "toggle", label = "« Pourquoi je suis mort » : contre quoi, et un conseil tiré des faits", section = "Après" },
    { key = "prepull", type = "toggle", label = "« Avant le pull » : récupère d'abord si ta vie / mana / ton familier est bas", section = "Avant le pull" },
    { key = "hpPull", type = "range", label = "Vie minimale avant un pull (%)", min = 30, max = 90, step = 5 },
    { key = "manaPull", type = "range", label = "Mana minimale avant un pull (%) — pas pour le chasseur", min = 10, max = 80, step = 5 },
  },
})
T.Fight = M
local TAB = "info"

---------------------------------------------------------------------------------------------------
-- utilitaires défensifs (valeurs secrètes de Forever → nil)
---------------------------------------------------------------------------------------------------
local function pass(ok, ...) if ok then return ... end return nil end
local function try(fn, ...) if type(fn) ~= "function" then return nil end return pass(pcall(fn, ...)) end
local function secret(v) local f = rawget(G, "issecretvalue") return type(f) == "function" and try(f, v) and true or false end
local function sv(v) if v == nil or secret(v) then return nil end return v end
local function now() return try(G.GetTime) or 0 end
local function opt(k) return M.db and M.db[k] end
local function inCombat() return T:InCombat() end
local function exists(u) return sv(try(G.UnitExists, u)) and true or false end
local function pct(u, power)
  local cur = sv(try(power and G.UnitPower or G.UnitHealth, u))
  local mx = sv(try(power and G.UnitPowerMax or G.UnitHealthMax, u))
  if type(cur) ~= "number" or type(mx) ~= "number" or mx <= 0 then return nil end
  return math.floor(cur / mx * 100 + 0.5)
end
local function hostile(u)
  if not exists(u) or sv(try(G.UnitIsPlayer, u)) then return false end
  if sv(try(G.UnitIsDead, u)) then return false end
  return sv(try(G.UnitCanAttack, "player", u)) and true or false
end
local function isElite(c) return c == "elite" or c == "rareelite" or c == "worldboss" end
local function myLevel() return tonumber(sv(try(G.UnitLevel, "player"))) or 1 end
local function groupSize() return math.max(1, tonumber(sv(try(G.GetNumGroupMembers))) or 0) end
local Fmt = T.Fmt or {}
local function dur(s) return Fmt.Time and Fmt.Time(s) or (math.floor(s or 0) .. " s") end
M._sv = sv

---------------------------------------------------------------------------------------------------
-- 1. ce qu'on sait du combat (lu quand c'est lisible : hors combat, ou ta propre vie)
---------------------------------------------------------------------------------------------------
local foe          -- { name, level, cls, at } : ta cible hostile, lue hors combat (avant le pull)
local fight        -- { start, startHP, foe, group }
local mirror = {}  -- minuteurs du jeu actifs : BREATH (respiration), EXHAUSTION (fatigue)

--- lit la cible si elle est hostile et lisible -> { name, level, cls } ou nil
function M.ReadFoe(u)
  u = u or "target"
  if not hostile(u) then return nil end
  local name = sv(try(G.UnitName, u))
  if type(name) ~= "string" or name == "" then return nil end
  local lvl = tonumber(sv(try(G.UnitLevel, u)))
  return { name = name, level = (lvl and lvl > 0) and lvl or nil, boss = lvl == -1 or nil,
    cls = sv(try(G.UnitClassification, u)) or "normal", at = now() }
end

-- (FIDELITY) Forever : ta propre vie est secrète EN combat : on garde la dernière lecture hors combat (rafraîchie
-- au ciblage et à chaque AutoRefresh hors combat) pour « vie au départ » ; jamais une valeur devinée
local hpOOC
function M.OnTarget()
  local f = M.ReadFoe("target")
  if f and not inCombat() then foe = f end
  if not inCombat() then hpOOC = pct("player") end
  M.CheckPrePull()
end

function M.OnCombatStart()
  local f = M.ReadFoe("target")   -- souvent encore lisible à l'instant du pull ; sinon la cible d'avant
  if f then foe = f end
  fight = { start = now(), startHP = pct("player") or hpOOC, group = groupSize(),
    foe = (foe and now() - (foe.at or 0) < 120) and foe or nil }
  M:Clear("prepull")
end
function M.OnCombatEnd() fight = nil end

---------------------------------------------------------------------------------------------------
-- 2. « Pourquoi je suis mort » (fonctions pures, testées)
---------------------------------------------------------------------------------------------------
--- r = { killer, killerLvl, elite, boss, myLevel, group, startHP, duration, env } -> conseil (texte) ou nil
--- Jamais un conseil générique : seulement ce que les faits disent.
function M.DeathAdvice(r)
  r = r or {}
  if r.env == "BREATH" then return "Remonte respirer plus tôt : la barre de souffle s'est vidée" end
  if r.env == "EXHAUSTION" then return "Fais demi-tour quand la barre de fatigue apparaît : eaux trop profondes" end
  if (r.elite or r.boss) and (r.group or 1) <= 1 then return "Cet élite est fait pour un groupe : reviens à plusieurs" end
  local gap = (r.killerLvl and r.myLevel) and (r.killerLvl - r.myLevel) or nil
  if r.boss or (gap and gap >= 3) then
    return r.boss and "Monstre bien trop haut pour toi : évite-le" or ("Monstre %d niveaux au-dessus de toi : vise ton niveau"):format(gap)
  end
  if r.startHP and r.startHP < 60 then return ("Tu as engagé à %d %% de vie : récupère avant le pull"):format(r.startHP) end
  if r.duration and r.duration >= 40 then return "Combat long : potion ou bandage plus tôt, ou recule vers ton groupe" end
  return nil
end

--- texte de la ligne (pur) : « Mort contre Loup des bois (niv 28, élite) — conseil »
function M.RecapText(r)
  r = r or {}
  local who
  if r.env == "BREATH" then who = "Mort noyé"
  elseif r.env == "EXHAUSTION" then who = "Mort d'épuisement (eaux profondes)"
  elseif r.killer then
    local tags = {}
    if r.boss then tags[#tags + 1] = "niveau ??" elseif r.killerLvl then tags[#tags + 1] = "niv " .. r.killerLvl end
    if r.elite then tags[#tags + 1] = "élite" end
    who = "Mort contre " .. r.killer .. (#tags > 0 and (" (" .. table.concat(tags, ", ") .. ")") or "")
  else who = "Mort" end
  if r.advice and r.advice ~= "" then return who .. " — " .. r.advice end
  return who
end

function M.BuildRecap()
  local t = now()
  local r = { time = try(G.time) or 0, myLevel = myLevel(), group = groupSize(), zone = sv(try(G.GetZoneText)) }
  local f = M.ReadFoe("target")
  if not f and fight and fight.foe then f = fight.foe end
  if not f and foe and t - (foe.at or 0) < 120 then f = foe end
  if f then r.killer, r.killerLvl, r.boss, r.elite = f.name, f.level, f.boss, isElite(f.cls) or nil end
  if fight then
    r.duration = math.floor(t - (fight.start or t))
    r.startHP = fight.startHP
    r.group = fight.group or r.group
  end
  if mirror.BREATH then r.env = "BREATH" elseif mirror.EXHAUSTION then r.env = "EXHAUSTION" end
  -- champs lus par le module Session (tendance de la soirée) : un élite = 1 attaquant élite connu
  r.elites = r.elite and 1 or 0
  return r
end

function M.OnDeath()
  if not opt("death") then return end
  local r = M.BuildRecap()
  r.advice = M.DeathAdvice(r)
  local txt = M.RecapText(r)
  local list = M.char and M.char.deaths
  if type(list) == "table" then
    list[#list + 1] = { time = r.time, killer = r.killer, level = r.killerLvl, elite = r.elite, advice = r.advice,
      zone = r.zone, duration = r.duration, startHP = r.startHP }
    while #list > 20 do table.remove(list, 1) end
  end
  M.lastRecap = r
  local tip = { "Pourquoi je suis mort" }
  if r.duration and r.duration > 0 then tip[#tip + 1] = "Combat de " .. dur(r.duration) end
  if r.startHP then tip[#tip + 1] = ("Vie au début du combat : %d %%"):format(r.startHP) end
  if (r.group or 1) > 1 then tip[#tip + 1] = ("En groupe de %d"):format(r.group) end
  if r.killer then tip[#tip + 1] = "Monstre lu sur ta cible (en combat, Forever cache le reste)." end
  M:Suggest("death", txt, 2, { ttl = 300, cooldown = 1, tab = TAB, death = true, sound = false, tooltip = tip })
  fight = nil
end

---------------------------------------------------------------------------------------------------
-- 3. « Avant le pull »
---------------------------------------------------------------------------------------------------
--- seuils : ceux de la barre de combat si elle existe (une seule définition de « prêt »), sinon les nôtres
local function thresholds()
  local QF = T.QuickFight
  local q = QF and QF.db
  return tonumber(q and q.hp) or tonumber(opt("hpPull")) or 60, tonumber(q and q.mana) or tonumber(opt("manaPull")) or 40,
    tonumber(q and q.petHp) or 50
end
--- raisons de ne pas engager (pur sur un état) : s = { hp, mana, petHp, hunter } -> { "vie 45 %", … }
function M.PrePullReasons(s, hpMin, manaMin, petMin)
  local bad = {}
  if s.hp and s.hp < (hpMin or 60) then bad[#bad + 1] = "vie " .. s.hp .. " %" end
  if s.mana and not s.hunter and s.mana < (manaMin or 40) then bad[#bad + 1] = "mana " .. s.mana .. " %" end
  if s.petHp and s.petHp < (petMin or 50) then bad[#bad + 1] = "familier " .. s.petHp .. " %" end
  return bad
end

function M.CheckPrePull()
  if not opt("prepull") or inCombat() or not hostile("target") or sv(try(G.UnitAffectingCombat, "target")) then
    M:Clear("prepull") return
  end
  local QF = T.QuickFight   -- (QUICK2) la barre affiche déjà « Pas prêt : … » : un seul propriétaire
  if QF and type(QF.OwnsPrePull) == "function" and QF:OwnsPrePull() then M:Clear("prepull") return end
  local s = { hp = pct("player"), hunter = T:PlayerClass() == "HUNTER" }
  if sv(try(G.UnitPowerType, "player")) == 0 then s.mana = pct("player", true) end
  if exists("pet") and not sv(try(G.UnitIsDead, "pet")) then s.petHp = pct("pet") end
  local bad = M.PrePullReasons(s, thresholds())
  if #bad == 0 then M:Clear("prepull") return end
  M:Suggest("prepull", "Récupère avant le pull : " .. table.concat(bad, ", "), 2,
    { ttl = 10, cooldown = 20, tab = TAB, sound = false,
      tooltip = { "Manger, boire ou soigner le familier avant d'engager : un combat commencé bas finit souvent mal." } })
end

---------------------------------------------------------------------------------------------------
-- cycle de vie
---------------------------------------------------------------------------------------------------
function M:AutoRefresh() if not inCombat() then hpOOC = pct("player") M.CheckPrePull() end end

function M:OnEnable()
  self:RegisterEvent("PLAYER_TARGET_CHANGED", function() M.OnTarget() end)
  self:RegisterEvent("PLAYER_DEAD", function() M.OnDeath() end)
  self:RegisterEvent("MIRROR_TIMER_START", function(_, _, timer) if type(timer) == "string" then mirror[timer] = true end end)
  self:RegisterEvent("MIRROR_TIMER_STOP", function(_, _, timer) if type(timer) == "string" then mirror[timer] = nil end end)
  self:RegisterEvent("PLAYER_UNGHOST", function() mirror = {} end)
  self:On("COMBAT_START", function() M.OnCombatStart() end)
  self:On("COMBAT_END", function() M.OnCombatEnd() end)
  self:On("SUMMARY", function() M:WriteSummary() end)
end

function M:OnDisable()
  M:Clear("prepull")
  fight = nil
end

function M:WriteSummary()
  local s = T:Summary()
  if type(s) ~= "table" then return end
  local d = M.char and M.char.deaths or {}
  local last = d[#d]
  s.fight = { deaths = #d, lastDeath = last and { killer = last.killer, level = last.level, elite = last.elite,
    advice = last.advice, zone = last.zone, time = last.time, duration = last.duration, startHP = last.startHP } or nil,
    updated = try(G.time) }   -- (QA-APP) l'appli écarte un bloc qui n'est plus réécrit (module coupé)
end
