-- Modules/Brain.lua (« Pilote auto ») : cœur pur (B.Core), décisions exactes. Aucun appel WoW.
-- Usage : lua5.1 tests/wow/brain_spec.lua [Brain.lua]
local path = arg[1] or "wow_addon/TreeAI/Modules/Brain.lua"
local fails, checks = 0, 0
local function ok(cond, msg)
  checks = checks + 1
  if not cond then fails = fails + 1; print("FAIL " .. msg) end
end
local function eq(a, b, msg) ok(a == b, msg .. " : attendu [" .. tostring(b) .. "], obtenu [" .. tostring(a) .. "]") end

-- TreeAI minimal : RegisterModule au chargement + la coupe de texte de Core (TreeAI.Cut, lue dans Core.lua)
local env = setmetatable({}, { __index = _G })
env._G = env
local core = path:gsub("Modules[/\\][^/\\]*$", "Core.lua")
env.TreeAI = { RegisterModule = function(_, name, tbl) tbl.name = name return tbl end, SAFE_MODULES = {}, MODULE_INFO = {},
  Cut = dofile(((arg[0] or ""):match("^(.*)[/\\]") or ".") .. "/corecut.lua")(core) }
local chunk = assert(loadfile(path))
setfenv(chunk, env)
chunk()
local B = env.TreeAI.Brain
ok(B and B.Core, "module chargé, cœur exposé")
ok(env.TreeAI.SAFE_MODULES.Brain == true, "rangé dans le mode sûr")
local K = B.Core

-- 1. contexte principal : un seul, dans l'ordre d'importance
eq(K.Main({}), "idle", "rien : calme")
eq(K.Main({ quest = true }), "quest", "trajet en cours : en quête")
eq(K.Main({ quest = true, travel = true }), "travel", "en route passe avant en quête")
eq(K.Main({ quest = true, city = true }), "city", "en ville")
eq(K.Main({ city = true, afk = true }), "afk", "absent passe avant la ville")
eq(K.Main({ afk = true, taxi = true }), "taxi", "en vol passe avant absent")
eq(K.Main({ taxi = true, instance = true }), "dungeon", "donjon passe avant le vol")
eq(K.Main({ instance = true, dead = true }), "dead", "mort : avant tout")
eq(K.Main({ dead = true, ghost = true }), "ghost", "fantôme")
eq(K.Main({ grind = true, quest = true }), "grind", "farmer passe avant en quête")

-- 2. « tu farmes » : 3 combats en 5 min, rien d'avancé, pas d'arrêt tout près
local t = 1000
eq(K.Grinding({ 800, 900, 990 }, t, nil, nil), true, "3 combats en 5 min, aucun arrêt : farme")
eq(K.Grinding({ 600, 900, 990 }, t, nil, nil), false, "un combat il y a plus de 5 min : 2 seulement")
eq(K.Grinding({ 800, 900, 990 }, t, 950, nil), false, "une quête a avancé : pas farmer")
eq(K.Grinding({ 800, 900, 990 }, t, 600, 300), false, "un arrêt du trajet à 300 yd : c'est la quête")
eq(K.Grinding({ 800, 900, 990 }, t, 600, 900), true, "arrêt à 900 yd : farme")

