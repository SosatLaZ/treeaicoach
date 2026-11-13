-- TreeAI — module Combat (fichier Fight.lua, CLEAN 4.0) : seulement ce que le vrai client laisse voir.
--  1. « Pourquoi je suis mort » : contre quoi (nom, niveau, élite), depuis combien de temps tu te battais, avec quelle
--     vie au départ, noyade / épuisement (minuteurs du jeu) ; un conseil SEULEMENT s'il découle de ces faits.
--     Le nom du monstre est lu hors combat (ta cible avant le pull) : en combat, Forever le rend secret, mais son
--     niveau et son rang (élite) restent lisibles. Le monstre retenu est TA DERNIÈRE CIBLE HOSTILE DU COMBAT FATAL :
--     jamais celui d'un combat précédent (oublié à la fin de chaque combat), jamais une cible morte entre-temps.
--     Mieux vaut « Mort » sans nom qu'un faux tueur.
--  2. « Avant le pull » : tu cibles un monstre hors combat et ta vie (ou ta mana, ou la vie du familier) est basse →
--     une ligne « récupère d'abord ». Quand la barre de combat montre son indicateur « Prêt / Pas prêt », elle seule
--     en parle (même seuils).
-- Retiré en 4.0 (faux ou muet dans le vrai jeu) : compteur de pull et patrouilles (pas de position des monstres sur
-- Forever : les barres de nom portent à 40–60 m), sort à interrompre (incantation de la cible secrète en combat),
-- minuteur des contrôles (auras ennemies secrètes en combat), menace du familier (secrète), fuyards, « tirs entre
-- deux tirs auto » (générique), dégâts subis (journal de combat interdit sur Forever).
-- Rien n'est fait à la place du joueur. Sortie : Suggest (onglet « Infos »), jamais le chat. Lua 5.1, aucune globale.
local tonumber = function(v) return _G.tonumber(v) end   -- (4.0.8) tonumber(f()) : un 2e retour de f n'est jamais une base
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
local function pass(ok, ...) if ok then return ... end local sw = _G.TreeAI and _G.TreeAI.Swallow if sw then sw((...), "Fight") end return nil end
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
local fight        -- { start, startHP, foe, group } : le combat en cours
local lastFight    -- le combat qui vient de finir (3 s) : l'ordre PLAYER_REGEN_ENABLED / PLAYER_DEAD n'est pas sûr
local mirror = {}  -- minuteurs du jeu actifs : BREATH (respiration), EXHAUSTION (fatigue)
local PULL_WINDOW, END_GRACE = 15, 3

--- lit la cible si elle est hostile -> { name?, level, cls, boss } ou nil. En combat sur Forever le nom est secret
--- (nil) ; le niveau et le rang (élite) restent lisibles : on les garde (un élite niv 33 dit déjà pourquoi).
function M.ReadFoe(u)
  u = u or "target"
  if not hostile(u) then return nil end
  local name = sv(try(G.UnitName, u))
  if type(name) ~= "string" or name == "" then name = nil end
  local lvl = tonumber(sv(try(G.UnitLevel, u)))
  local cls = sv(try(G.UnitClassification, u))
  if not name and not lvl and type(cls) ~= "string" then return nil end
  return { name = name, level = (lvl and lvl > 0) and lvl or nil, boss = lvl == -1 or nil,
    cls = type(cls) == "string" and cls or "normal", at = now() }
end

--- la même cible lue deux fois ? un relevé sans nom (en combat) et un relevé nommé de même niveau et même rang,
--- sans changement de cible entre les deux (sinon le relevé nommé aurait été remplacé) -> le relevé nommé
local function sameFoe(named, seen)
  return named and named.name and seen and not seen.name and named.level == seen.level and named.cls == seen.cls
end

