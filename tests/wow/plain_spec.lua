-- Modules/Plain.lua (agent SIMPLE) : phrases en clair, pur, aucun appel WoW.
-- Usage : lua5.1 tests/wow/plain_spec.lua [Plain.lua]
local path = arg[1] or "wow_addon/TreeAI/Modules/Plain.lua"
local fails, checks = 0, 0
local function ok(cond, msg)
  checks = checks + 1
  if not cond then fails = fails + 1; print("FAIL " .. msg) end
end
local function eq(a, b, msg) ok(a == b, msg .. " : attendu [" .. tostring(b) .. "], obtenu [" .. tostring(a) .. "]") end

local env = setmetatable({}, { __index = _G })
env._G = env
env.TreeAI = {}
local chunk = assert(loadfile(path))
setfenv(chunk, env)
chunk()
local P = env.TreeAI.Plain
ok(P, "TreeAI.Plain chargé")

-- temps, direction, lieu
eq(P.Minutes(20), "≈ 1 min", "jamais moins d'1 min")
eq(P.Minutes(170), "≈ 3 min", "3 min")
eq(P.Minutes(4800), "≈ 1 h 20", "1 h 20")
eq(P.Minutes(nil), nil, "pas de temps → rien")
eq(P.Dir(0), "au nord", "cap 0 = nord")
eq(P.Dir(math.pi / 2), "à l'ouest", "anti-horaire : +90° = ouest")
eq(P.Dir("SE"), "au sud-est", "lettres du planificateur")
eq(P.Where(20, 0), "juste à côté", "< 40 m")
eq(P.Where(150, math.pi), "tout près, au sud", "tout près")
eq(P.Where(600, 0), "au nord", "plus loin : la direction")
eq(P.Where(3000, 0, "Hurlevent", "Forêt d'Elwynn"), "à Hurlevent", "autre zone : à <ville>")

-- pluriels
eq(P.Plural("Loup", 6), "Loups", "Loup → Loups")
eq(P.Plural("Jeune loup", 3), "Jeunes loups", "adjectif + nom")
eq(P.Plural("Peau de loup", 4), "Peaux de loup", "de … reste au singulier")
eq(P.Plural("Ours gris vicieux", 2), "Ours gris vicieux", "déjà en s/x")
eq(P.Plural("Gnoll Rivepatte", 2), "Gnolls Rivepatte", "nom propre intact")

-- actions (verbe d'abord)
eq(P.ObjAction({ name = "Loup tué", have = 2, need = 8, t = "m" }), "Va tuer 6 Loups", "tuer")
eq(P.ObjAction({ name = "Peau de loup", have = 0, need = 4, t = "i" }), "Ramasse 4 Peaux de loup", "ramasser")
eq(P.ObjAction({ name = "Sang de lion", have = 2, need = 10, t = "m" }, { mob = "Lion des collines", rate = 0.5 }),
  "Récupère 8 Sangs de lion sur les Lions des collines", "butin connu → récupérer sur")
eq(P.ObjAction({ name = "Trouver la ferme abandonnée", have = 0, need = 1, t = "e" }), "Trouve la ferme abandonnée", "infinitif → impératif")
eq(P.ObjAction({ name = "Aller à la tour", have = 0, need = 1, t = "e" }), "Va à la tour", "aller → va")

-- LA ligne « À faire maintenant »
eq(P.Task({ title = "Les loups", obj = { name = "Loup tué", have = 2, need = 8, t = "m" }, dist = 600, bearing = 0, sec = 170 }),
  "Va tuer 6 Loups au nord (≈ 3 min)", "tâche : tuer, lieu, temps")
eq(P.Task({ complete = true, title = "Sang de lion", npc = "Dughan", turnZone = "Hurlevent", here = "Forêt d'Elwynn", dist = 3000, sec = 50 }),
  "Rends « Sang de lion » à Dughan, à Hurlevent (≈ 1 min)", "tâche : rendre")

-- étiquettes (UN mot) + raison
eq((P.Tag({ complete = true })), "Prête à rendre", "prête")
eq((P.Tag({ verdict = "abandon", reason = "trop facile maintenant" })), "À retirer", "à retirer")
eq((P.Tag({ hard = true, sec = 100 })), "À plusieurs", "à plusieurs")
eq((P.Tag({ sec = 300 })), "Rapide", "rapide")
eq((P.Tag({ sec = 1800 })), "Long", "long")
local _, why = P.Tag({ verdict = "abandon", reason = "trop facile maintenant" })
eq(why, "Elle ne vaut plus le coup : trop facile maintenant.", "raison courte")
eq(P.Why({}), "C'est la quête la plus rapide autour de toi.", "pourquoi par défaut")

-- trajets
eq(P.RouteName("xph", "XP/h max"), "Le plus rapide", "xph")
eq(P.RouteName("clear"), "Tout finir ici", "clear")
eq(P.RouteName("squad", "Escouade (4)"), "Avec mes potes", "squad")

-- garde-fou de robustesse
ok(P.IsPlain("Les loups · 410 XP/min"), "XP/min permis (le joueur l'aime)")
ok(not P.IsPlain("Garder (score 74/100)"), "score refusé")
ok(not P.IsPlain("λ = 0,3"), "λ refusé")
ok(P.IsPlain("Récompense +12 %"), "% permis")
ok(P.IsPlain("Niv 27 → 28 : 46 %"), "% de progression permis")
ok(P.IsPlain("|cffffd100À faire maintenant|r : Va tuer 6 Loups au nord (≈ 3 min)"), "ligne simple permise")

print(("plain_spec: %d checks, %d failures"):format(checks, fails))
os.exit(fails == 0 and 0 or 1)
