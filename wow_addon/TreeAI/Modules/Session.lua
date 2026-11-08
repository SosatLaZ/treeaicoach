-- TreeAI — module « Ma session » (agent NEXT10) : le bilan perso de la soirée, en une carte propre.
--  1 bilan en direct (onglet « Infos ») : durée, XP gagnée, niveaux, quêtes rendues, or gagné / dépensé.
--    Un clic sur la ligne ouvre / ferme le détail (meilleurs moments, butin rare, où se déconnecter).
--  2 fin de session : le bilan est gardé à la déconnexion et montré au retour (une ligne, un clic la range).
--    Un /reload ou une coupure de moins de 30 min continue la même session.
--  3 « pourquoi je meurs » : tendance des morts de la soirée (écart de niveau, même monstre, trop de monstres,
--    élites, même zone) + UN conseil ; une ligne dans « Maintenant » au 3e décès en 30 min (une fois vivant).
--  4 pause : après 2 h de jeu d'affilée, un petit rappel cliquable (option, une fois toutes les 2 h).
--  5 monture (niveaux 30 à 39) : l'or mis de côté face à ton objectif (réglable), et le temps qu'il faut à ton
--    rythme d'or MESURÉ (Stats, au moins 10 min) — repris de l'ancien module « Coups d'avance ».
-- Données : événements du jeu (XP, argent, quêtes rendues, butin perso, mort) + le récap du module Combat s'il
-- est là (contre quoi, niveau). Jamais un chiffre qu'on n'a pas (pas d'XP/h ici : c'est Stats, avec sa durée de
-- mesure). Rien n'est fait à la place du joueur.
-- Sortie : onglet « Infos » (TreeAI:SetTabLines, bloc "Session") + Suggest rares. Lua 5.1, aucune globale.
local T = _G.TreeAI
if not (T and T.RegisterModule) then return end

local M = T:RegisterModule("Session", {
  title = "Ma session",
  defaults = { enabled = true, card = true, prev = true, deaths = true, pause = true, pauseHours = 2, mount = true, mountGoal = 90 },
  charDefaults = { cur = false, prev = false },
  options = {
    { key = "card", type = "toggle", label = "Bilan de la session dans « Infos »", section = "Bilan" },
    { key = "prev", type = "toggle", label = "Montrer le bilan de la dernière session au retour", section = "Bilan" },
    { key = "deaths", type = "toggle", label = "« Pourquoi je meurs » : tendance + un conseil", section = "Conseils" },
    { key = "pause", type = "toggle", label = "Petit rappel de pause après une longue session", section = "Conseils" },
    { key = "pauseHours", type = "range", label = "Rappel de pause après (heures)", min = 1, max = 4, step = 1, section = "Conseils" },
    { key = "mount", type = "toggle", label = "Niveaux 30 à 39 : l'or mis de côté pour la monture", section = "Conseils" },
    { key = "mountGoal", type = "range", label = "Objectif pour la monture (po)", min = 40, max = 200, step = 5, section = "Conseils" },
  },
})
if T.MODULE_TAB and not T.MODULE_TAB.Session then T.MODULE_TAB.Session = "info" end
if T.TOPIC_IDS then
  T.TOPIC_IDS["Session:deaths"] = "death"
  T.TOPIC_IDS["Fight:death"] = T.TOPIC_IDS["Fight:death"] or "death"
  T.TOPIC_IDS["Session:prev"] = "changes"
end

local floor, max, min = math.floor, math.max, math.min
local fmt, tconcat, tinsert, tsort = string.format, table.concat, table.insert, table.sort
local GAP = 30 * 60          -- au-delà : nouvelle session
local MIN_SHOWN = 5 * 60     -- carte montrée après 5 min de jeu
local TREND_WINDOW = 30 * 60 -- 3 morts dans cette fenêtre = ligne « Maintenant »

local function g(name) return rawget(_G, name) end
local function try(fn, ...)
  if type(fn) ~= "function" then return nil end
  local ok, a, b, c, d, e = pcall(fn, ...)
  if ok then return a, b, c, d, e end