-- 3. besoins (faits du jeu) : urgents d'abord
local n = K.Needs({ free = 0, durMin = 8, broken = 0, ammo = 60, ammoWord = "flèches" })
eq(#n, 3, "3 besoins")
eq(n[1].key, "repair", "réparer d'abord")
eq(n[1].urgent, true, "équipement à 8 % : urgent")
eq(n[1].text, "Répare : équipement à 8 %", "texte réparer")
eq(n[2].text, "Sacs pleins : vends chez un marchand", "sacs pleins")
eq(n[3].text, "Munitions : plus que 60 flèches", "munitions")
eq(K.NeedsShort(n), "répare · vends · achète des flèches", "mots courts")
eq(K.NeedsSig(n), "repair!,bags!,ammo!", "signature")
n = K.Needs({ free = 2, durMin = 20, ammo = 300, ammoWord = "balles", ammoMin = 12 })
eq(n[1].text, "Répare bientôt : équipement à 20 %", "réparer bientôt")
eq(n[1].urgent, false, "20 % : pas urgent")
eq(n[2].text, "Sacs : plus que 2 places", "sacs presque pleins")
eq(n[3].text, "Munitions : 300 balles (≈ 12 min de jeu)", "munitions au rythme mesuré")
eq(K.Needs({ durMin = 60, broken = 1 })[1].text, "Répare : 1 pièce cassée", "une pièce cassée")
eq(K.Needs({ durMin = 60, broken = 2 })[1].text, "Répare : 2 pièces cassées", "deux pièces cassées")
eq(#K.Needs({ free = 12, durMin = 90, ammo = 1200 }), 0, "tout va bien : aucun besoin")
eq(#K.Needs({ free = 12, durMin = 90 }), 0, "pas d'arme à munitions : aucun besoin de munitions")
eq(K.Needs({ train = 28 })[1].text, "Maître de classe : nouveaux sorts (niveau 28)", "maître de classe")
eq(K.Needs({ train = 28, trainCost = 4200 })[1].text, "Maître de classe : sorts du niveau 28", "maître, prix lu")
eq(K.StopLabel({ "répare", "vends" }, "Forgeron"), "Répare · vends (Forgeron)", "étiquette d'arrêt")
eq(K.StopLabel({ "achète des flèches" }), "Achète des flèches", "étiquette sans nom")

-- 4. détour ruineux : XP réelle, détour mesuré, XP/h mesurée
local low, txt = K.LowValue(200, 300, 20000)
eq(low, true, "200 XP pour 5 min, à 20 000 XP/h : ruineux")
eq(txt, "200 XP pour ≈ 5 min de détour (tu fais ≈ 20 000 XP/h)", "texte du détour")
eq((K.LowValue(400, 180, 20000)), false, "400 XP pour 3 min : 6 857 XP/h ≥ 25 % → garde")
eq((K.LowValue(200, 100, 20000)), false, "détour < 2 min 30 : sur le chemin, garde")
eq((K.LowValue(200, 300, nil)), false, "XP/h pas mesurée : la règle attend")
eq((K.LowValue(nil, 300, 20000)), false, "XP inconnue : rien")

-- 5. zone : seulement quand rien ne reste sur le trajet ; la meilleure, proche d'abord
eq(K.ZonePick(1, { { map = 2, n = 30, sec = 60 } }), nil, "une quête (ou une remise) encore au trajet : on reste")
local best = K.ZonePick(0, { { map = 2, n = 12, sec = 120 }, { map = 3, n = 20, sec = 1800 }, { map = 4, n = 5, sec = 10 } })
eq(best and best.map, 2, "12 quêtes à 2 min > 20 quêtes à 30 min ; 5 quêtes : trop peu")
eq(K.ZonePick(0, { { map = 4, n = 5, sec = 10 } }), nil, "aucune zone avec au moins 6 quêtes : rien")
eq((K.ZonePick(0, { { map = 9, n = 6 } }) or {}).map, 9, "trajet inconnu : 10 min supposées, encore choisie")

-- 6. mode du trajet : groupe → « groupe », dissous → le mode d'avant, le joueur gagne
eq(K.StyleFor(1, "fast", nil), nil, "seul : rien")
eq(K.StyleFor(2, "fast", nil), "group", "duo en « le plus rapide » : groupe")
eq(K.StyleFor(3, "story", nil), nil, "le joueur avait choisi « par histoire » : rien")
eq(K.StyleFor(1, "group", { set = "group", prev = "fast" }), "fast", "groupe dissous : le mode d'avant")
eq(K.StyleFor(2, "group", { set = "group", prev = "fast" }), nil, "toujours en groupe : rien")
eq(K.StyleFor(1, "clear", { set = "group", prev = "fast" }), nil, "le joueur a changé : rien")

-- 7. pierre de foyer
eq(K.HearthWorth(2, 400, true), true, "2 remises, 6 min 40 gagnées, rien ici")
eq(K.HearthWorth(1, 900, true), false, "une seule remise : non")
eq(K.HearthWorth(3, 200, true), false, "moins de 5 min gagnées : non")
eq(K.HearthWorth(3, 900, false), false, "encore du travail ici : non")

-- 8. raisons : toutes nommées, conseillées ; les raisons « sûres » proposent l'abandon (page Journal)
for k in pairs(K.PARK_KEYS) do
  ok(type(K.WHY[k]) == "string" and K.WHY[k] ~= "", "raison nommée : " .. k)
  ok(type(K.ADVICE[k]) == "string" and K.ADVICE[k] ~= "", "conseil : " .. k)
end
eq(K.STRONG.hard, nil, "trop dure : jamais « abandonner », elle revient")
eq(K.STRONG.far, nil, "loin : jamais « abandonner »")
eq(K.STRONG.grey, true, "grise : abandon proposé (2 clics, page Journal)")

print(("brain_spec: %d checks, %d failures"):format(checks, fails))
os.exit(fails == 0 and 0 or 1)
