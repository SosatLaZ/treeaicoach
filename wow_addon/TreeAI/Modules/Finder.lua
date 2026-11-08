-- TreeAI — Chercher (agent FINDER) : « le truc qui change tout » près de toi, classé par un gain transparent.
--  * « Meilleur familier maintenant » (chasseur) : bêtes apprivoisables à ton niveau (Data/Pets.lua = familles,
--    techniques et modificateurs des tables client Forever + bêtes Classic APPROXIMATIVES revérifiées dans QuestieDB
--    en lecture seule + bêtes que tu cibles en jeu), zones sans gardes ennemis, trajet du modèle de Route (vols,
--    bateaux, zeppelins, foyer). Gain = rangs de technique gagnés (Morsure, Griffe, Hurlement furieux…) + famille
--    (dégâts / armure / PV de l'époque, comparés à ton familier) + vitesse d'attaque + niveau − trajet.
--  * Recherche générique (onglet « Chercher » du menu Plus, fenêtre, /tai cherche <mot>) : familier, rare / élite,
--    amélioration d'équipement (plan de Gear), maître de classe / d'armes, point de vol non appris, marchand
--    (munitions, boissons), trésor (Detect), quête clé (Pro), donjon pour le groupe (Data/Dungeons).
--  * Chaque résultat : trajet, pourquoi EN MOTS (jamais un score), « Ajouter au trajet » (arrêt inséré dans le trajet
--    du Planner → Route le montre et le renvoie à Shortest Path Forever s'il le suit) et « Y aller » (Compass.SetTarget).
--  * « Maintenant » : au plus UNE ligne, seulement si un truc qui change tout est à ≤ N min (« Apprivoise X à
--    300 m : Morsure 3 ») ET sûr : une bête VUE par toi en jeu ou placée par QuestieDB (jamais une position de la
--    liste Classic approximative). Seul propriétaire d'« apprivoiser » (F.OwnsPet) ; le Chasseur renvoie ici.
-- Rien d'automatique : tout sur clic, calculs hors combat et à la demande (petit relevé toutes les 30 s hors combat).
-- Aucune fonction protégée, aucun cadre de Blizzard touché. Lua 5.1, aucune globale.
local T = _G.TreeAI
if not (T and T.RegisterModule) then return end

local F = T:RegisterModule("Finder", {
  title = "Chercher",
  defaults = { enabled = true, pets = true, now = true, nearMin = 5, learn = true, seen = {} },
  charDefaults = { stops = {}, sig = 0 },
  options = {
    { key = "pets", type = "toggle", label = "Chasseur : meilleur familier à apprivoiser maintenant (remplace les autres conseils d'apprivoisement)", section = "Chercher" },
    { key = "now", type = "toggle", label = "Une ligne dans « Maintenant » quand un truc qui change tout est tout près", section = "Chercher" },
    { key = "nearMin", type = "range", label = "« Tout près » = à moins de (minutes de trajet)", min = 1, max = 15, step = 1, section = "Chercher" },
    { key = "learn", type = "toggle", label = "Retenir les bêtes que je cible (position, famille, niveau)", section = "Chercher" },
    { key = "open", type = "button", label = "Ouvrir « Chercher autour de moi »", section = "Chercher",
      onClick = function() local M = T.Finder if M and M.Show then M.Show(true) end end },
  },
})
T.Finder = F
-- profil « essentiel » (LITE) : léger, sur événement ; onglet propre ; rangé avec les quêtes et le trajet (TIDY)
if type(T.MODULE_INFO) == "table" and not T.MODULE_INFO.Finder then
  T.MODULE_INFO.Finder = { lite = true, desc = "Chercher : meilleur familier, rares, équipement, maîtres, vols, trésors" }
end
if type(T.MODULE_TAB) == "table" then T.MODULE_TAB.Finder = "finder" end
do
  local fam = T.OPTIONS_FAMILY and T.OPTIONS_FAMILY[1]
  if type(fam) == "table" and type(fam.mods) == "table" then
    local has = false
    for _, n in ipairs(fam.mods) do if n == "Finder" then has = true end end
    if not has then table.insert(fam.mods, 5, "Finder") end
  end
end

local floor, max, min, abs, sqrt, huge = math.floor, math.max, math.min, math.abs, math.sqrt, math.huge
local tsort, tconcat, fmt = table.sort, table.concat, string.format
local TAB = "finder"

local function pass(ok, ...) if ok then return ... end return nil end
local function try(fn, ...) if type(fn) ~= "function" then return nil end return pass(pcall(fn, ...)) end
local function G(n) return rawget(_G, n) end
local function C(ns, fn) local t = G(ns) return type(t) == "table" and t[fn] or nil end
local function now() return try(G("GetTime")) or 0 end
local function inCombat() return T.InCombat and T:InCombat() or false end
local function secret(v) local f = G("issecretvalue") return f and try(f, v) and true or false end
local function pub(v) if secret(v) then return nil end return v end
local cut = T.Cut   -- (4.0) Core : n caractères UTF-8 (« … » compris), jamais une lettre accentuée coupée
local function low(s) return tostring(s or ""):lower() end
local function opt(k) return not (F.db and F.db[k] == false) end
local function mod(n) local m = T.modules and T.modules[n] return (type(m) == "table" and m.IsEnabled and m:IsEnabled()) and m or nil end
local function pets() return T.Data and T.Data.Pets or nil end
local function level() return tonumber(pub(try(G("UnitLevel"), "player"))) or 1 end
local function myClass() return T.PlayerClass and T:PlayerClass() or select(2, try(G("UnitClass"), "player")) end
local function faction()
  local f = try(G("UnitFactionGroup"), "player")
  return f == "Alliance" and "A" or f == "Horde" and "H" or nil
end
local function num1(v) return (fmt("%.1f", v):gsub("%.", ",")) end

-- ===================================================================== formats / trajet
local function minutes(sec)
  local R = T.Route
  if R and R.Minutes then return R.Minutes(sec) end
  sec = tonumber(sec) or 0
  return sec < 60 and fmt("%d s", floor(sec + 0.5)) or fmt("%d min", floor(sec / 60 + 0.5))
end
local function dist(yd)
  local R = T.Route
  if R and R.Km then return R.Km(yd) end
  return fmt("%d m", floor((tonumber(yd) or 0) * 0.9144 / 10 + 0.5) * 10)
end
local function me()
  local Geo = T.Geo
  local m, x, y
  if Geo and Geo.Player then m, x, y = Geo.Player() end
  if m and x then return { map = m, x = x, y = y } end
end
F.Me = me
-- p = { map, x, y } en % → secondes, yards (nil = inconnu : autre continent sans transport connu)
local function travel(from, p)
  if not (from and p and p.map and p.x) then return nil end
  local Geo = T.Geo
  local yd = Geo and Geo.Dist and Geo.Dist(from.map, from.x, from.y, p.map, p.x, p.y)
  local R = T.Route
  local s = R and R.IsEnabled and R:IsEnabled() and R.LegSeconds and try(R.LegSeconds, from, p)
  if not s and yd then s = yd * 1.3 / 7 end
  return s, yd
end
F.Travel = travel
local function pct(v) v = tonumber(v) if not v then return nil end return v <= 1 and v * 100 or v end

-- ===================================================================== familier : modèle de valeur (pur)
-- poids d'un rang gagné (points de « gain ») ; les techniques de dégâts d'abord, Hurlement furieux vaut plus en groupe
F.ABILITY_W = { ["Morsure"] = 8, ["Griffe"] = 8, ["Hurlement furieux"] = 7, ["Souffle de foudre"] = 6,
  ["Poison de scorpide"] = 4, ["Charge"] = 3, ["Célérité"] = 3, ["Plongeon"] = 3, ["Déchirure du tendon"] = 3,
  ["Hurlement démoralisant"] = 3, ["Pourfendeur sauvage"] = 4, ["Démembrer"] = 4, ["Balayage"] = 3, ["Rôder"] = 2 }
local MAIN = { "Morsure", "Griffe", "Hurlement furieux", "Souffle de foudre", "Poison de scorpide", "Charge" }
local DIET = { { 1, "viande" }, { 2, "poisson" }, { 4, "fromage" }, { 8, "pain" }, { 16, "champignon" }, { 32, "fruit" } }
function F.DietText(mask)
  mask = tonumber(mask) or 0
  local out = {}
  for _, d in ipairs(DIET) do if floor(mask / d[1]) % 2 == 1 then out[#out + 1] = d[2] end end
  return #out > 0 and tconcat(out, ", ") or "?"
end
function F.FamilyValue(f)
  if type(f) ~= "table" then return 0 end
  return (f.dmg or 0) * 1.0 + (f.hp or 0) * 0.4 + (f.armor or 0) * 0.3
end
--- rang probable d'une technique pour une bête de niveau lv (rang dont le niveau d'apprentissage ≤ lv)
function F.ProbableRank(D, ability, lv)
  local a = D and D.abilities and D.abilities[ability]
  local r = 0
  for i, l in ipairs(a and a.lv or {}) do if l > 0 and l <= lv then r = i end end
  return r
end
--- b = bête { n, fam, lo, hi, teach, atk, rare } ; ctx = { level, group, petFam, known = { [technique] = rang }, sec }
--- → gain, parts = { {pts, texte} }, info = { ability, rank, probable, atk, atkGuess, eff } ; nil = pas apprivoisable
function F.PetValue(D, b, ctx)
  local fams = D and D.families or {}
  local f = fams[b.fam]
  if not f or (b.lo or 99) > (ctx.level or 1) then return nil end
  local eff = min(b.hi or b.lo, ctx.level)
  local parts, gain = {}, 0
  local function add(p, txt) p = floor(p + 0.5) if p ~= 0 then parts[#parts + 1] = { p, txt } gain = gain + p end end
  -- technique enseignée : celle écrite à la main, sinon la principale de la famille au rang probable
  local ab, rank, probable = b.teach and b.teach[1], b.teach and b.teach[2], false
  if not ab then
    for _, name in ipairs(MAIN) do
      for _, l in ipairs(f.learn or {}) do
        if l == name and not ab then ab, rank, probable = name, F.ProbableRank(D, name, eff), true end
      end
    end
  end
  local info = { ability = ab, rank = rank, probable = probable, eff = eff }
  if ab and rank and rank > 0 then
    local have = (ctx.known or {})[ab] or 0
    local w = (F.ABILITY_W[ab] or 2) + ((ab == "Hurlement furieux" and (ctx.group or 1) >= 3) and 3 or 0)
    if rank > have then add((rank - have) * w, fmt("%s rang %d%s (%s)", ab, rank, probable and " probable" or "",
      have > 0 and ("ton familier : rang " .. have) or "ton familier ne l'a pas")) end
  end
  -- famille comparée à ton familier actuel (modificateurs de l'époque)
  local cur = fams[ctx.petFam or -1]
  local dv = F.FamilyValue(f) - F.FamilyValue(cur)
  if abs(dv) >= 1 then
    add(dv, fmt("%s : dégâts %+d %%, armure %+d %%, PV %+d %%%s", f.n, f.dmg or 0, f.armor or 0, f.hp or 0,
      cur and (" (vs " .. cur.n .. ")") or ""))
  end
  -- vitesse d'attaque (attaques rapides = plus de coups critiques / Focus pour Morsure-Griffe)
  local atk = tonumber(b.atk)
  info.atk, info.atkGuess = atk or 2.0, atk == nil
  if atk then add((2.0 - atk) * 12, fmt("attaque %s s", num1(atk))) end
  -- niveau : le familier garde le niveau de la bête
  if eff < ctx.level - 1 then add(-2 * (ctx.level - 1 - eff), fmt("niveau %d (toi %d) : à remonter", eff, ctx.level)) end
  if b.rare then add(3, "rare (unique, parfois absent)") end
  if ctx.sec then add(-min(30, ctx.sec / 60 * 0.8), minutes(ctx.sec) .. " de trajet")
  elseif ctx.far then add(-25, "loin (autre continent ou trajet inconnu)") end
  tsort(parts, function(a, c) return a[1] > c[1] end)
  return gain, parts, info
end

-- ===================================================================== familier : état du jeu
--- techniques connues par ton familier : { [nom] = rang } (lecture du grimoire du familier, gardée)
function F.PetKnown()
  local out = {}
  if not try(G("UnitExists"), "pet") then return out end
  local SB = G("C_SpellBook")
  local E = G("Enum")
  local bank = type(E) == "table" and type(E.SpellBookSpellBank) == "table" and E.SpellBookSpellBank.Pet
  local n = tonumber(try(G("HasPetSpells")) or try(C("C_SpellBook", "HasPetSpells"))) or 0
  for i = 1, min(n > 0 and n or 30, 40) do
    local name, sub
    if SB and SB.GetSpellBookItemName and bank then name, sub = try(SB.GetSpellBookItemName, i, bank) end
    if not name then name, sub = try(G("GetSpellBookItemName"), i, G("BOOKTYPE_PET") or "pet") end
    if not name then name, sub = try(G("GetSpellName"), i, G("BOOKTYPE_PET") or "pet") end
    if type(name) ~= "string" then if n == 0 then break end
    else
      local r = tonumber(type(sub) == "string" and sub:match("(%d+)") or nil) or 1
      if (out[name] or 0) < r then out[name] = r end
    end
  end
  return out
end
local function famId(name)
  local D = pets()
  if not (D and type(name) == "string") then return nil end
  for id, f in pairs(D.families or {}) do if f.n == name then return id end end
  local EN = { Wolf = 1, Cat = 2, Spider = 3, Bear = 4, Boar = 5, Crocolisk = 6, ["Carrion Bird"] = 7, Crab = 8,
    Gorilla = 9, Raptor = 11, Tallstrider = 12, Scorpid = 20, Turtle = 21, Bat = 24, Hyena = 25, Owl = 26,
    ["Bird of Prey"] = 26, ["Wind Serpent"] = 27, Fox = 319 }
  return EN[name]
end
F.FamilyId = famId
local function petCtx()
  local fam = pub(try(G("UnitCreatureFamily"), "pet"))
  local n = tonumber(try(G("GetNumGroupMembers"))) or 0
  return { level = level(), group = max(1, n), petFam = famId(fam), known = F.PetKnown() }
end

-- apparitions : QuestieDB (lecture seule, par id de PNJ, une fois par session) ; niveaux revérifiés
local spotCache = {}
function F.Spots(b, here)
  local c = spotCache[b.npc]
  if c == nil then
    c = { pts = {} }
    local Cm = T.Compass
    local db, zdb
    if Cm and Cm.Questie then db, zdb = Cm.Questie() end
    if db and type(db.QueryNPCSingle) == "function" and b.npc then
      local qmin = tonumber(try(db.QueryNPCSingle, b.npc, "minLevel"))
      local qmax = tonumber(try(db.QueryNPCSingle, b.npc, "maxLevel"))
      if qmin and b.lo and abs(qmin - b.lo) > 5 then c.bad = true          -- id curé faux : on l'écarte
      else
        if qmin then c.lo, c.hi = qmin, qmax or qmin end
        local out = {}
        if Cm.SpawnsOf then pcall(Cm.SpawnsOf, "npc", b.npc, db, zdb, here and here.map, out) end
        for _, p in ipairs(out) do c.pts[#c.pts + 1] = { map = p.mapID, x = p.x * 100, y = p.y * 100 } end
        c.questie = #c.pts > 0
      end
    end
    if not c.bad and #c.pts == 0 and b.map and b.x then c.pts[1] = { map = b.map, x = b.x, y = b.y, approx = true } end
    spotCache[b.npc or b.n] = c
  end
  return c
end
F._spots = spotCache

-- bêtes apprises en jeu (cible / survol, hors combat) : db.seen[npc] = { n, fam, lv, map, x, y, t }
local lastSeen = 0
function F.LearnUnit(unit)
  if not (opt("learn") and F.db) or inCombat() then return nil end
  if now() - lastSeen < 0.5 then return nil end
  local fam = pub(try(G("UnitCreatureFamily"), unit))
  if type(fam) ~= "string" or try(G("UnitPlayerControlled"), unit) then return nil end
  local fid = famId(fam)
  local D = pets()
  if not (fid and D and D.families[fid]) then return nil end
  local guid = pub(try(G("UnitGUID"), unit))
  local npc = type(guid) == "string" and tonumber(guid:match("^%a+%-%d+%-%d+%-%d+%-%d+%-(%d+)")) or nil
  local p = me()
  if not (npc and p) then return nil end
  lastSeen = now()
  F.db.seen = type(F.db.seen) == "table" and F.db.seen or {}
  local S = F.db.seen
  local lv = tonumber(pub(try(G("UnitLevel"), unit))) or 0
  local e = S[npc]
  if not e then
    local n = 0 for _ in pairs(S) do n = n + 1 end
    if n >= 60 then return nil end
    e = {} S[npc] = e
  end
  e.n, e.fam, e.map, e.x, e.y, e.t = cut(pub(try(G("UnitName"), unit)) or "?", 40), fid, p.map, p.x, p.y, try(G("time")) or 0
  e.lo = (e.lo and lv > 0) and min(e.lo, lv) or (lv > 0 and lv or e.lo)
  e.hi = max(e.hi or 0, lv)
  return e
end

--- classement des bêtes : { {b, gain, parts, info, sec, yd, spot} } (meilleures d'abord), n max
function F.RankPets(n, ctx)
  local D = pets()
  if not D then return {} end
  ctx = ctx or petCtx()
  local here = me()
  local fz = faction()
  local out = {}
  local c2 = {}   -- (CLEAN) un seul contexte réutilisé (PetValue ne le garde pas) : pas une table par bête
  for k, v in pairs(ctx) do c2[k] = v end
  local function consider(b, spots)
    if b.fz and b.fz ~= "" and fz and b.fz ~= fz then return end
    local best, bs, byd
    for _, p in ipairs(spots) do
      local s, yd = travel(here, p)
      if s and (not bs or s < bs) then best, bs, byd = p, s, yd end
    end
    best = best or spots[1]
    c2.sec, c2.far = bs, not bs
    local gain, parts, info = F.PetValue(D, b, c2)
    if gain then out[#out + 1] = { b = b, gain = gain, parts = parts, info = info, sec = bs, yd = byd, spot = best } end
  end
  local seenNpc = {}
  for npc, e in pairs(F.db and type(F.db.seen) == "table" and F.db.seen or {}) do
    if type(e) == "table" and e.fam and e.map and e.x and (e.lo or 0) > 0 then
      seenNpc[npc] = true
      consider({ npc = npc, n = e.n, fam = e.fam, lo = e.lo, hi = e.hi, seen = true },
        { { map = e.map, x = e.x, y = e.y } })
    end
  end
  for _, b in ipairs(D.beasts or {}) do
    if not seenNpc[b.npc] and (b.lo or 99) <= ctx.level and (b.hi or 0) >= ctx.level - 12 then
      local sp = F.Spots(b, here)
      if not sp.bad then
        local bb = b
        if sp.lo then bb = setmetatable({ lo = sp.lo, hi = sp.hi }, { __index = b }) end
        if #sp.pts > 0 then consider(bb, sp.pts) end
      end
    end
  end
  tsort(out, function(a, c) if a.gain ~= c.gain then return a.gain > c.gain end return (a.sec or huge) < (c.sec or huge) end)
  while #out > (n or 3) do out[#out] = nil end
  return out
end

-- ===================================================================== résultats génériques
-- r = { key, cat, title, text (ligne), why = { {pts, texte} }, score, sec, yd, map, x, y (%), stopKind, stopLabel,
--       act = { label, fn } (bouton spécial, ex. donjon), now (texte court pour « Maintenant ») }
local CATS = {
  { id = "pet", label = "Familier", kw = { "familier", "pet", "apprivois", "bete", "bête", "tame" } },
  { id = "rare", label = "Rare / élite", kw = { "rare", "elite", "élite", "butin" } },
  { id = "gear", label = "Équipement", kw = { "equip", "équip", "stuff", "gear", "arme", "armure", "amelior", "amélior" } },
  { id = "trainer", label = "Maîtres", kw = { "maitre", "maître", "entraineur", "entraîneur", "competence", "compétence", "arme" } },
  { id = "flight", label = "Vol", kw = { "vol", "fly", "griffon", "wyverne", "taxi" } },
  { id = "vendor", label = "Marchand", kw = { "marchand", "munition", "fleche", "flèche", "balle", "boisson", "eau", "vendor" } },
  { id = "treasure", label = "Trésor", kw = { "coffre", "tresor", "trésor", "chest" } },
  { id = "dungeon", label = "Donjon", kw = { "donjon", "instance", "groupe" } },
}
F.CATS = CATS
--- mots → catégories voulues (set) + reste du texte (filtre libre) ; vide = tout
function F.ParseQuery(q)
  q = low(q):gsub("^%s+", ""):gsub("%s+$", "")
  if q == "" or q == "tout" then return nil, nil end
  local set, any = {}, false
  for _, c in ipairs(CATS) do
    for _, k in ipairs(c.kw) do if q:find(k, 1, true) then set[c.id] = true any = true end end
  end
  return any and set or nil, (not any) and q or nil
end

local function result(cat, key, title, score, why, p, extra)
  local r = { cat = cat, key = cat .. ":" .. tostring(key), title = title, score = floor(score + 0.5), why = why or {} }
  if p then r.map, r.x, r.y, r.sec, r.yd = p.map, p.x, p.y, p.sec, p.yd end
  for k, v in pairs(extra or {}) do r[k] = v end
  return r
end
local function travelPart(why, sec)
  if sec then why[#why + 1] = { -floor(min(30, sec / 60 * 0.8) + 0.5), minutes(sec) .. " de trajet" } end
  return sec and -floor(min(30, sec / 60 * 0.8) + 0.5) or 0
end
local function placeOf(here, map, x, y)
  local p = { map = map, x = pct(x), y = pct(y) }
  p.sec, p.yd = travel(here, p)
  return p
end

local S = {}   -- sources par catégorie : function(here, ctx) → { résultats }
S.pet = function()
  if not (opt("pets") and myClass() == "HUNTER") then return {} end
  local out = {}
  for _, e in ipairs(F.RankPets(3)) do
    local b, i = e.b, e.info
    local fam = (pets().families[b.fam] or {}).n or "?"
    local lv = (b.lo == b.hi or not b.hi) and fmt("niv %d", b.lo) or fmt("niv %d–%d", b.lo, b.hi)
    local abTxt = i.ability and (i.rank or 0) > 0 and fmt("%s %d", i.ability, i.rank) or fam
    local head = fmt("%s : %s, attaque %s s%s", b.n, i.ability and (i.rank or 0) > 0 and fmt("%s rang %d", i.ability, i.rank) or fam,
      num1(i.atk), e.sec and (", " .. minutes(e.sec) .. " de trajet") or "")
    local tip = { head }
    if i.probable then tip[#tip + 1] = "Rang déduit de son niveau : vérifie avec « Connaissance des bêtes »." end
    tip[#tip + 1] = fmt("%s · régime : %s.", fam, F.DietText((pets().families[b.fam] or {}).diet))
    local sure = b.seen or (e.spot and not e.spot.approx)
    tip[#tip + 1] = b.seen and "Vue par toi à cet endroit." or (sure and "Position donnée par QuestieDB."
      or "Position de la liste Classic, sans QuestieDB : à vérifier sur place.")
    local p = e.spot and { map = e.spot.map, x = e.spot.x, y = e.spot.y, sec = e.sec, yd = e.yd }
    -- « Maintenant » : seulement une bête sûre (vue en jeu / QuestieDB), et le rang seulement s'il n'est pas déduit
    out[#out + 1] = result("pet", b.npc or b.n, fmt("Apprivoise %s (%s)", b.n, lv), e.gain, e.parts, p, {
      tip = tip, stopKind = "find", stopLabel = "Apprivoiser « " .. cut(b.n, 30) .. " »",
      now = sure and fmt("Apprivoise %s à %s : %s", cut(b.n, 28), e.yd and dist(e.yd) or minutes(e.sec or 0),
        i.probable and fam or abTxt) or nil })
  end
  return out
end
S.rare = function(here, ctx)
  local Dt = T.Detect
  if not (Dt and Dt.Rares) then return {} end
  local out = {}
  for _, e in ipairs(try(Dt.Rares, Dt, "rare") or {}) do
    if e.mapID and e.x and #out < 5 then
      local why = { { 20, "rare : bon butin, grosse XP" } }
      local sc = 20
      if e.kind == "rareelite" or e.kind == "worldboss" then
        if ctx.group >= 3 then why[#why + 1] = { 8, fmt("élite : vous êtes %d", ctx.group) } sc = sc + 8
        else why[#why + 1] = { -8, "élite : viens à plusieurs" } sc = sc - 8 end
      end
      if (tonumber(e.level) or 0) > ctx.level + 3 then why[#why + 1] = { -10, fmt("niveau %d", e.level) } sc = sc - 10 end
      local p = placeOf(here, e.mapID, e.x, e.y)
      sc = sc + travelPart(why, p.sec)
      out[#out + 1] = result("rare", e.key or e.name, fmt("Vise le rare %s%s", cut(e.name or "?", 30),
        (tonumber(e.level) or 0) > 0 and (" (niv " .. e.level .. ")") or ""), sc, why, p,
        { stopKind = "find", stopLabel = "Rare « " .. cut(e.name or "?", 30) .. " »", now = fmt("Vise le rare %s à %s", cut(e.name or "?", 28), p.yd and dist(p.yd) or "?") })
    end
  end
  return out
end
S.treasure = function(here)
  local Dt = T.Detect
  if not (Dt and Dt.Rares) then return {} end
  local out = {}
  for _, e in ipairs(try(Dt.Rares, Dt, "treasure") or {}) do
    if e.mapID and e.x and #out < 3 then
      local why = { { 12, "coffre : objets / or" } }
      local p = placeOf(here, e.mapID, e.x, e.y)
      local sc = 12 + travelPart(why, p.sec)
      out[#out + 1] = result("treasure", e.key or e.name, "Ouvre le trésor « " .. cut(e.name or "Trésor", 30) .. " »", sc, why, p,
        { stopKind = "find", stopLabel = "Trésor « " .. cut(e.name or "?", 30) .. " »", now = fmt("Ouvre le trésor à %s", p.yd and dist(p.yd) or "?") })
    end
  end
  return out
end
local function service(here, kind)
  local Dt = T.Detect
  local e = Dt and Dt.Nearest and try(Dt.Nearest, Dt, kind)
  if type(e) == "table" and e.mapID and e.x then return e, placeOf(here, e.mapID, e.x, e.y) end
end
local SLOT_AMMO = 0
S.vendor = function(here, ctx)
  local out = {}
  local API = T.API or {}
  if myClass() == "HUNTER" then
    local id = try(G("GetInventoryItemID"), "player", SLOT_AMMO)
    local n = id and tonumber(API.ItemCount and API.ItemCount(id) or try(G("GetItemCount"), id)) or 0
    -- même seuil que le module Chasseur (« Alerte munitions sous », 200 par défaut) : un seul avis sur les munitions
    local H = T.Hunter
    local warn = tonumber(H and type(H.db) == "table" and H.db.ammoWarn) or 200
    if n < warn then
      local e, p = service(here, "vendor")
      local sc = n < 50 and 30 or 15
      local why = { { sc, fmt("munitions : %d restantes", n) } }
      if e then sc = sc + travelPart(why, p.sec) end
      out[#out + 1] = result("vendor", "ammo", "Achète des munitions" .. (e and (" chez " .. cut(e.name or "un marchand", 26)) or " au prochain marchand"),
        sc, why, e and p, { stopKind = "vendor", stopLabel = "Marchand : munitions", now = e and p.yd and fmt("Achète des munitions à %s", dist(p.yd)) or nil })
    end
  end
  local W = T.Data and T.Data.Consumables and T.Data.Consumables.water
  if type(W) == "table" and ctx.level >= 5 and myClass() ~= "HUNTER" then   -- le chasseur ne s'arrête pas pour boire
    local have = 0
    for _, w in ipairs(W) do if (w[2] or 0) <= ctx.level then have = have + (tonumber(API.ItemCount and API.ItemCount(w[1])) or 0) end end
    if have == 0 then
      local e, p = service(here, "vendor")
      local why = { { 8, "aucune boisson dans les sacs" } }
      local sc = 8 + (e and travelPart(why, p.sec) or 0)
      out[#out + 1] = result("vendor", "drink", "Achète des boissons" .. (e and (" chez " .. cut(e.name or "un marchand", 26)) or ""), sc, why, e and p,
        { stopKind = "vendor", stopLabel = "Marchand : boissons" })
    end
  end
  return out
end
-- maîtres d'armes (Classic ≈ : positions et listes à vérifier en jeu)
F.WEAPON_MASTERS = {
  { f = "A", n = "Woo Ping", map = 1453, x = 57.1, y = 57.7, s = { "Épées à une main", "Épées à deux mains", "Bâtons", "Armes d'hast" } },
  { f = "A", n = "Bixi Wobblebonk", map = 1455, x = 62.2, y = 89.6, s = { "Arbalètes", "Dagues", "Armes de jet" } },
  { f = "A", n = "Buliwyf Stonehand", map = 1455, x = 61.2, y = 89.5, s = { "Armes à feu", "Armes de pugilat", "Masses à une main", "Masses à deux mains" } },
  { f = "A", n = "Ilyenia Moonfire", map = 1457, x = 57.6, y = 46.7, s = { "Arcs", "Dagues", "Bâtons", "Armes de jet" } },
  { f = "H", n = "Hanashi", map = 1454, x = 81.5, y = 19.6, s = { "Arcs", "Bâtons", "Haches à deux mains", "Armes de jet" } },
  { f = "H", n = "Sayoc", map = 1454, x = 81.2, y = 19.0, s = { "Dagues", "Armes de pugilat", "Haches à une main", "Haches à deux mains" } },
  { f = "H", n = "Archibald", map = 1458, x = 57.3, y = 32.8, s = { "Arbalètes", "Dagues", "Épées à une main", "Épées à deux mains", "Armes d'hast" } },
  { f = "H", n = "Ansekhwa", map = 1456, x = 40.9, y = 62.7, s = { "Armes à feu", "Bâtons", "Masses à une main", "Masses à deux mains" } },
}
F.CLASS_WEAPONS = { HUNTER = { "Arcs", "Arbalètes", "Armes à feu", "Dagues", "Armes de pugilat", "Haches à une main",
  "Haches à deux mains", "Épées à une main", "Épées à deux mains", "Armes d'hast", "Bâtons", "Armes de jet" } }
function F.KnownSkills()
  local out = {}
  local nf = C("C_SkillInfo", "GetNumSkillLines") or G("GetNumSkillLines")
  local gf = C("C_SkillInfo", "GetSkillLineInfo")
  local n = tonumber(try(nf)) or 0
  for i = 1, min(n, 80) do
    local name
    if gf then local t = try(gf, i) name = type(t) == "table" and t.name or nil end
    if not name then name = try(G("GetSkillLineInfo"), i) end
    if type(name) == "string" then out[name] = true end
  end
  return out, n
end
S.trainer = function(here, ctx)
  local out = {}
  local sug = T._suggestions or {}
  if sug["Errands:trainer"] then   -- (CLEAN) le rappel du maître de classe est chez Guichets (prix réel)
    local e, p = service(here, "trainer")
    local why = { { 25, "nouveaux sorts de classe à ton niveau" } }
    local sc = 25 + (e and travelPart(why, p.sec) or 0)
    out[#out + 1] = result("trainer", "class", "Va voir ton maître de classe" .. (e and (" : " .. cut(e.name or "", 24)) or ""), sc, why, e and p,
      { stopKind = "train", stopLabel = "Maître de classe", now = e and p.yd and fmt("Va voir ton maître de classe à %s", dist(p.yd)) or nil })
  end
  local want = F.CLASS_WEAPONS[myClass() or ""]
  local known, nSk = F.KnownSkills()
  local fz = faction()
  if want and nSk > 0 and fz then
    local best
    for _, m in ipairs(F.WEAPON_MASTERS) do
      if m.f == fz then
        local new = {}
        for _, s in ipairs(m.s) do
          for _, w in ipairs(want) do if w == s and not known[s] then new[#new + 1] = s end end
        end
        if #new > 0 then
          local p = placeOf(here, m.map, m.x, m.y)
          local why = { { 6 + 2 * min(3, #new), fmt("nouvelle%s compétence%s d'arme : %s (10 pa chacune)", #new > 1 and "s" or "", #new > 1 and "s" or "", tconcat(new, ", ")) } }
          local sc = why[1][1] + travelPart(why, p.sec)
          local r = result("trainer", "wm:" .. m.n, "Apprends " .. cut(tconcat(new, ", "), 40) .. " chez " .. m.n, sc, why, p,
            { stopKind = "train", stopLabel = "Maître d'armes " .. m.n, tip = { "Maître d'armes : position des données Classic." } })
          if not best or r.score > best.score then best = r end
        end
      end
    end
    if best then out[#out + 1] = best end
  end
  if myClass() == "HUNTER" then
    local e, p = service(here, "stable")
    if e and ctx.petTrain then
      out[#out + 1] = result("trainer", "pet", "Passe au maître des familiers", 10 + travelPart({}, p.sec), { { 10, "points d'entraînement à dépenser" } }, p,
        { stopKind = "train", stopLabel = "Maître des familiers" })
    end
  end
  return out
end
S.flight = function(here)
  local D = T.Data and T.Data.Travel
  local R = T.Route
  local Geo = T.Geo
  if not (D and R and R.KnownTaxi and here and Geo and Geo.World) then return {} end
  local known = try(R.KnownTaxi) or {}
  if #known == 0 then return {} end
  local kset = {}
  for _, id in ipairs(known) do kset[id] = true end
  local n0, w0, c0 = Geo.World(here.map, here.x, here.y)
  if not n0 then return {} end
  local fac = try(R.Faction)
  local list = {}
  for id, r in pairs(D.taxi or {}) do
    if not kset[id] and r[2] == c0 and (not R.FactionOK or R.FactionOK(r[8], fac)) then
      list[#list + 1] = { id = id, r = r, d = sqrt((r[3] - n0) ^ 2 + (r[4] - w0) ^ 2) }
    end
  end
  tsort(list, function(a, b) if a.d ~= b.d then return a.d < b.d end return a.id < b.id end)
  local out = {}
  for i = 1, min(2, #list) do
    local e = list[i]
    local p = placeOf(here, e.r[5], e.r[6], e.r[7])
    local why = { { 18, "point de vol non appris : trajets bien plus courts ensuite" } }
    local sc = 18 + travelPart(why, p.sec)
    out[#out + 1] = result("flight", e.id, "Prends le point de vol de " .. cut(e.r[1], 34), sc, why, p,
      { stopKind = "fp", stopLabel = "Point de vol : " .. cut(e.r[1], 30), now = p.yd and p.yd < 800 and fmt("Prends le point de vol à %s", dist(p.yd)) or nil })
  end
  return out
end
S.gear = function(here, ctx)
  local Gm = mod("Gear")
  local plan = Gm and Gm.plan
  if type(plan) ~= "table" or type(plan.slots) ~= "table" then return {} end
  local out = {}
  local VERB = { vendor = "Achète", quest = "Choisis en récompense", boss = "Vise en donjon", drop = "Va chercher", craft = "Fais fabriquer", ah = "Achète à l'HV" }
  for slot, list in pairs(plan.slots) do
    local cd = type(list) == "table" and list[1]
    local src = cd and cd.src
    if src and VERB[src.kind] and (cd.need or 0) <= ctx.level and (cd.pct or 0) >= 5 and cd.name then
      local word = (Gm.Verdict and Gm.Verdict(cd.pct)) or "mieux"
      local why = { { min(40, floor(cd.pct)), cd.pct >= 999 and "emplacement vide : tout objet est un gain"
        or (word .. " que ce que tu portes") } }
      local p
      if src.kind == "quest" and src.qid and T.Compass and T.Compass.QuestPoints then
        local pts = try(T.Compass.QuestPoints, { id = src.qid }, here and { mapID = here.map }) or {}
        local q = pts[1]
        if q and (q.mapID or q.map) and q.x then p = placeOf(here, q.mapID or q.map, q.x, q.y) end
      end
      local sc = why[1][1] + (p and travelPart(why, p.sec) or 0)
      out[#out + 1] = result("gear", cd.id or slot, fmt("%s %s", VERB[src.kind], cut(cd.name, 34)), sc, why, p,
        { tip = { "Où : " .. tostring(src.text or "?") }, stopKind = "find", stopLabel = "Équipement : " .. cut(cd.name, 30) })
    end
  end
  return out
end
S.dungeon = function(_, ctx)
  local D = T.Data and T.Data.Dungeons
  if not (D and type(D.list) == "table") then return {} end
  local lv, n = ctx.level, 1
  local Dt = T.Detect
  for _, m in ipairs(Dt and Dt.Group and try(Dt.Group, Dt) or {}) do
    if m.unit ~= "player" and tonumber(m.level) then lv, n = lv + m.level, n + 1 end
  end
  local avg = lv / n
  local fac = try(G("UnitFactionGroup"), "player")
  local out = {}
  for _, d in ipairs(D.list) do
    if d.min and d.max and d.min <= avg + 1 and avg <= d.max - 2 and (not d.faction or d.faction == fac) then
      local fit = 10 - min(10, abs((d.min + d.max) / 2 - avg) * 2)
      local why = { { 12, fmt("niveau %d–%d, groupe niv %d", d.min, d.max, floor(avg + 0.5)) } }
      if n >= 3 then why[#why + 1] = { 3 * min(4, n), fmt("vous êtes %d", n) } end
      if fit >= 1 then why[#why + 1] = { floor(fit), "pile à votre niveau" } end
      local sc = 0 for _, w in ipairs(why) do sc = sc + w[1] end
      local Dg = mod("Dungeon")
      out[#out + 1] = result("dungeon", d.key, "Fais " .. cut(d.n, 34) .. " en groupe", sc, why, nil, {
        act = Dg and Dg.SetPlanned and { label = "Prévoir", fn = function() pcall(Dg.SetPlanned, d.key) end } or nil })   -- l'onglet « Donjon » le montre : rien dans la discussion
    end
  end
  tsort(out, function(a, b) return a.score > b.score end)
  while #out > 2 do out[#out] = nil end
  return out
end
F.Sources = S

--- recherche : query = mot(s) ; → liste triée par gain (≤ 12). Sur demande / hors combat seulement.
function F.Search(query)
  local cats, free = F.ParseQuery(query)
  local here = me()
  local ctx = { level = level(), group = max(1, tonumber(try(G("GetNumGroupMembers"))) or 0),
    petTrain = (tonumber(try(G("GetPetTrainingPoints")) or try(C("C_PetInfo", "GetPetTrainingPoints"))) or 0) > 0 }
  local out = {}
  for _, c in ipairs(CATS) do
    if not cats or cats[c.id] then
      local ok, list = pcall(S[c.id], here, ctx)
      if ok and type(list) == "table" then for _, r in ipairs(list) do out[#out + 1] = r end
      elseif not ok then F:Debug("source " .. c.id .. " : " .. tostring(list)) end
    end
  end
  if free then
    local keep = {}
    for _, r in ipairs(out) do if low(r.title):find(free, 1, true) then keep[#keep + 1] = r end end
    out = keep
  end
  tsort(out, function(a, b) if a.score ~= b.score then return a.score > b.score end return a.key < b.key end)
  while #out > 12 do out[#out] = nil end
  F.last, F.lastAt, F.lastQuery = out, now(), query
  return out
end
--- le pourquoi EN MOTS (le score sert seulement à trier) : les plus d'abord, puis « mais : … »
function F.WhyText(r)
  local plus, minus = {}, {}
  for _, w in ipairs(r.why or {}) do
    if (w[1] or 0) >= 0 then plus[#plus + 1] = w[2] else minus[#minus + 1] = w[2] end
  end
  local t = {}
  for _, x in ipairs(plus) do t[#t + 1] = "+ " .. x end
  for _, x in ipairs(minus) do t[#t + 1] = "mais : " .. x end
  return t
end
--- raison principale, courte (sous-titre de la fenêtre)
function F.MainWhy(r)
  local best
  for _, w in ipairs(r.why or {}) do if (w[1] or 0) > 0 and (not best or w[1] > best[1]) then best = w end end
  return best and best[2] or nil
end
function F.Tooltip(r)
  local tip = {}
  for _, l in ipairs(r.tip or {}) do tip[#tip + 1] = l end
  tip[#tip + 1] = "Pourquoi :"
  for _, l in ipairs(F.WhyText(r)) do tip[#tip + 1] = "  " .. l end
  if r.map then tip[#tip + 1] = "Clic : y aller (boussole) · Maj-clic : ajouter au trajet" end
  return tip
end

-- ===================================================================== actions (clic seulement)
function F.GoTo(r)
  if not (r and r.map and r.x) then F:Say("Pas de position connue pour ça") return nil end
  local Cm = T.Compass
  if not (Cm and Cm.SetTarget) then return nil end
  return try(Cm.SetTarget, Cm, { mapID = r.map, x = r.x, y = r.y, label = cut(r.stopLabel or r.title, 40), source = "finder" })
end
local function stops() F.char.stops = type(F.char.stops) == "table" and F.char.stops or {} return F.char.stops end
local function leg(a, b)
  local s = travel(a, b)
  return s or 600
end
--- insère les arrêts Finder manquants dans le trajet du Planner (appelé par Planner:Evaluate) ; vrai si inséré
function F.Inject(steps, cursor)
  if type(steps) ~= "table" or not F.char then return false end
  local sig = F.char.sig or 0
  if steps.__fsig == sig then return false end
  steps.__fsig = sig
  local have = {}
  for _, s in ipairs(steps) do if s.finder then have[s.finder] = true end end
  local P = T.Planner
  local E = P and P.Engine
  local added = false
  for _, st in ipairs(stops()) do
    if not st.done and not have[st.key] and st.map and st.x then
      local s = { k = st.k or "find", label = st.label, map = st.map, x = st.x, y = st.y, finder = st.key }
      local i0 = max(1, min(tonumber(cursor) or 1, #steps + 1))
      if E and E.Insert then
        local okIns = pcall(E.Insert, steps, { s }, i0, 20, me() or steps[i0 - 1], leg)
        if not okIns then table.insert(steps, i0, s) end
      else
        table.insert(steps, i0, s)
      end
      added = true
    end
  end
  return added
end
--- arrêt Finder fait ? (arrivé à ≤ 40 m une fois, ou retiré)
function F.StopDone(s)
  for _, st in ipairs(stops()) do
    if st.key == s.finder then
      if st.done then return true end
      local p = me()
      local Geo = T.Geo
      local d = p and Geo and Geo.Dist and Geo.Dist(p.map, p.x, p.y, st.map, st.x, st.y)
      if d and d < 44 then st.done = true F.char.sig = (F.char.sig or 0) + 1 return true end
      return false
    end
  end
  return true
end
function F:AddToRoute(r)
  if inCombat() then self:Say("Trajet : après le combat") return false end
  if not (r and r.map and r.x) then self:Say("Pas de position connue pour « " .. cut(r and r.title, 40) .. " »") return false end
  local list = stops()
  for i = #list, 1, -1 do if list[i].key == r.key or list[i].done then table.remove(list, i) end end
  list[#list + 1] = { key = r.key, k = r.stopKind or "find", label = cut(r.stopLabel or r.title, 60), map = r.map, x = r.x, y = r.y }
  while #list > 8 do table.remove(list, 1) end
  self.char.sig = (self.char.sig or 0) + 1
  local P = T.Planner
  local R = T.Route
  if P and P.steps and P.Evaluate then
    pcall(P.Evaluate, P)
    P._sig = nil
    if P.Render then pcall(P.Render, P) end
    if R and R.following and R.OnPlanStep then pcall(R.OnPlanStep, R)
    elseif R and R.SPF and R.SPF() and R.Follow then pcall(R.Follow, R) end
    self:Say("Ajouté au trajet : " .. cut(r.stopLabel or r.title, 50))
    return "route"
  end
  self.GoTo(r)
  self:Say("Pas de trajet en cours : la boussole te guide vers l'arrêt")
  return "compass"
end
function F.RemoveStop(key)
  local list = stops()
  for i = #list, 1, -1 do if list[i].key == key then list[i].done = true end end
  F.char.sig = (F.char.sig or 0) + 1
end

-- ===================================================================== « Maintenant » + onglet
--- propriétaire unique d'« apprivoiser » (Boost / HunterPro se taisent quand c'est vrai)
function F.OwnsPet()
  return F.IsEnabled and F:IsEnabled() and opt("pets") and myClass() == "HUNTER" and pets() ~= nil or false
end
local NOW_MIN = 15
function F:UpdateNow(list)
  if not opt("now") then self:Clear("now") return end
  local lim = (tonumber(self.db.nearMin) or 5) * 60
  local best
  for _, r in ipairs(list or {}) do
    if r.now and r.sec and r.sec <= lim and r.score >= NOW_MIN and (not best or r.score > best.score) then best = r end
  end
  if best then
    self:Suggest("now", best.now, 1, { ttl = 150, cooldown = 600, sound = false, tab = TAB, tooltip = F.Tooltip(best),
      onClick = function() if try(G("IsShiftKeyDown")) then F:AddToRoute(best) else F.GoTo(best) end end })
  else
    self:Clear("now")
  end
end
local function click(r)
  return function()
    if try(G("IsShiftKeyDown")) then F:AddToRoute(r) else F.GoTo(r) end
  end
end
function F:Publish(list)
  list = list or self.last or {}
  local lines = {}
  local nPet = 0
  for _, r in ipairs(list) do
    if #lines >= 8 then break end
    if r.cat ~= "pet" or nPet < 3 then
      if r.cat == "pet" then nPet = nPet + 1 end
      local where = r.sec and (" · " .. minutes(r.sec)) or ""
      lines[#lines + 1] = { text = r.title .. where, prio = 1, tooltip = F.Tooltip(r), onClick = r.map and click(r) or (r.act and r.act.fn) or nil }
    end
  end
  if #lines == 0 then
    lines[1] = { text = "Rien qui change tout ici pour l'instant.", prio = 1, tooltip = "Clique sur « Chercher » pour tout voir (familier, rares, maîtres, vols, trésors, donjons)." }
  end
  lines[#lines + 1] = { text = "Chercher", control = true, label = "Chercher", onClick = function() F.Show(true) end, tooltip = "Ouvrir « Chercher autour de moi »" }
  lines[#lines + 1] = { text = "Actualiser", control = true, label = "Actualiser", onClick = function() F:Refresh(true) end, tooltip = "Recalculer maintenant" }
  if F._active and T.SetTabLines then pcall(T.SetTabLines, T, TAB, lines, "Finder") end   -- (4.0 QA) coupé : rien
end
local lastRun = -1000
--- retour d'un clic : une ligne brève dans le panneau (jamais la discussion)
function F:Say(text) pcall(self.Suggest, self, "info", text, 1, { ttl = 8, cooldown = 0, sound = false, tab = TAB }) end

function F:Refresh(force)
  if inCombat() then self._dirty = true return end
  if not force and now() - lastRun < 25 then return end
  lastRun = now()
  self._dirty = false
  local list = F.Search(self.lastQuery)
  self:Publish(list)
  self:UpdateNow(list)
  if F.frame and F.frame:IsShown() then F.Render() end
  return list
end

-- ===================================================================== fenêtre « Chercher »
local W = { rows = {}, chips = {} }
F._W = W
local ROWS, ROW_H = 7, 34
local function btn(parent, text, w, h, fn, tip)
  local Th = T.Theme
  local b
  if Th and Th.Button then b = Th:Button(parent, text, w, h, fn, tip) end
  if not b then
    b = _G.CreateFrame("Button", nil, parent)
    b:SetSize(w, h)
    b.label = b:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
    b.label:SetPoint("CENTER", b, "CENTER", 0, 0)
    b.label:SetText(text)
    b.SetLabel = function(self2, t) self2.label:SetText(t) end
    b:SetScript("OnClick", fn)
  end
  return b
end
local function tipOn(frame, title, lines)
  frame:SetScript("OnEnter", function(self)
    local tt = G("GameTooltip")
    if not tt then return end
    tt:SetOwner(self, "ANCHOR_RIGHT")
    tt:AddLine(title, 1, 1, 1)
    for _, l in ipairs(type(lines) == "function" and lines() or lines or {}) do tt:AddLine(l, 0.85, 0.85, 0.85, true) end
    tt:Show()
  end)
  frame:SetScript("OnLeave", function() local tt = G("GameTooltip") if tt then tt:Hide() end end)
end
function F.CreateFrame()
  if F.frame or type(_G.CreateFrame) ~= "function" then return F.frame end
  local f = _G.CreateFrame("Frame", "TreeAIFinderFrame", _G.UIParent)
  F.frame = f
  f:SetSize(460, 120 + ROWS * ROW_H)
  f:SetPoint("CENTER", _G.UIParent, "CENTER", 0, 60)
  f:SetFrameStrata("MEDIUM")
  f:SetMovable(true)
  f:EnableMouse(true)
  f:SetClampedToScreen(true)
  f:RegisterForDrag("LeftButton")
  f:SetScript("OnDragStart", function(self) self:StartMoving() end)
  f:SetScript("OnDragStop", function(self) self:StopMovingOrSizing() end)
  if T.Theme and T.Theme.Skin then pcall(T.Theme.Skin, T.Theme, f, "window") else
    f.bg = f:CreateTexture(nil, "BACKGROUND") f.bg:SetAllPoints(f) f.bg:SetColorTexture(0.05, 0.05, 0.07, 0.92)
  end
  f.title = f:CreateFontString(nil, "OVERLAY", "GameFontNormal")
  f.title:SetPoint("TOPLEFT", f, "TOPLEFT", 10, -8)
  f.title:SetText("Chercher autour de moi")
  f.close = btn(f, "×", 20, 18, function() F.Show(false) end, { "Fermer" })
  f.close:SetPoint("TOPRIGHT", f, "TOPRIGHT", -6, -6)
  local eb = _G.CreateFrame("EditBox", "TreeAIFinderSearch", f, "InputBoxTemplate")
  eb:SetSize(300, 20)
  eb:SetPoint("TOPLEFT", f, "TOPLEFT", 16, -30)
  eb:SetAutoFocus(false)
  eb:SetScript("OnEnterPressed", function(self) F.lastQuery = self:GetText() F:Refresh(true) self:ClearFocus() end)
  eb:SetScript("OnEscapePressed", function(self) self:ClearFocus() end)
  f.search = eb
  f.go = btn(f, "Chercher", 90, 20, function() F.lastQuery = eb:GetText() F:Refresh(true) end, { "Mots : familier, rare, équipement, maître, vol, marchand, trésor, donjon… (vide = tout)" })
  f.go:SetPoint("LEFT", eb, "RIGHT", 10, 0)
  local chips = { { "", "Tout" } }
  for _, c in ipairs(CATS) do chips[#chips + 1] = { c.kw[1], c.label } end
  for i, c in ipairs(chips) do
    local b = btn(f, c[2], 82, 18, function() eb:SetText(c[1]) F.lastQuery = c[1] F:Refresh(true) end)
    b:SetPoint("TOPLEFT", f, "TOPLEFT", 12 + ((i - 1) % 5) * 87, -56 - floor((i - 1) / 5) * 21)
    W.chips[i] = b
  end
  for i = 1, ROWS do
    local row = _G.CreateFrame("Button", nil, f)
    row:SetSize(436, ROW_H - 2)
    row:SetPoint("TOPLEFT", f, "TOPLEFT", 12, -104 - (i - 1) * ROW_H)
    row.text = row:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
    row.text:SetPoint("TOPLEFT", row, "TOPLEFT", 2, -2)
    row.text:SetWidth(270)
    row.text:SetJustifyH("LEFT")
    row.sub = row:CreateFontString(nil, "OVERLAY", "GameFontDisableSmall")
    row.sub:SetPoint("TOPLEFT", row.text, "BOTTOMLEFT", 0, -2)
    row.sub:SetWidth(270)
    row.sub:SetJustifyH("LEFT")
    row.add = btn(row, "Ajouter au trajet", 104, 18, function() if row.r then F:AddToRoute(row.r) end end, { "Insère cet arrêt dans ton trajet (Shortest Path suit s'il est utilisé)" })
    row.add:SetPoint("RIGHT", row, "RIGHT", -62, 0)
    row.go = btn(row, "Y aller", 58, 18, function() if row.r then if row.r.map then F.GoTo(row.r) elseif row.r.act then row.r.act.fn() end end end, { "La boussole te guide" })
    row.go:SetPoint("RIGHT", row, "RIGHT", 0, 0)
    tipOn(row, "Pourquoi", function() return row.r and F.Tooltip(row.r) or {} end)
    W.rows[i] = row
  end
  f.empty = f:CreateFontString(nil, "OVERLAY", "GameFontDisableSmall")
  f.empty:SetPoint("TOP", f, "TOP", 0, -130)
  local list = G("UISpecialFrames")
  if type(list) == "table" then list[#list + 1] = "TreeAIFinderFrame" end
  -- (REVIEW) même chrome que les autres fenêtres TreeAI (verre, Échap, fondu) : HV, Macros, À faire
  if T.Theme and T.Theme.Window then pcall(T.Theme.Window, T.Theme, f, { id = "finder", header = false }) end
  f:Hide()
  return f
end
function F.Render()
  local f = F.frame
  if not f then return end
  local list = F.last or {}
  for i, row in ipairs(W.rows) do
    local r = list[i]
    row.r = r
    if r then
      row.text:SetText(fmt("%s", r.title))
      local where = r.sec and (minutes(r.sec) .. (r.yd and (" · " .. dist(r.yd)) or "")) or (r.map and "trajet inconnu" or "sans position")
      local why = F.MainWhy(r)
      row.sub:SetText(why and (cut(why, 44) .. " · " .. where) or where)
      if r.map then row.add:Show() row.go:Show() if row.go.SetLabel then row.go:SetLabel("Y aller") end
      elseif r.act then row.add:Hide() row.go:Show() if row.go.SetLabel then row.go:SetLabel(r.act.label) end
      else row.add:Hide() row.go:Hide() end
      row:Show()
    else
      row:Hide()
    end
  end
  f.empty:SetText(#list == 0 and "Rien trouvé : essaie « Tout »." or "")
end
function F.Show(on)
  local f = F.CreateFrame()
  if not f then return end
  if on == nil then on = not f:IsShown() end
  if on then
    f:Show()
    if f.search and f.search.SetText then f.search:SetText(F.lastQuery or "") end
    F:Refresh(true)
    F.Render()
  else
    f:Hide()
  end
end

-- ===================================================================== vie du module
function F:OnEnable()
  if T.RegisterTab then T:RegisterTab(TAB, "Chercher", 7) end
  self:RegisterEvent("PLAYER_TARGET_CHANGED", function() F.LearnUnit("target") end)
  self:RegisterEvent("UPDATE_MOUSEOVER_UNIT", function() F.LearnUnit("mouseover") end)
  self:RegisterEvent("PLAYER_LEVEL_UP", function() F._dirty = true end)
  self:RegisterEvent("ZONE_CHANGED_NEW_AREA", function() F._dirty = true end)
  self.ticker = T:NewTicker(30, function()
    if inCombat() then return end
    -- (CLEAN) toutes les 2 min suffisent ; jamais dans l'image du minuteur (où tournent d'autres modules) : 1 s après
    if F._dirty or now() - lastRun >= 120 then T:After(1.1, function() if not inCombat() then F:Refresh(true) end end) end
  end, self)
  T:After(8, function() if not inCombat() then F:Refresh(true) end end)
end
function F:OnDisable()
  if self.ticker and self.ticker.Cancel then self.ticker:Cancel() end
  self.ticker = nil
  if F.frame then F.frame:Hide() end
end
function F:OnOptionChanged() lastRun = -1000 end

T:RegisterSlash("cherche", function(rest)
  F.lastQuery = tostring(rest or "")
  F:Refresh(true)
  F.Show(true)   -- tout est dans la fenêtre (rien dans le chat)
end, "chercher autour de moi : /tai cherche [familier|rare|équipement|maître|vol|marchand|trésor|donjon]")
