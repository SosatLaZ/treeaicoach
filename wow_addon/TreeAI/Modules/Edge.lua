-- TreeAI — module Edge (agent CHEAT25) : 25 avantages « cheatés » mais LÉGAUX. Chacun = UNE ligne claire qui
-- commence par un verbe (ou un clic), cachée tant qu'elle ne sert à rien, coupable une par une dans les options.
-- Rien n'est fait à la place du joueur : pas d'achat, pas de vente, pas de cible, pas de sort. Un clic ouvre au plus
-- la boussole, le journal de quêtes, un chuchotement pré-rempli (le joueur appuie sur Entrée) ou le suivi de la
-- mini-carte. Données utilisées : API du jeu, roster de guilde (nom / zone / niveau en ligne), QuestieDB en lecture
-- seule, mesures de nos modules (Predict, Boost, Auction, Gear, Prof). Aucun chiffre inventé : sans donnée, rien.
--   Groupe  : eliteGuild, dungeonReady, friendDanger, escortReady
--   Farm    : tagRace, respawnCamp, namedRepop, grindSpot, restedWindow, campBuff, gatherTrack
--   Quêtes  : npcQuest, keepItem, chainBreadcrumb
--   Or      : foodBuy, vendorFlip, boeWorth, cheapGear, trainerSave, mountSave
--   Progrès : petTrainer, weaponTrainer, dualSpec, legacyNext, worldBuff
-- Lua 5.1, aucune globale, toutes les API testées (pcall), valeurs secrètes (Forever, en combat) ignorées.
local T = _G.TreeAI
if not T or not T.RegisterModule then return end
local API = T.API or {}
local G = _G

local FEATURES = {
  { "eliteGuild", "Quête élite : joueurs de ta guilde dans ta zone", "Groupe" },
  { "dungeonReady", "Donjon : « lancez, tout le monde a la quête »", "Groupe" },
  { "friendDanger", "Un membre du groupe à côté de toi est presque mort", "Groupe" },
  { "escortReady", "Escorte : vie pleine avant de lancer, échec = reprendre", "Groupe" },
  { "tagRace", "Monstre déjà tapé par un autre : prends le suivant", "Farm" },
  { "respawnCamp", "Camp de quête : temps de repop appris (« reste ici 1 min »)", "Farm" },
  { "namedRepop", "Nommé / rare : temps de repop appris", "Farm" },
  { "grindSpot", "Plus de quêtes : meilleur coin de farm pour ton niveau", "Farm" },
  { "restedWindow", "Repos : finir le niveau à XP ×2 en tuant des monstres", "Farm" },
  { "campBuff", "Camp Forever : pose ton camp quand tu farmes au même endroit", "Farm" },
  { "gatherTrack", "Récolte : active le suivi des plantes / filons", "Farm" },
  { "npcQuest", "Un PNJ proche a une quête pour toi", "Quêtes" },
  { "keepItem", "Chez le marchand : garde l'objet demandé par une quête", "Quêtes" },
  { "chainBreadcrumb", "Quête de liaison devenue inutile", "Quêtes" },
  { "foodBuy", "Chez le marchand : nourriture / eau pour 1 h de jeu", "Or" },
  { "vendorFlip", "Chez le marchand : objet qui se revend plus cher à l'HV", "Or" },
  { "boeWorth", "Butin lié quand équipé qui vaut cher à l'HV", "Or" },
  { "cheapGear", "À l'HV : amélioration pas chère pour ton niveau", "Or" },
  { "trainerSave", "Garde l'or du prochain maître de classe", "Or" },
  { "mountSave", "Économies pour la monture du niveau 40", "Or" },
  { "petTrainer", "Chasseur : nouveau rang au maître des familiers", "Progrès" },
  { "weaponTrainer", "Armes à apprendre chez le maître d'armes", "Progrès" },
  { "dualSpec", "Double spé dès que tu as l'or (niv 40)", "Progrès" },
  { "legacyNext", "Prochain point Legacy (métiers) tout proche", "Progrès" },
  { "worldBuff", "Buff mondial annoncé en ville : reste là", "Progrès" },
}