-- (FIDELITY) Forever : ta propre vie est secrète EN combat : on garde la dernière lecture hors combat (rafraîchie
-- au ciblage, à chaque UNIT_HEALTH du joueur hors combat et à l'AutoRefresh) pour « vie au départ » ; jamais devinée
local hpOOC
function M.OnTarget()
  local f = M.ReadFoe("target")
  if not inCombat() then
    foe = f                         -- même nil : une cible amie / morte / vide efface l'ancien ennemi
    hpOOC = pct("player")
  elseif fight and (f or exists("target")) then
    -- nouvelle cible en combat : c'est elle qui compte désormais (son nom est souvent secret, son niveau non) ;
    -- une cible illisible (amie, morte) efface l'ancienne : on ne sait plus contre qui tu te bats
    fight.foe = f
  end
  M.CheckPrePull()
end

function M.OnCombatStart()
  local t = now()
  local f = M.ReadFoe("target")   -- au pull, le nom est souvent déjà secret : celui lu juste avant (même cible)
  local recent = foe and t - (foe.at or 0) < PULL_WINDOW and foe or nil
  if recent and (not f or sameFoe(recent, f)) then f = recent end
  fight = { start = t, startHP = pct("player") or hpOOC, group = groupSize(), foe = f }
  lastFight = nil
  M:Clear("prepull")
  M:Clear("death")                -- le récap de la mort d'avant reste dans l'onglet Infos, pas pendant ce combat
end
function M.OnCombatEnd()
  if fight then fight.endedAt = now() lastFight = fight end
  fight, foe = nil, nil           -- le monstre tué (ou fui) n'est plus « l'ennemi » du prochain combat
end

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

--- texte de la ligne (pur) : « Mort contre Loup des bois (niv 28, élite) — conseil » ; nom secret (Forever, en
--- combat) : « Mort contre un élite niv 33 — conseil »
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
  elseif r.boss or r.killerLvl then
    who = "Mort contre " .. (r.elite and "un élite" or "un monstre") .. (r.boss and " de niveau ??" or (" niv " .. r.killerLvl))
  else who = "Mort" end
  if r.advice and r.advice ~= "" then return who .. " — " .. r.advice end
  return who
end

function M.BuildRecap()
  local t = now()
  local r = { time = try(G.time) or 0, myLevel = myLevel(), group = groupSize(), zone = sv(try(G.GetZoneText)) }
  -- le combat fatal : en cours, ou fini il y a moins de END_GRACE s (REGEN_ENABLED arrivé avant PLAYER_DEAD)
  local fg = fight or (lastFight and t - (lastFight.endedAt or 0) < END_GRACE and lastFight) or nil
  -- monstre : ta cible hostile vivante à l'instant de la mort, sinon ta dernière cible hostile de CE combat ; une
  -- cible morte (tu l'as tuée, un autre t'a eu) = tueur inconnu : jamais l'ennemi d'un combat précédent
  local f = M.ReadFoe("target")
  if fg and sameFoe(fg.foe, f) then f = fg.foe end
  if not f and fg and not (exists("target") and sv(try(G.UnitIsDead, "target"))) then f = fg.foe end
  if f then r.killer, r.killerLvl, r.boss, r.elite = f.name, f.level, f.boss, isElite(f.cls) or nil end
  if fg then
    r.duration = math.floor((fg.endedAt or t) - (fg.start or t))
    r.startHP = fg.startHP
    r.group = fg.group or r.group
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
  if r.killer then tip[#tip + 1] = "Monstre lu sur ta cible (en combat, Forever cache le reste)."
  elseif r.killerLvl or r.boss then tip[#tip + 1] = "Niveau et rang lus sur ta cible ; son nom, Forever le cache en combat."
  elseif not r.env then tip[#tip + 1] = "Tueur inconnu : Forever ne dit pas aux addons qui frappe en combat." end
  M:Suggest("death", txt, 2, { ttl = 120, cooldown = 1, tab = TAB, death = true, sound = false, tooltip = tip })
  fight, lastFight, foe = nil, nil, nil
end

---------------------------------------------------------------------------------------------------
-- 3. « Avant le pull »
---------------------------------------------------------------------------------------------------
--- seuils : ceux de la barre de combat quand c'est elle qui montre « Prêt / Pas prêt » (une seule définition de
--- « prêt »), sinon les nôtres (les curseurs « Vie / Mana minimale avant un pull » de Combat)
local function thresholds()
  local QF = T.QuickFight
  local q = QF and type(QF.OwnsPrePull) == "function" and QF:OwnsPrePull() and QF.db or nil
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
-- (5.0 STABLE) au calme (aucun événement, joueur immobile) : rien à refaire — le cycle complet du cœur (30 s) suffit
M.autoIdleSafe = true
function M:AutoRefresh() if not inCombat() then hpOOC = pct("player") M.CheckPrePull() end end

function M:OnEnable()
  self:RegisterEvent("PLAYER_TARGET_CHANGED", function() M.OnTarget() end)
  -- vie au départ : relue dès qu'elle change hors combat (manger jusqu'à 100 % puis tirer dans les 3 s = 100 %)
  local function onHealth() if not inCombat() then hpOOC = pct("player") end end
  if self.RegisterUnitEvent then self:RegisterUnitEvent("UNIT_HEALTH", "player", nil, onHealth)
  else self:RegisterEvent("UNIT_HEALTH", function(_, _, u) if u == "player" then onHealth() end end) end
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
  fight, lastFight, foe = nil, nil, nil
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