end
-- valeur « secrète » (WoW Forever, en combat) = inconnue
local function sv(v)
  local is = g("issecretvalue")
  if v ~= nil and is and try(is, v) then return nil end
  return v
end
local function num(v) v = tonumber(sv(v)) return v end
local function now() return num(try(g("time"))) or 0 end
local function opt(k) return not (M.db and M.db[k] == false) end
local Fmt = T.Fmt or {}
local function money(c) return Fmt.Money and Fmt.Money(c) or (floor((c or 0) / 10000) .. " po") end
local function dur(s) return Fmt.Time and Fmt.Time(s) or (floor((s or 0) / 60) .. " min") end
local function n(x) return Fmt.Num and Fmt.Num(x) or tostring(floor(x or 0)) end
local function plural(k, one, many) return k .. " " .. (k > 1 and many or one) end
local function short(s, len)
  s = tostring(s or ""):gsub("|c%x%x%x%x%x%x%x%x", ""):gsub("|r", ""):gsub("|H.-|h(.-)|h", "%1"):gsub("[%[%]]", "")
  return T.Cut(s, len, "")          -- (4.0) Core : len caractères UTF-8, jamais une lettre accentuée coupée
end

local function myLevel() return num(try(g("UnitLevel"), "player")) or 1 end
local function myXP() return num(try(g("UnitXP"), "player")), num(try(g("UnitXPMax"), "player")) end
local function myMoney() return num(try(g("GetMoney"))) end

-- ------------------------------------------------------------------ fonctions pures (testées)
--- Tendance des morts : deaths = { {t, gap, killer, attackers, elites, zone}, ... } -> texte court, conseil, onglet
function M.DeathTrend(deaths)
  local k = #(deaths or {})
  if k < 2 then return nil end
  local high, crowd, elite, byKiller, byZone = 0, 0, 0, {}, {}
  for _, d in ipairs(deaths) do
    if (tonumber(d.gap) or 0) >= 3 then high = high + 1 end
    if (tonumber(d.attackers) or 0) >= 3 then crowd = crowd + 1 end
    if (tonumber(d.elites) or 0) > 0 then elite = elite + 1 end
    if d.killer and d.killer ~= "" then byKiller[d.killer] = (byKiller[d.killer] or 0) + 1 end
    if d.zone and d.zone ~= "" then byZone[d.zone] = (byZone[d.zone] or 0) + 1 end
  end
  local function top(t)
    local best, bn = nil, 0
    for name, c in pairs(t) do if c > bn or (c == bn and best and name < best) then best, bn = name, c end end
    return best, bn
  end
  local killer, kn = top(byKiller)
  local zone, zn = top(byZone)
  local head = plural(k, "mort", "morts") .. " ce soir"
  local half = k / 2
  if high >= half then
    return head .. " : surtout des monstres 3 niveaux au-dessus", "Vise des monstres de ton niveau", "quests"
  elseif kn >= 2 then
    return fmt("%s : %d fois face à %s", head, kn, short(killer, 28)), "Évite-le ou reviens à plusieurs", "group"
  elseif crowd >= half then
    return head .. " : souvent face à 3 monstres ou plus", "Tire-les un par un", "now"
  elseif elite >= half then
    return head .. " : surtout contre des élites", "Les élites, c'est en groupe", "group"
  elseif zn >= 2 and zn >= half then
    return fmt("%s : %d dans %s", head, zn, short(zone, 28)), "Zone trop dure, vois l'onglet Quêtes", "quests"
  end
  return head, "Remonte PV et mana avant chaque combat", "now"
end