local defaults = { enabled = true, mountGoal = 90, camps = {} }
local options = {}
for _, f in ipairs(FEATURES) do
  defaults[f[1]] = true
  options[#options + 1] = { key = f[1], type = "toggle", label = f[2], section = f[3] }
end
options[#options + 1] = { key = "mountGoal", type = "range", label = "Or visé pour la monture (po)", min = 40, max = 200,
  step = 5, section = "Or" }

local M = T:RegisterModule("Edge", {
  title = "Coups d'avance",
  defaults = defaults,
  charDefaults = { ack = {} },
  options = options,
})
T.Edge = M
M.FEATURES = FEATURES

local floor, max, min, ceil, abs, sqrt = math.floor, math.max, math.min, math.ceil, math.abs, math.sqrt
local fmt, tconcat = string.format, table.concat
local function g(name) return rawget(G, name) end
local function pass(ok, ...) if ok then return ... end return nil end
local function try(fn, ...) if type(fn) ~= "function" then return nil end return pass(pcall(fn, ...)) end
local function secret(v) local f = g("issecretvalue") return type(f) == "function" and try(f, v) and true or false end
local function sv(v) if v == nil or secret(v) then return nil end return v end
local function now() return try(g("GetTime")) or 0 end
local function n1(v) return tonumber(v) end   -- 1re valeur seulement (tonumber(a, b) = base b)
local function opt(k) return M.db and M.db[k] ~= false end
local function mod(n) local m = T.modules and T.modules[n] return (m and (not m.IsEnabled or m:IsEnabled())) and m or nil end
local function level() return tonumber(sv(try(g("UnitLevel"), "player"))) or 1 end
local function zone() return sv(try(g("GetRealZoneText"))) or sv(try(g("GetZoneText"))) or "" end
local function inCombat() return T.InCombat and T:InCombat() or false end
local function inInstance() return try(g("IsInInstance")) and true or false end
local function resting() return try(g("IsResting")) and true or false end
local function money() return tonumber(sv(API.Money and API.Money() or try(g("GetMoney")))) or 0 end
local function groupSize() return n1(try(g("GetNumGroupMembers"))) or 0 end
local function low(s) return tostring(s or ""):lower() end

-- nom affichable : jamais vide, jamais « ? », jamais un lien brut ; coupé proprement
local function nm(s, n)
  if s == nil or secret(s) then return nil end
  s = tostring(s):gsub("|c%x%x%x%x%x%x%x%x", ""):gsub("|r", ""):gsub("|H.-|h(.-)|h", "%1"):gsub("^%s+", ""):gsub("%s+$", "")
  s = s:gsub("%-[^%s%-]+$", function(realm) return realm:find("^%-%u") and "" or realm end)   -- Nom-Royaume -> Nom
  if s == "" or s:find("?", 1, true) or s == "nil" then return nil end
  n = n or 30
  if #s > n then
    local cut = s:sub(1, n - 1):gsub("[\192-\255][\128-\191]*$", "")
    s = cut .. "…"
  end
  return s
end
M.Name = nm

local function goldText(c)
  c = floor(abs(tonumber(c) or 0))
  local po, pa, pc = floor(c / 10000), floor(c / 100) % 100, c % 100
  if po > 0 then return pa > 0 and fmt("%d po %d pa", po, pa) or fmt("%d po", po) end
  if pa > 0 then return fmt("%d pa", pa) end
  return fmt("%d pc", pc)
end
M.Gold = goldText
local function durText(s)
  s = floor(tonumber(s) or 0)
  if s < 60 then return fmt("%d s", max(1, s)) end
  local m, r = floor(s / 60), s % 60
  if m >= 60 then return fmt("%d h %02d", floor(m / 60), m % 60) end
  if r >= 10 and m < 5 then return fmt("%d min %02d", m, r) end
  return fmt("%d min", m)
end
M.Dur = durText

-- o.window (s) + o.sig : une même ligne (même clé + même sens) n'est montrée que pendant `window` secondes,
-- puis se tait jusqu'à ce que le sens change (autre quête, autre niveau…) : utile, puis discret.
local firstAt, firstN = {}, 0
local function say(key, text, prio, o)
  if not opt(key) or not text then M:Clear(key) return nil end
  o = o or {}
  if o.window then
    local sig = key .. "|" .. tostring(o.sig or text)
    if not firstAt[sig] then
      firstN = firstN + 1
      if firstN > 300 then firstAt, firstN = {}, 0 end
      firstAt[sig] = now()
    end
    if now() - firstAt[sig] > o.window then M:Clear(key) return nil end
  end
  if o.sound == nil then o.sound = false end
  if o.ttl == nil then o.ttl = 60 end
  if o.cooldown == nil then o.cooldown = 120 end
  o.chat = false
  M.shown = M.shown or {}
  M.shown[key] = (M.shown[key] or 0) + 1
  return M:Suggest(key, text, prio or 2, o)
end
local function drop(key) M:Clear(key) end

-- position du joueur (Compass) et distance en yards entre deux points de la même carte
local function me()
  local C = mod("Compass") or T.Compass
  if C and C.Player then return C.Player() end
  return nil
end
local function distYd(a, b)
  local C = T.Compass or (T.modules and T.modules.Compass)
  local Geo = C and C.Geo
  if Geo and Geo.Vector and a and b then
    local d = Geo.Vector(a, b)
    if d then return d end
  end
  if a and b and a.mapID == b.mapID and a.x and b.x then
    local dx, dy = (a.x - b.x) * 3000, (a.y - b.y) * 2000
    return sqrt(dx * dx + dy * dy)
  end
  return nil
end
local function meters(yd) return floor((yd or 0) * 0.9144 / 10 + 0.5) * 10 end

local function compassTo(mapID, x, y, label)
  return function()
    local C = T.Compass or (T.modules and T.modules.Compass)
    if C and C.SetTarget then try(C.SetTarget, C, { mapID = mapID, x = x, y = y, label = label, source = "Edge" }) end
  end
end

local CAPITALS = { ["hurlevent"] = true, ["stormwind"] = true, ["forgefer"] = true, ["ironforge"] = true,
  ["darnassus"] = true, ["orgrimmar"] = true, ["les pitons-du-tonnerre"] = true, ["pitons-du-tonnerre"] = true,
  ["thunder bluff"] = true, ["fossoyeuse"] = true, ["undercity"] = true }
local function inCapital() return CAPITALS[low(zone())] or false end
M.CAPITALS = CAPITALS

-- ===================================================================================== moteur pur (testable)
local E = {}
M.Engine = E

--- membres de guilde en ligne dans ta zone, niveau proche : roster = { {name, level, zone, online} }
function E.GuildHelpers(roster, myZone, myName, qLevel, maxN)
  local out = {}
  myZone, qLevel = low(myZone), tonumber(qLevel) or 0
  for _, r in ipairs(roster or {}) do
    local n = nm(r.name, 16)
    local lv = tonumber(r.level) or 0
    if n and r.online and low(r.zone) == myZone and myZone ~= "" and n ~= myName
        and (qLevel <= 0 or (lv >= qLevel - 3 and lv <= qLevel + 6)) then
      out[#out + 1] = n
      if #out >= (maxN or 2) then break end
    end
  end
  return out
end

--- temps de repop appris : médiane des 5 derniers écarts mort -> réapparition (30 s – 30 min)
function E.LearnRespawn(rec, sample)
  sample = tonumber(sample)
  if not sample or sample < 30 or sample > 1800 then return rec end
  rec = type(rec) == "table" and rec or { s = {} }
  rec.s = type(rec.s) == "table" and rec.s or {}
  rec.s[#rec.s + 1] = floor(sample)
  while #rec.s > 5 do table.remove(rec.s, 1) end
  return rec
end
function E.Respawn(rec)
  if type(rec) ~= "table" or type(rec.s) ~= "table" or #rec.s < 3 then return nil end
  local c = {}
  for i, v in ipairs(rec.s) do c[i] = v end
  table.sort(c)
  return c[floor((#c + 1) / 2)], #rec.s
end
--- prochain repop (s) d'après les morts récentes { t } et le temps appris ; nil si rien dans [5, 150] s
function E.NextRepop(kills, respawn, t)
  if not respawn then return nil end
  local best
  for _, k in ipairs(kills or {}) do
    local left = k.t + respawn - t
    if left >= 5 and left <= 150 and (not best or left < best) then best = left end
  end
  return best
end

--- heure d'apparition d'un monstre lue dans son GUID (« Creature-0-serveur-instance-zone-PNJ-spawnUID ») :
--- les 23 bits bas du spawnUID = heure serveur d'apparition modulo 2^23 s (≈ 97 jours), donnée publique du GUID.
--- nil si le GUID n'a pas cette forme ou si l'heure obtenue n'est pas dans les 30 dernières minutes (client qui ne
--- suit pas ce format : on n'apprend rien plutôt qu'un faux chiffre).
local P23 = 8388608
function E.SpawnTime(guid, serverNow)
  if type(guid) ~= "string" or not tonumber(serverNow) then return nil end
  local kind, uid = guid:match("^(%a+)%-.-%-(%x+)$")
  if (kind ~= "Creature" and kind ~= "Vehicle") or not uid or #uid > 12 then return nil end
  local low = (tonumber(uid, 16) or 0) % P23
  local t = serverNow - serverNow % P23 + low
  if t > serverNow + 5 then t = t - P23 end
  if serverNow - t > 1800 or t > serverNow + 5 then return nil end
  return t
end

--- meilleur coin de farm : monstres non élites, niveau [L-2, L+1], le plus d'apparitions ; centre du groupe
function E.GrindPick(hostiles, L)
  local by = {}
  for _, h in ipairs(hostiles or {}) do
    local lv = tonumber(h.lvl)
    if h.id and lv and not h.elite and lv >= L - 2 and lv <= L + 1 and nm(h.name) then
      local b = by[h.id]
      if not b then b = { id = h.id, name = nm(h.name), lvl = lv, n = 0, sx = 0, sy = 0 } by[h.id] = b end
      b.n, b.sx, b.sy = b.n + 1, b.sx + h.x, b.sy + h.y
    end
  end
  local best
  for _, b in pairs(by) do
    local w = b.n * ((b.lvl >= L - 1 and b.lvl <= L) and 1.2 or 1)
    b.score = w
    if b.n >= 6 and (not best or w > best.score or (w == best.score and b.id < best.id)) then best = b end
  end
  if best then best.x, best.y = best.sx / best.n, best.sy / best.n end
  return best
end

--- repos qui suffit pour finir le niveau en tuant : nombre de monstres (XP ×2) ou nil
function E.RestedFinish(remaining, rested, avgKill)
  remaining, rested, avgKill = tonumber(remaining), tonumber(rested), tonumber(avgKill)
  if not (remaining and rested and avgKill) or avgKill <= 0 or remaining <= 0 then return nil end
  if remaining > 2 * rested then return nil end
  return ceil(remaining / (2 * avgKill))
end

--- quantité à acheter pour 1 h : arrondie au multiple de 5 au-dessus ; nil si inutile
function E.BuyForHour(perHour, have)
  perHour, have = tonumber(perHour), tonumber(have) or 0
  if not perHour or perHour <= 0 then return nil end
  local need = ceil(perHour) - have
  if need < 3 then return nil end
  return ceil(need / 5) * 5
end

--- marchand -> HV : gain net par unité (commission 5 %, dépôt) ; nil si pas rentable (≥ 20 pa et ≥ +50 %)
function E.VendorFlip(unitBuy, market, deposit)
  unitBuy, market, deposit = tonumber(unitBuy), tonumber(market), tonumber(deposit) or 0
  if not (unitBuy and market) or unitBuy <= 0 then return nil end
  local net = floor(market * 0.95 - unitBuy - deposit)
  if net >= 2000 and net >= unitBuy * 0.5 then return net end
  return nil
end

--- objet lié quand équipé : vaut le coup à l'HV (≥ 3× le marchand et ≥ +50 pa)
function E.BoeWorth(market, vendor)
  market, vendor = tonumber(market), tonumber(vendor) or 0
  if not market or market <= 0 then return false end
  local net = market * 0.95
  return net >= 3 * vendor and net >= vendor + 5000
end

-- Grondement (maître des familiers) : niveaux des rangs
E.GROWL = { 10, 20, 30, 40, 50, 60 }
function E.GrowlRank(L)
  local rank, at = nil, nil
  for i, lv in ipairs(E.GROWL) do if L >= lv then rank, at = i + 1, lv end end
  return rank, at
end

-- armes utiles à apprendre par classe (nom FR, nom EN) — maître d'armes, 10 pa
E.WEAPONS = {
  WARRIOR = { { "Épées à deux mains", "Two-Handed Swords" }, { "Haches à deux mains", "Two-Handed Axes" },
    { "Masses à deux mains", "Two-Handed Maces" }, { "Armes d'hast", "Polearms" }, { "Arcs", "Bows" } },
  HUNTER = { { "Armes d'hast", "Polearms" }, { "Bâtons", "Staves" }, { "Épées à deux mains", "Two-Handed Swords" },
    { "Haches à deux mains", "Two-Handed Axes" }, { "Arcs", "Bows" }, { "Fusils", "Guns" }, { "Arbalètes", "Crossbows" } },
  ROGUE = { { "Dagues", "Daggers" }, { "Épées", "Swords" }, { "Masses", "Maces" }, { "Armes de pugilat", "Fist Weapons" } },
  PALADIN = { { "Épées à deux mains", "Two-Handed Swords" }, { "Masses à deux mains", "Two-Handed Maces" },
    { "Haches à deux mains", "Two-Handed Axes" }, { "Armes d'hast", "Polearms" } },
  SHAMAN = { { "Haches à deux mains", "Two-Handed Axes" }, { "Masses à deux mains", "Two-Handed Maces" }, { "Bâtons", "Staves" } },
  DRUID = { { "Bâtons", "Staves" }, { "Masses à deux mains", "Two-Handed Maces" }, { "Armes d'hast", "Polearms" } },
  PRIEST = { { "Bâtons", "Staves" }, { "Dagues", "Daggers" } },
  MAGE = { { "Bâtons", "Staves" }, { "Dagues", "Daggers" }, { "Épées", "Swords" } },
  WARLOCK = { { "Bâtons", "Staves" }, { "Dagues", "Daggers" }, { "Épées", "Swords" } },
}
local ALL_WEAPONS = {}
for _, list in pairs(E.WEAPONS) do for _, w in ipairs(list) do ALL_WEAPONS[low(w[1])], ALL_WEAPONS[low(w[2])] = true, true end end
for _, n in ipairs({ "épées", "swords", "haches", "axes", "masses", "maces", "dagues", "daggers", "arcs", "bows",
  "fusils", "guns", "arbalètes", "crossbows", "bâtons", "staves", "armes de jet", "thrown", "baguettes", "wands" }) do
  ALL_WEAPONS[n] = true
end
--- armes manquantes (liste ordonnée) ; nil si on ne voit aucune compétence d'arme (API absente)
function E.MissingWeapons(class, knownNames)
  local list = E.WEAPONS[class or ""]
  if not list then return nil end
  local known, any = {}, false
  for _, n in ipairs(knownNames or {}) do
    local k = low(n)
    known[k] = true
    if ALL_WEAPONS[k] then any = true end
  end
  if not any then return nil end
  local out = {}
  for _, w in ipairs(list) do
    if not known[low(w[1])] and not known[low(w[2])] then out[#out + 1] = w[1] end
  end
  return out
end

--- escorte : titre / objectif qui parle d'escorter, protéger, accompagner
local ESCORT = { "escort", "protég", "proteg", "accompagn", "protect" }
function E.IsEscort(q)
  if type(q) ~= "table" then return false end
  local txt = low(q.title)
  for _, o in ipairs(q.obj or {}) do txt = txt .. " " .. low(o.text or o.name) end
  for _, p in ipairs(ESCORT) do if txt:find(p, 1, true) then return true end end
  return false
end

--- buff mondial annoncé par un cri : nom du buff ou nil
E.WORLD_BUFFS = {
  { "onyxia", "Cri de ralliement du pourfendeur de dragons" },
  { "nefarian", "Cri de ralliement du pourfendeur de dragons" },
  { "rend", "Bénédiction du chef de guerre" },
  { "hakkar", "Esprit de Zandalar" },
}
function E.WorldBuff(msg, speaker)
  local s = low(msg) .. " " .. low(speaker)
  if s:find("onyxia", 1, true) or s:find("nefarian", 1, true) then
    if s:find("slain", 1, true) or s:find("vaincu", 1, true) or s:find("tué", 1, true) or s:find("mort", 1, true)
        or s:find("rallying", 1, true) or s:find("ralliement", 1, true) or s:find("tête", 1, true) or s:find("head", 1, true) then
      return E.WORLD_BUFFS[1][2]
    end
  end
  if s:find("rend", 1, true) and (s:find("blackhand", 1, true) or s:find("main-noire", 1, true)) then return E.WORLD_BUFFS[3][2] end
  if s:find("hakkar", 1, true) and (s:find("slain", 1, true) or s:find("vaincu", 1, true) or s:find("tué", 1, true)) then
    return E.WORLD_BUFFS[4][2]
  end
  return nil
end

--- nom de monstre d'un objectif « Kobold tué : 3/10 » -> « Kobold »
function E.MobOf(objName)
  local s = T.ObjectiveName and T.ObjectiveName(objName) or tostring(objName or "")
  s = s:gsub("%s+tuée?s?$", ""):gsub("%s+slain$", ""):gsub("%s+killed$", "")
  return s
end

-- ===================================================================================== données partagées
local function quests()
  local Q = mod("Quests")
  return (Q and Q.lastList) or {}
end
local function questsHere()
  local Q = mod("Quests")
  local qs = Q and Q.lastQuestie
  return (qs and qs.here) or {}
end
local function inLog(id)
  for _, q in ipairs(quests()) do if q.id == id then return q end end
  return nil
end
-- QuestieDB (lecture seule) : champ de quête / de PNJ
local function qField(id, key)
  local Q = T.modules and T.modules.Quests
  local db = Q and Q.QuestieDB and Q.QuestieDB()
  if db and db.QueryQuestSingle then return try(db.QueryQuestSingle, id, key) end
  local lib = g("LibQuestieDB")
  if type(lib) == "table" and type(lib.Quest) == "table" and type(lib.Quest.Get) == "function" then return try(lib.Quest.Get, id, key) end
  return nil
end
local function npcDB()
  local SP = T.modules and T.modules.SafePath
  return SP and SP.DB and try(SP.DB) or nil
end
local function questDone(id)
  local CQ = g("C_QuestLog")
  local fn = (CQ and CQ.IsQuestFlaggedCompleted) or g("IsQuestFlaggedCompleted")
  return try(fn, id) and true or false
end
local function auctionMarket(id, minConf)
  local A = T.modules and T.modules.Auction
  if not (A and A.Stats and id) then return nil end
  local st = try(A.Stats, id)
  if type(st) ~= "table" or not tonumber(st.market) then return nil end
  if (tonumber(st.conf) or 0) < (minConf or 0.3) then return nil end
  return tonumber(st.market)
end
local function itemID(link)
  if type(link) == "number" then return link end
  return tonumber(tostring(link or ""):match("item:(%d+)"))
end
local function skillNames()
  local out = {}
  for _, l in ipairs(API.SkillLines and API.SkillLines() or {}) do
    if l.name and not l.isHeader and not secret(l.name) then out[#out + 1] = l.name end
  end
  return out
end
local function playerClass() return T.PlayerClass and T:PlayerClass() or nil end

-- ===================================================================================== 1. quête élite + guilde
local roster, rosterAt, rosterAsk = {}, -1e9, -1e9
local function readRoster()
  local t = now()
  if t - rosterAsk > 60 then
    rosterAsk = t
    local CG = g("C_GuildInfo")
    if CG and CG.GuildRoster then try(CG.GuildRoster) else try(g("GuildRoster")) end
  end
  if t - rosterAt < 30 then return roster end
  rosterAt = t
  roster = {}
  local total = n1(try(g("GetNumGuildMembers"))) or 0
  for i = 1, min(total, 500) do
    local name, _, _, lv, _, z, _, _, online = try(g("GetGuildRosterInfo"), i)
    if name then roster[#roster + 1] = { name = sv(name), level = sv(lv), zone = sv(z), online = online and true or false } end
  end
  return roster
end
function M.CheckEliteGuild()
  if not opt("eliteGuild") or not try(g("IsInGuild")) or groupSize() >= 3 then return drop("eliteGuild") end
  local z = zone()
  local myName = nm(try(g("UnitName"), "player"), 16)
  for _, q in ipairs(quests()) do
    if (q.elite or q.groupTag) and not q.dungeon and not q.complete and not q.grey and q.zone == z then
      local names = E.GuildHelpers(readRoster(), z, myName, q.level, 2)
      local title = nm(q.title, 30)
      if #names > 0 and title then
        local first = names[1]
        return say("eliteGuild", fmt("Demande à %s (guilde, ta zone) : « %s » est élite", tconcat(names, ", "), title), 2,
          { ttl = 90, cooldown = 900, window = 180, sig = q.id, tooltip = "Clic : chuchotement pré-rempli (tu appuies sur Entrée).",
            onClick = function()
              local open = g("ChatFrame_OpenChat")
              if open then try(open, "/w " .. first .. " Partant pour « " .. title .. " » avec moi ") end
            end })
      end
    end
  end
  drop("eliteGuild")
end

-- ===================================================================================== 2. monstre déjà tapé
function M.CheckTag()
  if not opt("tagRace") then return end
  local U = "target"
  if inCombat() or not try(g("UnitExists"), U) or try(g("UnitIsDead"), U) or try(g("UnitIsPlayer"), U)
      or not try(g("UnitCanAttack"), "player", U) or not sv(try(g("UnitIsTapDenied"), U)) then
    return drop("tagRace")
  end
  say("tagRace", "Laisse ce monstre : déjà tapé par un autre joueur, prends le suivant", 2, { ttl = 10, cooldown = 30 })
end

-- ===================================================================================== 3–4. repop appris
local kills = {}      -- [nom] = { {t, mapID, x, y} } (session)
local classOf = {}    -- [nom] = "rare" | "rareelite" | "worldboss"
local seenGUID, seenN = {}, 0
local function serverNow() return n1(try(g("GetServerTime"))) or 0 end
local function camps()
  M.db.camps = type(M.db.camps) == "table" and M.db.camps or {}
  return M.db.camps
end
function M.RecordKill(name, t, pos)
  name = nm(name, 40)
  if not name then return end
  local list = kills[name]
  if not list then
    local n = 0
    for _ in pairs(kills) do n = n + 1 end
    if n > 60 then kills = {} end
    list = {}
    kills[name] = list
  end
  pos = pos or me() or {}
  list[#list + 1] = { t = t or now(), st = serverNow(), mapID = pos.mapID, x = pos.x, y = pos.y }
  while #list > 8 do table.remove(list, 1) end
end
local function learn(name, dt)
  local db = camps()
  local n = 0
  if not db[name] then for _ in pairs(db) do n = n + 1 end end
  if n < 150 then db[name] = E.LearnRespawn(db[name], dt) end
end
--- échantillon de repop : l'heure d'apparition du nouveau monstre (lue dans son GUID, voir E.SpawnTime) moins
--- l'heure de la plus ancienne mort non appariée du même nom près d'ici (FIFO). Sans heure fiable : rien.
local function sampleFromGUID(name, guid)
  local st = E.SpawnTime(guid, serverNow())
  local list = kills[name]
  if not (st and list) then return nil end
  -- appariement : la mort la plus proche du temps déjà appris, sinon la plus récente (≥ 30 s avant l'apparition)
  local p, known = me(), E.Respawn(camps()[name])
  local bi, bdt, bscore
  for i, k in ipairs(list) do
    local dt = k.st and (st - k.st)
    if dt and dt >= 30 and dt <= 1800 and ((not p or not k.x) or ((distYd(p, k) or 999) <= 80)) then
      local score = known and abs(dt - known) or dt
      if not bscore or score < bscore then bi, bdt, bscore = i, dt, score end
    end
  end
  if not bi then return nil end
  learn(name, bdt)
  table.remove(list, bi)
  return bdt
end
local function newGUID(guid)
  if not guid then return false end
  if seenGUID[guid] then return false end
  seenN = seenN + 1
  if seenN > 400 then seenGUID, seenN = {}, 0 end
  seenGUID[guid] = true
  return true
end
--- rare / nommé vu (cible, barre de nom)
function M.SeeAlive(name, guid)
  name = nm(name, 40)
  if not name or not newGUID(guid) then return end
  return sampleFromGUID(name, guid)
end
-- barres de nom visibles par nom : « reste ici » seulement quand le camp est vide
local visible, guidName = {}, {}
local function vcount(name) local n = 0 for _ in pairs(visible[name] or {}) do n = n + 1 end return n end
M.Visible = vcount
function M.PlateAdded(name, guid)
  name = nm(name, 40)
  if not (name and guid) then return end
  visible[name] = visible[name] or {}
  visible[name][guid], guidName[guid] = true, name
  if newGUID(guid) then return sampleFromGUID(name, guid) end
end
function M.PlateRemoved(guid)
  local name = guid and guidName[guid]
  if not name then return end
  guidName[guid] = nil
  if visible[name] then visible[name][guid] = nil end
end
-- hors combat : barres de nom relues (une disparition ratée en combat — GUID secret — ne bloque rien)
function M.RebuildVisible()
  local cur, names = {}, {}
  for i = 1, 40 do
    local u = "nameplate" .. i
    if try(g("UnitExists"), u) and not try(g("UnitIsDead"), u) and not try(g("UnitIsPlayer"), u)
        and try(g("UnitCanAttack"), "player", u) then
      local name, guid = nm(sv(try(g("UnitName"), u)), 40), sv(try(g("UnitGUID"), u))
      if name and guid then cur[name] = cur[name] or {} cur[name][guid] = true names[guid] = name end
    end
  end
  visible, guidName = cur, names
end
local function unitSeen(u)
  if not try(g("UnitExists"), u) or try(g("UnitIsDead"), u) or try(g("UnitIsPlayer"), u) then return end
  if not try(g("UnitCanAttack"), "player", u) then return end
  local name, guid = sv(try(g("UnitName"), u)), sv(try(g("UnitGUID"), u))
  if not name then return end
  local c = sv(try(g("UnitClassification"), u))
  if c == "rare" or c == "rareelite" or c == "worldboss" then
    classOf[name] = c
    M.SeeAlive(name, guid)
  elseif type(u) == "string" and u:find("^nameplate") then
    M.PlateAdded(name, guid)
  end
end
local function nearKills(name, p)
  local out = {}
  for _, k in ipairs(kills[name] or {}) do
    if not p or not k.x or (distYd(p, k) or 999) <= 80 then out[#out + 1] = k end
  end
  return out
end
function M.CheckRepop()
  if not (opt("respawnCamp") or opt("namedRepop")) or inCombat() or inInstance() then
    drop("respawnCamp") drop("namedRepop") return
  end
  local t, p = now(), me()
  local db = camps()
  M.RebuildVisible()
  -- 3. camp de quête : monstre d'un objectif encore à faire
  if opt("respawnCamp") then
    local best, bestName, bestN
    for _, q in ipairs(quests()) do
      if not q.complete then
        for _, o in ipairs(q.obj or {}) do
          if not o.finished then
            local mob = nm(E.MobOf(o.name or o.text), 40)
            local r, n = E.Respawn(mob and db[mob])
            local left = r and vcount(mob) == 0 and E.NextRepop(nearKills(mob, p), r, t)
            if left and (not best or left < best) then best, bestName, bestN = left, mob, n end
          end
        end
      end
    end
    if best then
      say("respawnCamp", fmt("Reste ici %s : %s repop (appris sur %d passages)", durText(best), nm(bestName, 28), bestN), 2,
        { ttl = ceil(best) + 5, cooldown = 30 })
    else
      drop("respawnCamp")
    end
  end
  -- 4. nommé / rare
  if opt("namedRepop") then
    local best, bestName
    for name, c in pairs(classOf) do
      local r = E.Respawn(db[name])
      local left = r and E.NextRepop(nearKills(name, p), r, t)
      if left and (not best or left < best) then best, bestName = left, name end
    end
    if best then
      say("namedRepop", fmt("Reste ici %s : %s (rare) repop ici", durText(best), nm(bestName, 30)), 2,
        { ttl = ceil(best) + 5, cooldown = 30 })
    else
      drop("namedRepop")
    end
  end
end

-- ===================================================================================== 5. coin de farm
local grindJob, grindMap, grindPick, grindAt = nil, nil, nil, -1e9
local function noQuestsLeft()
  local z = zone()
  for _, q in ipairs(quests()) do
    if not q.complete and not q.grey and q.zone == z then return false end
  end
  local Q = mod("Quests")
  local qs = Q and Q.lastQuestie
  if qs and (qs.hereCount or 0) > 0 then return false end
  return true
end
function M.CheckGrind()
  if not opt("grindSpot") or inCombat() or inInstance() then return drop("grindSpot") end
  local L = level()
  if L < 8 or L >= 60 or not noQuestsLeft() then grindJob = nil return drop("grindSpot") end
  local SP = T.modules and T.modules.SafePath
  local p = me()
  if not (SP and SP.IndexMap and p) then return drop("grindSpot") end
  if grindMap ~= p.mapID or (grindPick == false and now() - grindAt > 300) then grindMap, grindPick, grindJob = p.mapID, nil, nil end
  if grindPick == nil and not grindJob then
    local mapID = p.mapID
    grindJob = coroutine.create(function()
      local idx = SP.IndexMap(mapID, coroutine.yield)
      return idx and idx.hostile or {}
    end)
  end
  if grindJob then
    for _ = 1, 20 do
      local ok, res = coroutine.resume(grindJob)
      if not ok then grindJob, grindPick, grindAt = nil, false, now() break end
      if coroutine.status(grindJob) == "dead" then
        grindJob, grindAt = nil, now()
        grindPick = E.GrindPick(res, L) or false
        break
      end
    end
  end
  local b = grindPick
  if not b then return drop("grindSpot") end
  say("grindSpot", fmt("Farme %s (niv %d) : %d apparitions ici, clic = boussole", b.name, b.lvl, b.n), 1,
    { ttl = 300, cooldown = 600, window = 300, sig = b.id, onClick = compassTo(grindMap, b.x, b.y, b.name),
      tooltip = "Plus de quêtes de ton niveau dans la zone. Monstres non élites de ton niveau (QuestieDB)." })
end

-- ===================================================================================== 6. repos pour finir le niveau
function M.CheckRested()
  if not opt("restedWindow") or resting() or inInstance() or level() >= 60 then return drop("restedWindow") end
  local xp, xpMax = tonumber(sv(try(g("UnitXP"), "player"))), tonumber(sv(try(g("UnitXPMax"), "player")))
  local rested = tonumber(sv(try(g("GetXPExhaustion")))) or 0
  local B = mod("Boost")
  local st = B and B.XPStats and try(B.XPStats, B)
  local avgKill = type(st) == "table" and st.avgKill or nil
  if not (xp and xpMax) or rested <= 0 then return drop("restedWindow") end
  local n = E.RestedFinish(xpMax - xp, rested, avgKill)
  if not n or n < 3 or n > 80 then return drop("restedWindow") end
  say("restedWindow", fmt("Tue %d monstres : le repos (XP ×2) suffit pour finir le niveau %d", n, level()), 2,
    { ttl = 180, cooldown = 1800, window = 120, sig = level() })
end

-- ===================================================================================== 7. objet à garder (marchand)
local function questItems(ids)
  local need = {}
  for _, id in ipairs(ids) do
    local objs = qField(id, "objectives")
    local items = type(objs) == "table" and objs[3]
    if type(items) == "table" then
      for _, it in ipairs(items) do
        local iid = type(it) == "table" and tonumber(it[1]) or tonumber(it)
        if iid then need[iid] = need[iid] or id end
      end
    end
  end
  return need
end
function M.CheckKeep()
  if not opt("keepItem") then return drop("keepItem") end
  local ids = {}
  for _, e in ipairs(questsHere()) do ids[#ids + 1] = e.id end
  if #ids == 0 or not API.IterBags then return drop("keepItem") end
  local need = questItems(ids)
  for _, _, it in API.IterBags() do
    local qid = it.itemID and need[it.itemID]
    if qid and not it.noValue then
      local item = nm(API.ItemName and API.ItemName(it.itemID), 26)
      local qt = nm(qField(qid, "name"), 30)
      if item and qt then
        return say("keepItem", fmt("Garde « %s » : demandé par « %s » (quête dispo ici)", item, qt), 2, { ttl = 90, cooldown = 300 })
      end
    end
  end
  drop("keepItem")
end

-- ===================================================================================== 8. PNJ proche avec une quête
local giverPos = {}   -- [questID] = { mapID, x, y, name } | false
local function giverOf(qid, mapID)
  local c = giverPos[qid]
  if c ~= nil and (c == false or c.mapID == mapID) then return c or nil end
  giverPos[qid] = false
  local sb = qField(qid, "startedBy")
  local npc = type(sb) == "table" and type(sb[1]) == "table" and tonumber(sb[1][1])
  local db = npcDB()
  if not (npc and db) then return nil end
  local spawns = db.N(npc, "spawns")
  local C = T.modules and T.modules.Compass
  if type(spawns) ~= "table" then return nil end
  for area, list in pairs(spawns) do
    local m = type(area) == "number" and (db.map(area) or (C and C.AreaToMap and try(C.AreaToMap, area, nil, mapID)))
    if m == mapID and type(list) == "table" and type(list[1]) == "table" then
      local x, y = tonumber(list[1][1]), tonumber(list[1][2])
      if x and y then
        giverPos[qid] = { mapID = mapID, x = x / 100, y = y / 100, name = nm(db.N(npc, "name"), 24) }
        return giverPos[qid]
      end
    end
  end
  return nil
end
function M.CheckNpcQuest()
  if not opt("npcQuest") or inCombat() or inInstance() then return drop("npcQuest") end
  local p = me()
  if not p then return drop("npcQuest") end
  local best, bd
  for i, e in ipairs(questsHere()) do
    if i > 8 then break end
    if e.id and not inLog(e.id) then
      local gp = giverOf(e.id, p.mapID)
      local d = gp and distYd(p, gp)
      if d and d <= 90 and gp.name and (not bd or d < bd) then best, bd = { e = e, gp = gp }, d end
    end
  end
  if not best then return drop("npcQuest") end
  local title = nm(best.e.title or qField(best.e.id, "name"), 30)
  if not title then return drop("npcQuest") end
  say("npcQuest", fmt("Parle à %s (%d m) : « %s » est pour toi", best.gp.name, max(10, meters(bd)), title), 2,
    { ttl = 20, cooldown = 30, window = 120, sig = best.e.id, onClick = compassTo(best.gp.mapID, best.gp.x, best.gp.y, best.gp.name) })
end

-- ===================================================================================== 9. quête de liaison inutile
function M.CheckBreadcrumb()
  if not opt("chainBreadcrumb") then return drop("chainBreadcrumb") end
  for _, q in ipairs(quests()) do
    local target = tonumber(qField(q.id, "breadcrumbForQuestId"))
    if target and target > 0 and not q.complete and questDone(target) then
      local a, b = nm(q.title, 28), nm(qField(target, "name"), 28)
      if a and b then
        local Q = mod("Quests")
        return say("chainBreadcrumb", fmt("Abandonne « %s » : elle mène à « %s », déjà faite", a, b), 1,
          { ttl = 300, cooldown = 1800, window = 300, sig = q.id, tooltip = "Clic : ouvre la quête (l'abandon reste manuel).",
            onClick = function() if Q and Q.OpenQuest then try(Q.OpenQuest, q) end end })
      end
    end
  end
  drop("chainBreadcrumb")
end

-- ===================================================================================== 10–11. marchand
local function merchantItems()
  local out = {}
  local n = n1(try(g("GetMerchantNumItems"))) or 0
  for i = 1, min(n, 60) do
    local name, _, price, qty, avail = try(g("GetMerchantItemInfo"), i)
    local id = n1(try(g("GetMerchantItemID"), i)) or itemID(try(g("GetMerchantItemLink"), i))
    if id and tonumber(price) then
      out[#out + 1] = { id = id, name = name, price = tonumber(price), qty = max(1, tonumber(qty) or 1), avail = tonumber(avail) or -1 }
    end
  end
  return out
end
M.MerchantItems = merchantItems
local function consumableSet(kind, L)
  local set = {}
  local C = T.Data and T.Data.Consumables
  for _, e in ipairs((C and C[kind]) or {}) do
    if type(e) == "table" and (tonumber(e[2]) or 1) <= L then set[e[1]] = tonumber(e[3]) or 0 end
  end
  return set
end
function M.CheckFood(items)
  if not opt("foodBuy") then return drop("foodBuy") end
  local P = mod("Predict")
  local r = P and P.Rates and try(P.Rates)
  if type(r) ~= "table" or not r.restsPerHour or (r.rests or 0) < 2 then return drop("foodBuy") end
  local haveF, haveW = 0, 0
  if P.FoodCount then haveF, haveW = try(P.FoodCount) end
  local L = level()
  for _, kind in ipairs({ "food", "water" }) do
    local per = (kind == "food" and r.foodPerRest or r.waterPerRest)
    local amount = per and E.BuyForHour(per * r.restsPerHour, kind == "food" and haveF or haveW)
    if amount then
      local set, best = consumableSet(kind, L), nil
      for _, it in ipairs(items) do
        local v = set[it.id]
        if v and (not best or v > best.v) then best = { it = it, v = v } end
      end
      local name = best and nm(best.it.name or (API.ItemName and API.ItemName(best.it.id)), 28)
      if name then
        return say("foodBuy", fmt("Achète %d × %s : 1 h de jeu (tu en as %d)", amount, name,
          kind == "food" and (haveF or 0) or (haveW or 0)), 2, { ttl = 120, cooldown = 300 })
      end
    end
  end
  drop("foodBuy")
end
function M.CheckFlip(items)
  if not opt("vendorFlip") then return drop("vendorFlip") end
  local A = T.modules and T.modules.Auction
  local best
  for _, it in ipairs(items) do
    local market = auctionMarket(it.id, 0.35)
    if market then
      local unit = it.price / it.qty
      local vendorSell = tonumber((select(11, API.ItemInfo and API.ItemInfo(it.id)))) or 0
      local dep = (A and A.F and A.F.deposit and try(A.F.deposit, vendorSell, 1, 24)) or floor(vendorSell * 0.05)
      local net = E.VendorFlip(unit, market, dep)
      if net and (not best or net > best.net) then best = { it = it, net = net, market = market, unit = unit } end
    end
  end
  local name = best and nm(best.it.name or (API.ItemName and API.ItemName(best.it.id)), 26)
  if not name then return drop("vendorFlip") end
  local n = best.it.avail > 0 and best.it.avail or 1
  say("vendorFlip", fmt("Achète %d × %s ici (%s) : se revend ~%s à l'HV", n, name, goldText(best.unit), goldText(best.market)), 2,
    { ttl = 120, cooldown = 300, tooltip = fmt("Gain net estimé : %s par unité (commission et dépôt déduits).", goldText(best.net)) })
end

-- ===================================================================================== 12. butin lié quand équipé
function M.OnLoot(msg)
  if not opt("boeWorth") or type(msg) ~= "string" or secret(msg) then return end
  local mine = msg:find("^Vous recevez le butin") or msg:find("^You receive loot") or msg:find("^Vous recevez")
  if not mine then return end
  local id = itemID(msg)
  if not id then return end
  local info = { try(API.ItemInfo, id) }
  local name, quality, sell, bind = info[1], tonumber(info[3]) or 0, tonumber(info[11]) or 0, tonumber(info[14])
  if quality < 2 or bind == 1 or bind == 4 then return end   -- lié quand ramassé / quête : jamais à l'HV
  local market = auctionMarket(id, 0.25)
  if not market or not E.BoeWorth(market, sell) then return end
  name = nm(name, 30)
  if not name then return end
  say("boeWorth", fmt("Vends « %s » à l'HV (~%s), pas au marchand (%s)", name, goldText(market), goldText(sell)), 2,
    { ttl = 180, cooldown = 0 })
end

-- ===================================================================================== 13. donjon : tout le monde a la quête
local function unitOnQuest(u, qid)
  local CQ = g("C_QuestLog")
  if CQ and CQ.IsUnitOnQuest then return try(CQ.IsUnitOnQuest, qid, u) or try(CQ.IsUnitOnQuest, u, qid) end
  local Q = T.modules and T.modules.Quests
  if Q and Q.FriendsWith then
    local _, names = try(Q.FriendsWith, qid)
    local un = sv(try(g("UnitName"), u))
    for _, x in ipairs(type(names) == "table" and names or {}) do if un and tostring(x):find(un, 1, true) then return true end end
    return nil
  end
  return nil
end
function M.CheckDungeon()
  local n = groupSize()
  if not opt("dungeonReady") or inInstance() or inCombat() or n < 3 or n > 5 then return drop("dungeonReady") end
  for _, q in ipairs(quests()) do
    if q.dungeon and not q.complete then
      local have = 1
      for i = 1, n - 1 do if unitOnQuest("party" .. i, q.id) then have = have + 1 end end
      local dz, title = nm(q.zone, 24), nm(q.title, 28)
      if have == n and dz and title then
        return say("dungeonReady", fmt("Lancez %s : %d/%d ont « %s »", dz, have, n, title), 2,
          { ttl = 60, cooldown = 900, window = 120, sig = q.id .. ":" .. n })
      end
    end
  end
  drop("dungeonReady")
end

-- ===================================================================================== 14. maître des familiers
function M.CheckPet()
  if not opt("petTrainer") or playerClass() ~= "HUNTER" or inCombat() then return drop("petTrainer") end
  if not try(g("HasPetUI")) then return drop("petTrainer") end
  -- (COHERE) points LIBRES (total − utilisés), jamais le total brut ; nil = lecture pas fiable → silence
  local tp = tonumber(sv(API.PetFreeTrainingPoints and API.PetFreeTrainingPoints()))
  if tp and tp >= 30 then
    return say("petTrainer", fmt("Dépense %d points de dressage : sort « Dressage des bêtes »", tp), 2,
      { ttl = 120, cooldown = 1800, window = 180, sig = level() })
  end
  local rank, at = E.GrowlRank(level())
  local ack = M.char and M.char.ack or {}
  if rank and (tonumber(ack.growl) or 0) < at and inCapital() then
    return say("petTrainer", fmt("Va au maître des familiers : Grondement rang %d (niv %d)", rank, at), 2,
      { ttl = 120, cooldown = 1800, window = 300, sig = at, tooltip = "Clic : c'est fait (caché jusqu'au prochain rang).",
        onClick = function() if M.char then M.char.ack = M.char.ack or {} M.char.ack.growl = at end drop("petTrainer") end })
  end
  drop("petTrainer")
end

-- ===================================================================================== 15. maître d'armes
function M.CheckWeapons()
  if not opt("weaponTrainer") or level() < 10 or not inCapital() or inCombat() then return drop("weaponTrainer") end
  local miss = E.MissingWeapons(playerClass(), skillNames())
  local ack = M.char and M.char.ack or {}
  if not miss or #miss == 0 or (tonumber(ack.weapons) or 0) >= level() then return drop("weaponTrainer") end
  local more = #miss > 1 and fmt(" (+%d autre%s)", #miss - 1, #miss > 2 and "s" or "") or ""
  say("weaponTrainer", fmt("Apprends %s%s chez le maître d'armes : 10 pa chacune", miss[1], more), 2,
    { ttl = 120, cooldown = 3600, window = 300, sig = level(), tooltip = "Plus de choix de butin. Clic : cacher jusqu'au prochain niveau.",
      onClick = function() if M.char then M.char.ack = M.char.ack or {} M.char.ack.weapons = level() end drop("weaponTrainer") end })
end

-- ===================================================================================== 16. suivi de récolte
local TRACK = {
  { skills = { "herboristerie", "herbalism" }, label = "Découverte d'herbes", tex = 133939, keys = { "herb", "herbe" } },
  { skills = { "minage", "mining" }, label = "Découverte de gisements", tex = 136025, keys = { "miner", "gisement", "filon" } },
}
local function trackingState()
  local MM = g("C_Minimap")
  if MM and MM.GetNumTrackingTypes and MM.GetTrackingInfo then
    local list, any = {}, false
    for i = 1, min(n1(try(MM.GetNumTrackingTypes)) or 0, 40) do
      local a, b, c = try(MM.GetTrackingInfo, i)
      local name, tex, active
      if type(a) == "table" then name, tex, active = a.name, a.texture, a.active else name, tex, active = a, b, c end
      list[#list + 1] = { i = i, name = low(name), tex = tex, active = active and true or false }
      if active then any = true end
    end
    return list, any
  end
  local tex = try(g("GetTrackingTexture"))
  if tex == nil then return nil end
  return { { name = "", tex = tex, active = true } }, true
end
function M.CheckTracking()
  if not opt("gatherTrack") or inCombat() or inInstance() or resting() then return drop("gatherTrack") end
  local known = {}
  for _, n in ipairs(skillNames()) do known[low(n)] = true end
  local list, any = trackingState()
  if list == nil or any then return drop("gatherTrack") end
  for _, tr in ipairs(TRACK) do
    if known[tr.skills[1]] or known[tr.skills[2]] then
      local idx
      for _, e in ipairs(list) do
        if e.tex == tr.tex then idx = e.i end
        for _, k in ipairs(tr.keys) do if e.name:find(k, 1, true) then idx = idx or e.i end end
      end
      local MM = g("C_Minimap")
      return say("gatherTrack", fmt("Active « %s » (mini-carte) : les nœuds s'affichent", tr.label), 2,
        { ttl = 120, cooldown = 1800, window = 120, sig = zone(), onClick = (idx and MM and MM.SetTracking) and function()
          T:UserAction(function() MM.SetTracking(idx, true) end)
          drop("gatherTrack")
        end or nil })
    end
  end
  drop("gatherTrack")
end

-- ===================================================================================== 17–20. or (marchand / HV)
function M.CheckTrainerSave()
  if not opt("trainerSave") then return drop("trainerSave") end
  local P = T.modules and T.modules.Predict
  local L = level()
  local nx, cost = nil, nil
  if P and P.NextTraining then nx, cost = P.NextTraining(L) end
  local m = money()
  if not (nx and cost) or nx - L > 2 or m >= cost then return drop("trainerSave") end
  say("trainerSave", fmt("Garde ton or : maître de classe au niv %d (~%s), tu as %s", nx, goldText(cost), goldText(m)), 2,
    { ttl = 90, cooldown = 900 })
end
function M.CheckMount()
  local L = level()
  local goal = (tonumber(M.db and M.db.mountGoal) or 90) * 10000
  local m = money()
  if not opt("mountSave") or L < 30 or L >= 40 or m >= goal then return drop("mountSave") end
  local P = T.modules and T.modules.Predict
  local r = P and P.Rates and try(P.Rates)
  local gph = type(r) == "table" and tonumber(r.goldPerHour) or nil
  local tail = (gph and gph > 0) and fmt(", ~%s à ce rythme", durText((goal - m) / gph * 3600)) or ""
  say("mountSave", fmt("Garde %s sur %s : monture au niv 40%s", goldText(m), goldText(goal), tail), 2,
    { ttl = 90, cooldown = 1800 })
end
function M.CheckCheapGear()
  if not opt("cheapGear") then return drop("cheapGear") end
  local Gm = mod("Gear")
  local plan = Gm and Gm.plan
  local m = money()
  for _, e in ipairs((type(plan) == "table" and plan.shopping) or {}) do
    if e.kind == "ah" and tonumber(e.cost) and e.cost <= m and (tonumber(e.pct) or 0) >= 5 then
      local name = nm(e.name or (API.ItemName and API.ItemName(e.id)), 28)
      if name then
        return say("cheapGear", fmt("Achète « %s » à l'HV (~%s) : +%d %% pour toi", name, goldText(e.cost), floor(e.pct + 0.5)), 2,
          { ttl = 120, cooldown = 600 })
      end
    end
  end
  drop("cheapGear")
end
function M.CheckDualSpec()
  local L = level()
  local groups = n1(try(g("GetNumTalentGroups")))
  local ack = M.char and M.char.ack or {}
  if not opt("dualSpec") or L < 40 or groups ~= 1 or ack.dual or not inCapital() or money() < 500000 then return drop("dualSpec") end
  say("dualSpec", "Achète la double spé chez ton maître de classe (50 po) : tu as l'or", 2,
    { ttl = 120, cooldown = 3600, window = 300, sig = level(), tooltip = "Clic : c'est fait / pas intéressé (caché).",
      onClick = function() if M.char then M.char.ack = M.char.ack or {} M.char.ack.dual = true end drop("dualSpec") end })
end

-- ===================================================================================== 21. ami en danger
local lastHP = -1e9
function M.CheckFriends()
  local n = groupSize()
  if not opt("friendDanger") or inInstance() or n < 2 or try(g("IsInRaid")) then return drop("friendDanger") end
  local worst, wp
  for i = 1, min(4, n - 1) do
    local u = "party" .. i
    if try(g("UnitExists"), u) and try(g("UnitIsConnected"), u) and not try(g("UnitIsDeadOrGhost"), u) then
      local hp, mx = tonumber(sv(try(g("UnitHealth"), u))), tonumber(sv(try(g("UnitHealthMax"), u)))
      local near = sv(try(g("CheckInteractDistance"), u, 4))
      if near == nil then near = sv(try(g("UnitInRange"), u)) end
      if hp and mx and mx > 0 and near then
        local p = hp / mx
        if p > 0 and p < 0.35 and (not wp or p < wp) then worst, wp = u, p end
      end
    end
  end
  local name = worst and nm(try(g("UnitName"), worst), 16)
  if not name then return drop("friendDanger") end
  say("friendDanger", fmt("Aide %s : %d %% de vie, juste à côté de toi", name, floor(wp * 100 + 0.5)), 2,
    { ttl = 6, cooldown = 20, kind = "always" })
end

-- ===================================================================================== 22. escorte
function M.CheckEscort()
  if not opt("escortReady") or inCombat() then return drop("escortReady") end
  local Q = mod("Quests")
  for _, q in ipairs(quests()) do
    if E.IsEscort(q) and not q.complete then
      local title = nm(q.title, 30)
      if title and q.failed then
        return say("escortReady", fmt("Abandonne puis reprends « %s » : l'escorte a échoué", title), 2,
          { ttl = 300, cooldown = 600, tooltip = "Clic : ouvre la quête (l'abandon reste manuel).",
            onClick = function() if Q and Q.OpenQuest then try(Q.OpenQuest, q) end end })
      end
      local hp, mx = tonumber(sv(try(g("UnitHealth"), "player"))), tonumber(sv(try(g("UnitHealthMax"), "player")))
      if title and q.dist and q.dist <= 200 and hp and mx and mx > 0 and hp / mx < 0.9 then
        return say("escortReady", fmt("Remonte ta vie avant l'escorte « %s » : elle échoue si tu meurs", title), 2,
          { ttl = 30, cooldown = 120, window = 90, sig = q.id })
      end
    end
  end
  drop("escortReady")
end

-- ===================================================================================== 23. buff mondial
function M.OnYell(msg, speaker)
  if not opt("worldBuff") or secret(msg) then return end
  local buff = E.WorldBuff(msg, speaker)
  if not buff then return end
  local z = low(zone())
  if buff == E.WORLD_BUFFS[4][2] then
    if not (z:find("strangleronce", 1, true) or z:find("stranglethorn", 1, true)) then return end
  elseif not inCapital() then return end
  say("worldBuff", fmt("Reste là 20 s : buff « %s » dans un instant", buff), 2, { ttl = 25, cooldown = 60 })
end

-- ===================================================================================== 24. prochain point Legacy
function M.CheckLegacy()
  if not opt("legacyNext") then return drop("legacyNext") end
  local P = T.modules and T.modules.Prof
  local D = T.Data and T.Data.Recipes
  if not (P and P.ScanProfessions and P.Engine and P.Engine.Legacy and D and D.lines) then return drop("legacyNext") end
  local profs = try(P.ScanProfessions) or {}
  local by, kinds = {}, {}
  for line, v in pairs(D.lines) do kinds[line] = type(v) == "table" and v[1] or nil end
  for _, pr in ipairs(profs) do by[pr.line] = pr.rank end
  local _, nl, ns, gap = P.Engine.Legacy(by, kinds)
  local v = nl and D.lines[nl]
  local pname = type(v) == "table" and nm(v[2], 20)
  if not (pname and gap and gap > 0 and gap <= 10) then return drop("legacyNext") end
  say("legacyNext", fmt("Monte %s de %d point%s : +1 point Legacy à %d", pname, gap, gap > 1 and "s" or "", ns), 2,
    { ttl = 300, cooldown = 3600, window = 300, sig = nl .. ":" .. ns })
end

-- ===================================================================================== 25. camp Forever
local CAMP = { [171] = "Puits de mana", [164] = "Meule", [333] = "Luth enchanté", [202] = "Robot à compos", [165] = "Tente",
  [197] = "Bannière de faction", [182] = "Bougie d'encens", [186] = "Pierre d'aimant", [393] = "Chaise de camp",
  [185] = "Feu de camp", [129] = "Trousse de soins", [356] = "Bocal" }
local anchor = nil   -- { mapID, x, y, t }
function M.CheckCamp()
  if not opt("campBuff") or inCombat() or inInstance() or resting() then anchor = nil return drop("campBuff") end
  local p = me()
  if not p then return drop("campBuff") end
  local t = now()
  if not anchor or anchor.mapID ~= p.mapID or (distYd(p, anchor) or 999) > 60 then
    anchor = { mapID = p.mapID, x = p.x, y = p.y, t = t }
    return
  end
  if t - anchor.t < 180 then return end
  local recent = 0
  for _, list in pairs(kills) do for _, k in ipairs(list) do if t - k.t <= 180 then recent = recent + 1 end end end
  if recent < 3 then return end
  local P = T.modules and T.modules.Prof
  local object
  for _, pr in ipairs((P and P.ScanProfessions and try(P.ScanProfessions)) or {}) do
    if CAMP[pr.line] and (tonumber(pr.rank) or 0) >= 20 then object = CAMP[pr.line] break end
  end
  if not object then return end
  say("campBuff", fmt("Pose ton camp (%s) : buff gratuit pendant que tu farmes ici", object), 2,
    { ttl = 60, cooldown = 3600, window = 60, sig = floor(t / 3600) })
end

-- ===================================================================================== boucle + évènements
local tick = 0
local function run(fn, ...) local ok = pcall(fn, ...) return ok end
function M:AutoRefresh()
  tick = tick + 1
  run(M.CheckTag)
  run(M.CheckFriends)
  if tick % 5 == 1 then
    run(M.CheckRepop) run(M.CheckRested) run(M.CheckEscort) run(M.CheckTracking) run(M.CheckNpcQuest)
    run(M.CheckDungeon) run(M.CheckPet) run(M.CheckWeapons) run(M.CheckDualSpec) run(M.CheckCamp)
  end
  if tick % 20 == 2 then
    run(M.CheckEliteGuild) run(M.CheckBreadcrumb) run(M.CheckLegacy)
  end
  if tick % 5 == 3 or grindJob then run(M.CheckGrind) end
end

function M:OnMerchant()
  if inCombat() then return end
  local items = merchantItems()
  run(M.CheckFood, items) run(M.CheckFlip, items) run(M.CheckKeep) run(M.CheckTrainerSave) run(M.CheckMount)
end
function M:OnAuction()
  run(M.CheckCheapGear) run(M.CheckTrainerSave) run(M.CheckMount)
end
local function merchantClosed()
  for _, k in ipairs({ "foodBuy", "vendorFlip", "keepItem", "trainerSave", "mountSave", "cheapGear" }) do drop(k) end
end

local function parseKill(msg)
  if type(msg) ~= "string" or secret(msg) then return nil end
  local P = T.modules and T.modules.Predict
  if P and P.ParseKillXP then return P.ParseKillXP(msg) end
  return msg:match("^(.-) meurt, vous gagnez") or msg:match("^(.-) dies, you gain")
end

function M:OnEnable()
  self:RegisterEvent("MERCHANT_SHOW", function() M:OnMerchant() end)
  self:RegisterEvent("MERCHANT_CLOSED", merchantClosed)
  self:RegisterEvent("AUCTION_HOUSE_SHOW", function() M:OnAuction() end)
  self:RegisterEvent("AUCTION_HOUSE_CLOSED", merchantClosed)
  self:RegisterEvent("CHAT_MSG_LOOT", function(_, _, msg) M.OnLoot(msg) end)
  self:RegisterEvent("CHAT_MSG_COMBAT_XP_GAIN", function(_, _, msg)
    local name = parseKill(msg)
    if name then M.RecordKill(name) end
  end)
  self:RegisterEvent("PLAYER_TARGET_CHANGED", function() run(unitSeen, "target") run(M.CheckTag) end)
  self:RegisterEvent("NAME_PLATE_UNIT_ADDED", function(_, _, u) run(unitSeen, u) end)
  self:RegisterEvent("NAME_PLATE_UNIT_REMOVED", function(_, _, u)
    local guid = sv(try(g("UnitGUID"), u))
    if guid then M.PlateRemoved(guid) end
  end)
  self:RegisterEvent("CHAT_MSG_MONSTER_YELL", function(_, _, msg, speaker) M.OnYell(msg, speaker) end)
  self:RegisterEvent("UNIT_HEALTH", function(_, _, u)
    if type(u) == "string" and u:find("^party") and now() - lastHP > 1.5 then lastHP = now() run(M.CheckFriends) end
  end)
  self:RegisterEvent("TRAINER_SHOW", function()
    if playerClass() == "HUNTER" and M.char then
      local _, at = E.GrowlRank(level())
      if at then M.char.ack = M.char.ack or {} M.char.ack.growl = at end
    end
  end)
end
function M:OnDisable() grindJob = nil end
function M.ResetCaches() rosterAt, rosterAsk, roster = -1e9, -1e9, {} giverPos = {} grindJob, grindMap, grindPick = nil, nil, nil end

function M:OnOptionChanged(key, v) if v == false and key then drop(key) end end

-- /tai avantages : état de chaque fonction
if T.RegisterSlash then
  T:RegisterSlash("avance", function()
    local on = 0
    for _, f in ipairs(FEATURES) do if opt(f[1]) then on = on + 1 end end
    M:Print(fmt("%d/%d coups d'avance actifs (options > Coups d'avance). Lignes montrées cette session :", on, #FEATURES))
    for _, f in ipairs(FEATURES) do
      M:Print(fmt("  %s %s · %d", opt(f[1]) and "[x]" or "[ ]", f[2], (M.shown and M.shown[f[1]]) or 0))
    end
  end, "avance : les 25 coups d'avance (repop appris, coin de farm, HV…) et leur état")
end
