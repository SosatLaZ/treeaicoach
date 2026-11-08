-- Modules/Abandon.lua : cœur pur (A.*), chiffres exacts. Aucun appel WoW.
-- Usage : lua5.1 tests/wow/abandon_spec.lua [Abandon.lua]
local path = arg[1] or "wow_addon/TreeAI/Modules/Abandon.lua"
local fails, checks = 0, 0
local function ok(cond, msg)
  checks = checks + 1
  if not cond then fails = fails + 1; print("FAIL " .. msg) end
end
local function eq(a, b, msg) ok(a == b, msg .. " : attendu [" .. tostring(b) .. "], obtenu [" .. tostring(a) .. "]") end
local function near(a, b, msg) ok(type(a) == "number" and math.abs(a - b) < 0.01, msg .. " : attendu " .. b .. ", obtenu " .. tostring(a)) end

-- TreeAI minimal : RegisterModule au chargement + la coupe de texte de Core (TreeAI.Cut, lue dans Core.lua)
local env = setmetatable({}, { __index = _G })
env._G = env
local core = path:gsub("Modules[/\\][^/\\]*$", "Core.lua")
env.TreeAI = { RegisterModule = function(_, name, tbl) tbl.name = name return tbl end,
  Cut = dofile(((arg[0] or ""):match("^(.*)[/\\]") or ".") .. "/corecut.lua")(core) }
local chunk = assert(loadfile(path))
setfenv(chunk, env)
chunk()
local M = env.TreeAI.Abandon
ok(M and M.Core, "module chargé, cœur exposé")
local A = M.Core

-- 1. formules de base
eq(A.XPFactor(5), 1, "XP pleine à +5")
eq(A.XPFactor(6), 0.8, "80 % à +6")
eq(A.XPFactor(8), 0.4, "40 % à +8")
eq(A.XPFactor(12), 0.1, "10 % à +10 et plus")
eq(A.GreyLevel(26), 19, "gris niv 26")
eq(A.GreyLevel(34), 26, "gris niv 34")
eq(A.GreyLevel(50), 39, "gris niv 50")
eq(A.Color(26, 34), "gris", "26 grise au niv 34")
eq(A.Color(29, 26), "orange", "orange +3")
eq(A.Color(31, 26), "rouge", "rouge +5")
eq(A.EstimateXP(26), 2106, "XP estimée niv 26")
near(A.GrindXPM(30, 25), 334.29, "XP/min en farmant niv 30")
local s, mobs = A.ObjSeconds({ name = "Défense", have = 0, need = 4, t = "i" }, { secPerKill = 25, drops = { ["défense"] = { rate = 0.3 } } })
eq(s, 350, "drop 30 % : 14 monstres × 25 s")
eq(mobs, 14, "monstres pour 4 objets à 30 %")
eq((A.ObjSeconds({ have = 3, need = 10, t = "m" }, { secPerKill = 25 })), 175, "7 kills × 25 s")
eq((A.ObjSeconds({ have = 0, need = 3, t = "i" }, {})), 225, "drop inconnu 35 % : 9 monstres × 25 s")
local d, why, hard = A.Difficulty({ level = 27, elite = true }, 26, 1)
eq(d, 4.5, "élite +1 seul : 1,5 × 3")
ok(hard and why[1] == "élite", "élite seul = pas faisable")

-- 2. l'exemple de l'utilisateur : grise, longue, peu d'XP, aucune suite
local ctx = { pl = 34, group = 1, zone = "Contreforts", secPerKill = 25, drops = { ["défense"] = { rate = 0.3 } },
  logCount = 3, logMax = 20 }
local bad = { id = 1, title = "Vieille corvée", level = 26, xp = 120, zone = "Contreforts", travelSec = 300, chain = false,
  obj = { { name = "Sanglier tué", have = 3, need = 10, t = "m" }, { name = "Défense", have = 0, need = 4, t = "i" } } }