--- Ligne de bilan (fonction pure) : s = { start, last, xp, quests, money0, money, lv0, lv, deaths }
function M.RecapText(s, label)
  if type(s) ~= "table" then return nil end
  local parts = {}
  local lv = (tonumber(s.lv) or 0) - (tonumber(s.lv0) or 0)
  if lv > 0 then parts[#parts + 1] = "+" .. plural(lv, "niveau", "niveaux") end
  if (tonumber(s.quests) or 0) > 0 then parts[#parts + 1] = plural(s.quests, "quête", "quêtes") end
  local dm = (tonumber(s.money) and tonumber(s.money0)) and (s.money - s.money0) or 0
  if dm > 0 then parts[#parts + 1] = "+" .. money(dm) end
  if lv == 0 and (tonumber(s.xp) or 0) > 0 then parts[#parts + 1] = n(s.xp) .. " XP" end
  local d = max(0, (tonumber(s.last) or 0) - (tonumber(s.start) or 0))
  local head = (label or "Ma session") .. " : " .. dur(d)
  if #parts == 0 then return head end
  return head .. ", " .. tconcat(parts, ", ")
end

-- ------------------------------------------------------------------ état de la session
local track = { xp = nil, max = nil, lv = nil }

local function newSession()
  local t = now()
  local xp, xmax = myXP()
  track.xp, track.max, track.lv = xp, xmax, myLevel()
  return { start = t, last = t, xp = 0, quests = 0, money0 = myMoney(), money = myMoney(), lv0 = myLevel(),
    lv = myLevel(), levels = {}, deaths = {}, loot = {}, best = false, pauses = 0, trendAt = 0 }
end

function M.Cur()
  if not M.char then return nil end
  local s = M.char.cur
  if type(s) ~= "table" then
    s = newSession()
    M.char.cur = s
  end
  return s
end

--- au login : continuer (reload / courte coupure) ou clore l'ancienne session et en ouvrir une neuve
function M.StartOrResume()
  if not M.char then return end
  local s, t = M.char.cur, now()
  if type(s) == "table" and t - (tonumber(s.last) or 0) <= GAP then
    local xp, xmax = myXP()
    track.xp, track.max, track.lv = xp, xmax, myLevel()
    return s, false
  end
  if type(s) == "table" and (tonumber(s.last) or 0) - (tonumber(s.start) or 0) >= MIN_SHOWN then
    M.char.prev = { start = s.start, last = s.last, xp = s.xp, quests = s.quests, money0 = s.money0, money = s.money,
      lv0 = s.lv0, lv = s.lv, deaths = #(s.deaths or {}), best = s.best, seen = false }
  end
  M.char.cur = newSession()
  return M.char.cur, true
end

local function touch(s)
  s.last = now()
  s.lv = myLevel()
  s.money = myMoney() or s.money
end

function M.OnXP()
  local s = M.Cur()
  if not s then return end
  local xp, xmax = myXP()
  local lv = myLevel()
  if xp and track.xp and track.lv then
    local gain = 0
    if lv == track.lv and xp >= track.xp then gain = xp - track.xp
    elseif lv > track.lv and track.max then gain = max(0, track.max - track.xp) + xp end
    if gain > 0 and gain < 1e7 then s.xp = (s.xp or 0) + gain end
  end
  track.xp, track.max, track.lv = xp, xmax, lv
  touch(s)
end

function M.OnLevel(lv)
  local s = M.Cur()
  if not s then return end
  lv = tonumber(lv) or myLevel()
  s.levels = s.levels or {}
  if #s.levels < 10 then s.levels[#s.levels + 1] = { lv = lv, t = now() } end
  touch(s)
  s.lv = max(s.lv or 0, lv)
  M.Refresh(1)
end

function M.OnQuest(questID, xpReward)
  local s = M.Cur()
  if not s then return end
  s.quests = (s.quests or 0) + 1
  local xp = tonumber(xpReward) or 0
  if xp > 0 and (not s.best or xp > (tonumber(s.best.xp) or 0)) then
    local CQ = g("C_QuestLog")
    local title = (CQ and try(CQ.GetTitleForQuestID, questID)) or M.lastTitle
    if title and title ~= "" then s.best = { title = short(title, 40), xp = xp } end
  end
  touch(s)
  M.Refresh(1)
end

local selfLoot
local function lootPattern()
  if selfLoot ~= nil then return selfLoot end
  local p = g("LOOT_ITEM_SELF")
  selfLoot = type(p) == "string" and p:match("^(.-)%%s") or false
  return selfLoot
end
function M.OnLoot(msg)
  if type(msg) ~= "string" or not msg:find("|Hitem:", 1, true) then return end
  local prefix = lootPattern()
  if not prefix or prefix == "" or msg:sub(1, #prefix) ~= prefix then return end
  local link = msg:match("(|c%x+|Hitem:.-|h.-|h|r)") or msg:match("(|Hitem:.-|h.-|h)")
  if not link then return end
  local API = T.API or {}
  local name, _, quality = try(API.ItemInfo, link)
  quality = tonumber(quality)
  if not (name and quality and quality >= 3) then return end
  local s = M.Cur()
  if not s then return end
  s.loot = s.loot or {}
  if #s.loot < 3 then s.loot[#s.loot + 1] = { name = short(name, 36), q = quality } end
end

local QUAL = { [3] = "rare", [4] = "épique", [5] = "légendaire" }

-- ------------------------------------------------------------------ morts
function M.OnDeath()
  local s = M.Cur()
  if not s or not opt("deaths") then return end
  local F = T.modules and T.modules.Fight
  local r = F and F.lastRecap
  if type(r) == "table" and now() - (tonumber(r.time) or 0) > 10 then r = nil end
  local d = { t = now(), zone = short(try(g("GetZoneText")) or "", 40) }
  if r then
    d.killer, d.attackers, d.elites = r.killer and short(r.killer, 30) or nil, r.attackers, r.elites
    if r.killerLvl then d.gap = r.killerLvl - myLevel() end
  else
    local UE = g("UnitExists")
    if UE and try(UE, "target") and try(g("UnitCanAttack"), "player", "target") then
      d.killer = short(sv(try(g("UnitName"), "target")) or "", 30)
      local tl = num(try(g("UnitLevel"), "target"))
      if tl and tl > 0 then d.gap = tl - myLevel() end
    end
  end
  if d.killer == "" then d.killer = nil end
  s.deaths = s.deaths or {}
  s.deaths[#s.deaths + 1] = d
  while #s.deaths > 30 do table.remove(s.deaths, 1) end
  touch(s)
  M.Refresh(1)
end

function M.RecentDeaths(s, window)
  local out, t = {}, now()
  for _, d in ipairs((s and s.deaths) or {}) do
    if not window or t - (tonumber(d.t) or 0) <= window then out[#out + 1] = d end
  end
  return out
end

local function openTab(id)
  return function() if T.SelectTab then pcall(T.SelectTab, T, id) end end
end

-- ------------------------------------------------------------------ carte « Infos »
function M.RestTip()
  local rest = try(g("IsResting"))
  if rest then return "Tu es en zone de repos : déconnecte-toi ici pour l'XP reposée" end
  local bind = try(g("GetBindLocation"))
  if type(bind) == "string" and bind ~= "" then
    return "Pour finir : déconnecte-toi à une auberge (ta pierre : " .. short(bind, 30) .. ")"
  end
  return "Pour finir : déconnecte-toi à une auberge (XP reposée)"
end

function M.Lines()
  local out = {}
  if not M.char then return out end
  local s = M.Cur()
  local t = now()
  local function add(text, prio, onClick, tooltip)
    if text and text ~= "" then out[#out + 1] = { text = text, prio = prio or 1, onClick = onClick, tooltip = tooltip } end
  end
  local p = M.char.prev
  if opt("prev") and type(p) == "table" and not p.seen then
    add(M.RecapText(p, "Dernière session"), 1, function() M.DismissPrev() end,
      { "Bilan gardé à ta dernière déconnexion", "Clic : ranger" })
  end
  if s and opt("card") and t - (tonumber(s.start) or t) >= MIN_SHOWN then
    touch(s)
    add(M.RecapText(s), 1, function() M.Toggle() end,
      { "Ta soirée en un coup d'oeil", M.open and "Clic : refermer le détail" or "Clic : voir le détail" })
    if M.open then
      local d = max(1, t - s.start)
      if (s.xp or 0) > 0 then add(fmt("XP gagnée : %s en %s", n(s.xp), dur(d)), 1, function() M.Toggle() end,
        { "XP par heure, mesurée et datée : onglet « Stats »." }) end
      for i, l in ipairs(s.levels or {}) do
        if i <= 3 then add(fmt("Beau moment : niveau %d atteint", l.lv), 1, function() M.Toggle() end) end
      end
      if s.best then add(fmt("Meilleure quête : %s (+%s XP)", s.best.title, n(s.best.xp)), 1, function() M.Toggle() end) end
      for _, it in ipairs(s.loot or {}) do
        add(fmt("Beau butin : %s (%s)", it.name, QUAL[it.q] or "rare"), 1, function() M.Toggle() end)
      end
      local dm = (s.money or 0) - (s.money0 or 0)
      if s.money and s.money0 and dm < 0 then add("Or dépensé : " .. money(-dm), 1, function() M.Toggle() end) end
      add(M.RestTip(), 1, function() M.Toggle() end)
    end
  end
  if s and opt("deaths") then
    local txt, tip, tab = M.DeathTrend(s.deaths)
    if txt then add(txt .. ". " .. tip, 2, openTab(tab ~= "now" and tab or "info"), { "Pourquoi je meurs", tip }) end
  end
  local mt, mtip = M.MountLine()
  if mt then add(mt, 1, nil, mtip) end
  return out
end

--- monture du niveau 40 (niveaux 30 à 39) : or réel face à l'objectif réglable ; durée seulement si l'or/h est mesuré
function M.MountText(lvl, money_, goal, goldPerHour)
  lvl, money_, goal = tonumber(lvl) or 0, tonumber(money_) or 0, tonumber(goal) or 0
  if lvl < 30 or lvl >= 40 or goal <= 0 or money_ >= goal then return nil end
  local txt = fmt("Monture au niveau 40 : %s de côté sur %s", money(money_), money(goal))
  if goldPerHour and goldPerHour > 0 then txt = txt .. fmt(", ≈ %s de jeu à ton rythme", dur((goal - money_) / goldPerHour * 3600)) end
  return txt
end
function M.MountLine()
  if not opt("mount") then return nil end
  local St = T.Stats
  local gph = St and St.GoldPerHour and St:GoldPerHour() or nil
  local txt = M.MountText(myLevel(), myMoney(), (tonumber(M.db and M.db.mountGoal) or 90) * 10000, gph)
  if not txt then return nil end
  return txt, { "Monture + compétence de monte au niveau 40 : le prix exact dépend de ta race et de ta réputation.",
    "Règle l'objectif dans les options (Ma session)." }
end

function M.Toggle()
  M.open = not M.open
  pcall(M.Update)
end
function M.DismissPrev()
  if M.char and type(M.char.prev) == "table" then M.char.prev.seen = true end
  M:Clear("prev")
  pcall(M.Update)
end

-- ------------------------------------------------------------------ rappels (« Maintenant »)
function M.Nudges()
  local s = M.Cur()
  if not s then return end
  local t = now()
  -- pourquoi je meurs : 3 morts en 30 min -> une ligne, une fois par palier, quand tu es en vie
  local recent = M.RecentDeaths(s, TREND_WINDOW)
  if opt("deaths") and #recent >= 3 and #recent > (s.trendN or 0) then
    local txt, tip, tab = M.DeathTrend(recent)
    if txt then
      s.trendN = #recent
      M:Suggest("deaths", (txt:gsub(" ce soir", " en 30 min")) .. ". " .. tip, 2,
        { ttl = 300, tab = "info", topic = "death", sound = false, onClick = openTab(tab ~= "now" and tab or "info"),
          tooltip = { "Pourquoi je meurs", tip, "Clic : ouvrir l'onglet utile" } })
    end
  elseif #recent < 3 then
    s.trendN = 0
  end
  -- pause
  local every = (tonumber(M.db and M.db.pauseHours) or 2) * 3600
  local k = floor((t - (tonumber(s.start) or t)) / every)
  if opt("pause") and k >= 1 and k > (s.pauses or 0) then
    s.pauses = k
    M:Suggest("pause", fmt("Fais une petite pause : %s de jeu d'affilée", dur(k * every)), 1,
      { ttl = 600, tab = "info", sound = false, onClick = function() M.open = true; openTab("info")(); M:Clear("pause"); pcall(M.Update) end,
        tooltip = { "Clic : voir le bilan de la session" } })
  end
end

local pending, lastRun = false, 0
function M.Update()
  lastRun = now()
  if T.SetTabLines then pcall(T.SetTabLines, T, "info", M.Lines(), "Session") end
  pcall(M.Nudges)
end
function M.Refresh(delay)
  if pending then return end
  pending = true
  T:After(delay or 1, function() pending = false pcall(M.Update) end)
end
function M:AutoRefresh()
  if now() - lastRun >= 30 then pcall(M.Update) end
end

function M.WriteSummary()
  local s = T:Summary()
  local c = M.char and M.char.cur
  if type(s) ~= "table" or type(c) ~= "table" then return end
  touch(c)
  local trend, tip = M.DeathTrend(c.deaths)
  s.session = { minutes = floor(((c.last or 0) - (c.start or 0)) / 60), xp = c.xp, quests = c.quests,
    levels = (c.lv or 0) - (c.lv0 or 0), money = (c.money or 0) - (c.money0 or 0), deaths = #(c.deaths or {}),
    trend = trend, tip = tip, best = c.best and c.best.title or nil,
    updated = now() }   -- (QA-APP) l'appli écarte un bloc qui n'est plus réécrit (module coupé)
end

function M.Report()
  M.open = true
  pcall(M.Update)
  if T.SelectTab then pcall(T.SelectTab, T, "info") end
  if T.Show then pcall(T.Show, T) end
end

function M:OnEnable()
  local _, fresh = M.StartOrResume()
  local p = M.char and M.char.prev
  if fresh and opt("prev") and type(p) == "table" and not p.seen then
    T:After(8, function()
      M:Suggest("prev", M.RecapText(p, "Dernière session"), 1, { ttl = 180, tab = "info", topic = "changes", sound = false,
        onClick = function() M.DismissPrev() end, tooltip = { "Bilan gardé à ta dernière déconnexion", "Clic : ranger" } })
    end)
  end
  M:RegisterEvent("PLAYER_XP_UPDATE", function() M.OnXP() end)
  M:RegisterEvent("PLAYER_LEVEL_UP", function(_, _, lv) M.OnLevel(lv) end)
  M:RegisterEvent("QUEST_COMPLETE", function() M.lastTitle = sv(try(g("GetTitleText"))) end)
  M:RegisterEvent("QUEST_TURNED_IN", function(_, _, id, xp) M.OnQuest(id, xp) M.lastTitle = nil end)
  M:RegisterEvent("PLAYER_MONEY", function() local s = M.Cur() if s then touch(s) end end)
  M:RegisterEvent("CHAT_MSG_LOOT", function(_, _, msg) pcall(M.OnLoot, msg) end)
  M:RegisterEvent("PLAYER_DEAD", function() T:After(0.3, function() pcall(M.OnDeath) end) end)
  M:RegisterEvent("PLAYER_UNGHOST", function() M.Refresh(2) end)
  M:RegisterEvent("PLAYER_ALIVE", function() M.Refresh(2) end)
  M:RegisterEvent("PLAYER_LOGOUT", function() local s = M.char and M.char.cur if type(s) == "table" then touch(s) end end)
  M:On("SUMMARY", function() pcall(M.WriteSummary) end)
  if T.RegisterSlash then
    T:RegisterSlash("bilan", function() M.Report() end, "bilan : ta session (XP, quêtes, or, morts) et où te déconnecter")
  end
  M.Refresh(3)
end

function M:OnDisable()
  if T.SetTabLines then pcall(T.SetTabLines, T, "info", {}, "Session") end
end
function M:OnOptionChanged() M.Refresh(0.2) end

T.Session = M