local good1 = { id = 2, title = "Les tours", level = 34, xp = 2460, zone = "Contreforts", travelSec = 285, obj = {} }
local good2 = { id = 3, title = "Le col", level = 34, xp = 2460, zone = "Contreforts", travelSec = 285, obj = {} }
local ev = A.EvaluateAll({ bad, good1, good2 }, ctx)
local e1 = ev[1]
eq(e1.q.title, "Vieille corvée", "la pire en tête")
eq(e1.sec, 840, "temps : 300 trajet + 525 farm + 15")
near(e1.xpm, 8.57, "XP/min")
eq(e1.ref, 410, "référence = médiane des autres")
eq(e1.score, 5, "score")
eq(e1.verdict, "abandon", "verdict")
eq(e1.text, "Abandonne « Vieille corvée » : grise (tu as 8 niveaux de plus), 120 XP seulement pour ~14 min de trajet et de farm"
  .. " → 9 XP/min contre 410 XP/min pour tes autres quêtes ; elle n'ouvre aucune suite.", "explication")
eq(e1.short, "grise, 9 XP/min (autres 410)", "ligne courte")
eq(e1.plain, "trop facile maintenant, presque pas d'XP", "raison en clair (agent SIMPLE)")
eq(A.PlainLine(e1), "Retire « Vieille corvée » : trop facile maintenant, presque pas d'XP", "ligne en clair")
ok(not A.PlainLine(ev[2]):find("%d"), "ligne gardée sans aucun nombre : " .. A.PlainLine(ev[2]))
eq(ev[2].score, 74, "bonne quête : score")
eq(ev[2].verdict, "keep", "bonne quête gardée")
near(ev[2].ref, 209.29, "sa référence (médiane 8,57 / 410)")
local fl = A.FreeList(ev)
eq(#fl, 1, "libérer de la place : 1 quête")
eq(A.HowTo("Vieille corvée"), "Pour la retirer : ouvre ton journal (L), clique « Vieille corvée », puis « Abandonner » et confirme."
  .. " TreeAI ne le fait jamais à ta place.", "marche à suivre")

-- 3. XP au rendu : un niveau pris avant le rendu (100 XP/min, 500 XP restants) → 80 % → 60 %
local ev2 = A.EvaluateAll({ { title = "Lettre", level = 24, xp = 1000, travelSec = 285, obj = {} } },
  { pl = 30, group = 1, secPerKill = 25, xpm = 100, xpToLevel = 500, logCount = 1, logMax = 20 })
local e2 = ev2[1]
eq(e2.xpTurn, 750, "XP au rendu")
near(e2.xpm, 125, "XP/min au rendu")
ok(e2.refGrind, "seule quête : référence = farmer")
eq(e2.score, 10, "score décroissance")
-- (QUESTQA) verte, journal à 1/20 : peu rentable selon le modèle, mais rien de FACTUEL → « plus tard », jamais « abandonne »
eq(e2.text, "Plus tard « Lettre » : elle perdra 250 XP si tu montes de niveau avant de la rendre, 750 XP seulement pour ~6 min"
  .. " de trajet et de farm → 125 XP/min contre 334 XP/min en tuant simplement des monstres ; fais-la si tu passes à côté.",
  "explication décroissance")

-- 4. amis + suite + récompense → garder (score plafonné à 100)
local c3 = { pl = 26, group = 1, secPerKill = 25, logCount = 5, logMax = 20 }
local e3 = A.Judge(A.Base({ title = "Les loups", level = 26, xp = 2100, travelSec = 120,
  obj = { { name = "Loup tué", have = 0, need = 10, t = "m" } }, mates = { "Bravo", "Charlie" },
  chain = { n = 2, title = "La suite", xp = 4200 }, reward = { pct = 12, item = "Gilet", slot = "Torse" } }, c3), 300, c3)
eq(e3.sec, 385, "temps 120 + 250 + 15")
eq(e3.score, 100, "score plafonné")
eq(e3.verdict, "keep", "gardée")
eq(e3.text, "Garde « Les loups » : Bravo et Charlie l'ont aussi : à faire ensemble, elle ouvre une suite (« La suite », 2 quêtes,"
  .. " ~4200 XP), récompense : Gilet (+12 % Torse), 2100 XP pour ~6 min → 327 XP/min, dans la moyenne (300).", "explication garder")
-- un ami l'a : jamais « abandonner »
local e3b = A.Judge(A.Base({ title = "Grise", level = 10, xp = 50, travelSec = 900, obj = {}, mates = { "Bravo" } }, c3), 300, c3)
eq(e3b.verdict, "later", "ami : au pire plus tard")

-- 5. élite rentable mais infaisable seul → plus tard (pas jetée)
local c4 = { pl = 26, group = 1, zone = "Hautebrande", secPerKill = 25, logCount = 5, logMax = 20 }
local e4 = A.Judge(A.Base({ title = "La tête du chef", level = 27, elite = true, xp = 2300, zone = "Hautebrande",
  obj = { { name = "Chef tué", have = 0, need = 1, t = "m" } } }, c4), 600, c4)
near(e4.sec, 367.5, "temps élite (×4,5)")
eq(e4.score, 13, "score élite")
eq(e4.verdict, "later", "élite rentable : plus tard")
eq(e4.text, "Plus tard « La tête du chef » : élite : pas faisable seul, 2300 XP pour ~6 min → 376 XP/min, dans la moyenne (600)"
  .. " ; garde-la pour quand tes amis seront là.", "explication élite")

-- 6. place dans le journal : même quête (score 35) plus tard à 10/20, à abandonner à 19/20
local q5 = { title = "Moyenne", level = 26, xp = 1000, travelSec = 285, obj = {} }
local e5 = A.Judge(A.Base(q5, { pl = 26 }), 250, { pl = 26, logCount = 10, logMax = 20 })
eq(e5.score, 35, "score moyen")
eq(e5.verdict, "later", "journal 10/20 : plus tard")
eq(A.Judge(A.Base(q5, { pl = 26 }), 250, { pl = 26, logCount = 19, logMax = 20 }).verdict, "later", "journal 19/20 : plus tard (pas un fait)")
eq(A.Judge(A.Base(q5, { pl = 26 }), 250, { pl = 26, logCount = 20, logMax = 20 }).verdict, "abandon", "journal plein 20/20 : abandon")

-- 7. relais inutile, quête prête, quête isolée / zone quittée
local e6 = A.Judge(A.Base({ title = "Le relais", level = 26, xp = 400, travelSec = 100, obj = {}, breadcrumbUseless = true }, c3), 300, c3)
eq(e6.verdict, "abandon", "relais inutile")
ok(e6.text:find("relais inutile", 1, true), "relais expliqué")
local e7 = A.Judge(A.Base({ title = "Courrier", level = 20, xp = 300, complete = true, travelSec = 600, obj = {} }, c3), 300, c3)
eq(e7.verdict, "keep", "prête à rendre : gardée")
ok(e7.score >= 90 and e7.text:find("prête à rendre", 1, true), "prête : score ≥ 90")
local c8 = { pl = 26, zone = "Hautebrande", leavingZone = "Les Paluns", logCount = 8, logMax = 20 }
local e8 = A.Judge(A.Base({ title = "Loin", level = 26, xp = 2000, zone = "Les Paluns", isolated = true, travelSec = 285, obj = {} }, c8), 300, c8)
ok(e8.text:find("seule quête en Les Paluns", 1, true) and e8.text:find("tu quittes Les Paluns", 1, true), "zone : isolée + quittée")
eq(e8.score, 40, "score zone (50 + 25·log2(333/300) − 8 − 6 = 39,8)")

print(("abandon_spec: %d checks, %d failures"):format(checks, fails))
os.exit(fails == 0 and 0 or 1)
