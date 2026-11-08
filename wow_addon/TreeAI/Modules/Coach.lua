-- TreeAI — module « Conseils pro » (Coach.lua, ADVICE 4.0.1) : le geste d'un joueur expert, au bon moment.
-- Chaque conseil repose sur une donnée LISIBLE hors combat (vie / mana lus hors combat, auras, sacs, familier,
-- XP et repos, journal de quêtes, déplacements, tes combats mesurés) ET sur une règle vraie : tables du client
-- WoW: Forever 1.60.1 (sorts, objets, munitions, plats « Bien nourri ») ou règle Classic marquée « à vérifier ».
-- Rien de deviné : sans mesure, pas de chiffre (ou « ≈ » + la formule dans l'infobulle).
--  * UN seul conseil à la fois dans « Maintenant » (le plus rentable gagne), prio 2, ni son ni chat ;
--    infobulle « pourquoi » avec les chiffres ; chaque conseil a son temps de repos et son interrupteur.
--  * Page « Conseils pro » de la fenêtre TreeAI : ce qui est vrai maintenant, puis 8 thèmes (Pull et combat,
--    Repos et ressources, Familier, Quêtes et trajet, Argent et sacs, Duo et groupe, Donjons, Sécurité et JcJ) :
--    chaque conseil automatique (clic = ON / OFF) et les techniques d'expert à appliquer (montrées à la demande).
--  * Propriétaires respectés : quantité de munitions / humeur / dressage (Chasseur), boutons manger / bandage /
--    aspect (barre de combat), mort (Speed), rares (Rares), entraîneur (Guichets) : on ne redit pas leur ligne.
-- Coût : événements + une petite évaluation par passage d'AutoRefresh, RIEN en combat (la ligne s'efface au pull).
-- Lua 5.1, aucune globale, toutes les API testées (pcall), valeurs secrètes (Forever) ignorées.
local tonumber = function(v) return _G.tonumber(v) end   -- (4.0.8) tonumber(f()) : un 2e retour de f n'est jamais une base
local T = _G.TreeAI
if not (T and T.RegisterModule) then return end
local API = T.API or {}
local G = _G

-- mode sûr : lectures seules, hors combat, une ligne → robuste par construction, allumé par défaut
if type(T.SAFE_MODULES) == "table" then T.SAFE_MODULES.Coach = true end
if type(T.MODULE_INFO) == "table" and not T.MODULE_INFO.Coach then
  T.MODULE_INFO.Coach = { lite = true, desc = "Conseils pro : le bon geste au bon moment (repos, familier, munitions, XP…), un à la fois" }
end
do   -- page d'options rangée dans « Combat et classe », après Chasseur
  local fam = type(T.OPTIONS_FAMILY) == "table" and T.OPTIONS_FAMILY[2]
  if type(fam) == "table" and type(fam.mods) == "table" then
    local has = false
    for _, n in ipairs(fam.mods) do if n == "Coach" then has = true end end
    if not has then table.insert(fam.mods, math.min(#fam.mods + 1, 2), "Coach") end
  end
end

local floor, max, min = math.floor, math.max, math.min
local fmt, tconcat, tremove = string.format, table.concat, table.remove
local ICON = "Interface\\Icons\\"

-- ===================================================================================== thèmes, conseils, techniques
local THEMES = {
  { id = "pull", label = "Pull et combat", icon = ICON .. "Ability_Hunter_SniperShot" },
  { id = "rest", label = "Repos et ressources", icon = ICON .. "INV_Misc_Food_15" },
  { id = "pet", label = "Familier", icon = ICON .. "Ability_Hunter_BeastCall" },
  { id = "quest", label = "Quêtes et trajet", icon = ICON .. "INV_Misc_Book_09" },
  { id = "money", label = "Argent et sacs", icon = ICON .. "INV_Misc_Coin_01" },
  { id = "group", label = "Duo et groupe", icon = ICON .. "Spell_Holy_PrayerOfHealing" },
  { id = "dungeon", label = "Donjons", icon = ICON .. "INV_Misc_Key_03" },
  { id = "safety", label = "Sécurité et JcJ", icon = ICON .. "Ability_Rogue_FeignDeath" },
}
local THEME = {}
for _, th in ipairs(THEMES) do THEME[th.id] = th end

-- conseils automatiques : { clé, thème, libellé (option + page), quand il apparaît } ; hunter = chasseurs seulement ;
-- base = poids de départ (le plus rentable gagne) ; ttl = durée à l'écran ; cd = repos avant de revenir (s) ;
-- max = fois au plus par session (au-delà, tu le sais : la page le garde)
local TIPS = {
  { "petTank", "pull", "Ton familier tanke : laisse-lui l'aggro avant de tirer", hunter = true, base = 65, ttl = 60, cd = 1500,
    when = "Quand tu perds beaucoup de vie par combat alors que ton familier est là (mesuré sur tes combats)." },
  { "chainPull", "pull", "Enchaîner au lieu de manger quand ta vie suffit", base = 35, ttl = 30, cd = 1200, max = 3,
    when = "Quand tu t'assois pour manger alors que ta vie tient encore 2 combats (vie perdue mesurée)." },
  { "greenMobs", "pull", "Monstres trop faibles : vise ton niveau", base = 50, ttl = 45, cd = 1200, max = 3,
    when = "Quand tes 5 derniers monstres (hors quêtes) rapportent moins de 60 % de l'XP d'un monstre de ton niveau." },
  { "newTool", "pull", "Nouveau sort de chasseur : comment s'en servir", hunter = true, base = 66, ttl = 120, cd = 60,
    when = "Une fois, quand tu apprends un sort clé (Trait de choc, pièges, Visée, Tir rapide, Feindre la mort…)." },
  { "eatDrink", "rest", "Manger et boire en même temps", base = 75, ttl = 20, cd = 600, max = 4,
    when = "Quand tu manges sans boire (ou l'inverse) alors qu'il te manque aussi du mana (ou de la vie)." },
  { "bandage", "rest", "Bandage au lieu d'un repas quand il suffit", base = 55, ttl = 25, cd = 300, max = 5,
    when = "Après un combat, quand un bandage de tes sacs rend presque toute la vie qui manque." },
  { "drinkNow", "rest", "Boire avant de tomber à court de mana", base = 52, ttl = 30, cd = 600, max = 4,
    when = "Quand il te reste moins de mana qu'un combat n'en coûte (mesuré sur tes combats)." },
  { "wellFed", "rest", "Plat « Bien nourri » : +5 % d'XP des monstres (Forever)", base = 60, ttl = 75, cd = 1200, max = 4,
    when = "Quand tu n'as pas « Bien nourri – bonus d'EXP » et qu'un plat cuisiné qui le donne est dans tes sacs." },
  { "growlAuto", "pet", "Grondement en automatique en solo et en duo", hunter = true, base = 66, ttl = 60, cd = 1800, max = 2,
    when = "Solo ou duo, hors donjon : le Grondement de ton familier n'est pas en automatique." },
  { "petLevel", "pet", "Familier en retard de niveaux", hunter = true, base = 45, ttl = 60, cd = 2700,
    when = "Quand ton familier a 3 niveaux ou plus de retard sur toi." },
  { "restedKill", "quest", "Repos d'XP : tue les monstres sur ton chemin", base = 45, ttl = 60, cd = 1800, max = 3,
    when = "Quand ton repos couvre encore au moins 8 monstres à XP double, hors ville." },
  { "cheetah", "quest", "Aspect du guépard pour marcher", hunter = true, base = 50, ttl = 30, cd = 1200, max = 3,
    when = "Quand tu marches depuis 15 s hors combat sans aspect de voyage, sans ennemi ciblé." },
  { "turninLevel", "quest", "Quêtes finies = niveau suivant d'un coup", base = 68, ttl = 120, cd = 1800,
    when = "Quand l'XP de tes quêtes finies suffit pour passer le niveau." },
  { "questPenalty", "quest", "Rendre une quête avant qu'un niveau baisse son XP", base = 72, ttl = 90, cd = 1200,
    when = "Quand tu vas passer de niveau et qu'une quête finie perdrait une part de son XP (règle Classic)." },
  { "ammoTier", "money", "Munitions du marchand à ton niveau (plus de dégâts)", hunter = true, base = 70, ttl = 90, cd = 1800,
    when = "Quand tu tires des munitions de marchand d'un palier en dessous de ce que ton niveau permet." },
  { "emptyBag", "money", "Case de sac vide", base = 40, ttl = 60, cd = 3600, max = 2,
    when = "Quand une de tes 4 cases de sac est vide." },
  { "groupXP", "group", "En groupe de 3+, vise les monstres de quête", base = 30, ttl = 45, cd = 1800, max = 2,
    when = "À 3 ou plus, quand vos 6 derniers monstres hors quête rapportent moins de la moitié d'un monstre solo." },
  { "growlDungeon", "dungeon", "Grondement coupé en donjon (avec un tank)", hunter = true, base = 80, ttl = 90, cd = 600,
    when = "En donjon avec un tank, si le Grondement de ton familier est en automatique." },
  { "petDefensive", "dungeon", "Familier en défensif en donjon", hunter = true, base = 80, ttl = 90, cd = 600,
    when = "En donjon en groupe, si ton familier est en mode agressif." },
  { "eliteSolo", "safety", "Élite plus fort que toi, seul : prudence", base = 58, ttl = 40, cd = 600, max = 5,
    when = "Hors combat, seul, quand tu cibles un élite plus haut que toi (ou à ton niveau sans familier)." },
}
local TIP = {}
for i, tp in ipairs(TIPS) do tp.key, tp.theme, tp.label, tp.order = tp[1], tp[2], tp[3], i TIP[tp.key] = tp end

-- techniques d'expert (montrées à la demande dans la page) : { thème, titre, ligne, détails, source } ;
-- hunter = chasseurs seulement, level = à partir de quel niveau (sort appris)
local TECH = {
  { "pull", "Familier d'abord", "Envoie le familier, attends 2 morsures (≈ 2 s), puis tire : il garde l'aggro.", hunter = true,
    { "Marque du chasseur + familier sur la cible.", "Laisse-le mordre 2 fois : son Grondement lui donne de l'avance.",
      "Puis Morsure de serpent, tir auto, Tir des arcanes." }, "Classic (règle de menace)" },
  { "pull", "Ouverture à Visée", "Cible marquée à 35 m : Visée pendant qu'elle arrive, puis Tir des arcanes.", hunter = true, level = 20,
    { "Forever : Visée s'apprend au niveau 20 chez ton maître (pas un talent).",
      "Elle partage sa recharge avec Flèches multiples : Visée en ouverture, Multi sur les packs.",
      "Pas juste avant un tir auto : elle le retarde (la barre de tir le montre)." }, "Forever (description du sort)" },
  { "pull", "Pull en ligne de vue", "Lanceurs de sorts : tire puis cache-toi derrière un mur, ils viennent à toi.",
    { "Un lanceur s'arrête pour incanter tant qu'il te voit.", "Sans ligne de vue, il avance jusqu'à toi : le pack se regroupe.",
      "Là : piège, Flèches multiples, familier sur le plus dangereux." }, "Classic" },
  { "pull", "Kiter au corps à corps", "Coupure d'ailes, recule de 10 m, tire ; pas de mêlée qui dure.", hunter = true, level = 12,
    { "Trait de choc sur un monstre qui fonce sur toi.", "Aspect du singe si tu dois encaisser au contact.",
      "Classic : zone morte de 5 à 8 m (ni mêlée ni tir) — à vérifier sur Forever." }, "Classic, à vérifier" },
  { "pull", "Fuyards", "Humanoïdes vers 15-20 % de vie : Trait de choc avant qu'ils fuient chercher leurs amis.",
    { "Un fuyard ramène souvent un 2e pack." }, "Classic" },
  { "pull", "Enchaîner (chain pull)", "Pendant que le familier finit le 1er, marque et prépare le suivant.",
    { "Morsure de serpent sur le suivant pendant que le premier meurt.", "Regarde les survivants avant de partir." }, "Classic" },
  { "pull", "Piège avant le pull", "Pose un piège d'immolation (ou de givre pour ralentir), recule, tire : la cible marche dedans.",
    hunter = true, level = 16, { "Recharge commune aux pièges : un seul à la fois.",
      "Piège givrant : pour figer un 2e monstre, pas ta cible (tout dégât le libère)." }, "Classic" },
  { "pull", "Effrayer une bête", "Une 2e bête arrive : Effrayer une bête, tue la 1re, puis la 2e.", hunter = true, level = 14,
    { "Un dégât sur la bête effrayée peut la libérer.", "Elle peut courir chercher d'autres monstres." }, "Forever (description du sort)" },
  { "pull", "Tir rapide", "Pour les élites et les combats à 2 : +40 % de vitesse de tir 15 s.", hunter = true, level = 26,
    { "Recharge 5 min : pas sur un monstre normal seul." }, "Forever (guide Warcraft Tavern)" },
  { "rest", "Manger et boire ensemble", "Assis : mange ET bois, les deux tournent en même temps.",
    { "Un repas comme une boisson dure 18 à 30 s selon son niveau : ensemble = une attente, l'un après l'autre = deux." },
    "Classic" },
  { "rest", "Bandage pendant que le familier tanke", "Il a l'aggro : bandage (6 à 8 s) même en combat ; un coup reçu l'interrompt.",
    { "Ensuite 1 min de « Bandage appliqué récemment »." }, "Classic" },
  { "rest", "Ne pas manger pour rien", "Au-dessus de 70 % de vie avec un familier qui tanke, enchaîne.",
    { "TreeAI mesure ta vie perdue par combat et te le dit quand tu t'assois trop tôt." }, "Classic" },
  { "rest", "Bien nourri (Forever)", "Plat cuisiné mangé 10 s : +5 % d'XP des monstres tués.",
    { "Pas sur l'XP des quêtes.", "Sort « Bien nourri – bonus d'EXP » du client Forever ; la plupart des plats cuisinés le donnent." },
    "Forever (client 1.60.1)" },
  { "rest", "Repos d'XP", "Déconnecte-toi à l'auberge : 5 % d'un niveau par 8 h (4 fois moins dehors), 150 % au plus.",
    { "Forever : le Sac de couchage douillet (chaîne dès le niveau 14) donne du repos hors de la ville." },
    "Classic + Forever (wiki)" },
  { "pet", "Grondement", "Solo et duo : en automatique. En donjon avec un tank : coupé.", hunter = true,
    { "Clic droit sur le bouton du sort dans la barre du familier." }, "Classic" },
  { "pet", "Modes du familier", "Défensif en général, passif pour fuir, jamais agressif en donjon.", hunter = true,
    { "/petpassive puis /petfollow le sort d'un mauvais pull." }, "Classic" },
  { "pet", "Rendre l'aggro au familier", "Il perd la cible : Coupure d'ailes ou Trait de choc, recule, il la reprend.", hunter = true,
    { "Désengagement baisse ta menace au contact." }, "Classic" },
  { "pet", "Niveau du familier", "Il n'apprend que sur les monstres : en retard, fais-le tuer ou apprivoise à ton niveau.",
    hunter = true, { "Pas d'XP de quête pour lui ; il s'arrête à ton niveau." }, "Forever (guide Warcraft Tavern)" },
  { "pet", "Nourrir pendant ton repas", "Nourris-le pendant que tu manges : aucune seconde perdue.", hunter = true,
    { "Heureux = +25 % de dégâts, mécontent = −25 %." }, "Classic" },
  { "pet", "Le soigner tôt", "Guérison du familier pendant ton repas : le ressusciter coûte bien plus cher.", hunter = true,
    level = 12, { "Une mort lui coûte aussi de l'humeur." }, "Classic + Forever (guide)" },
  { "quest", "Rendre par paquets", "Finis toutes les quêtes d'un relais avant d'y retourner, rends tout d'un coup.",
    { "Prends toutes ses quêtes en partant.", "Passe chez ton maître de classe aux niveaux pairs (nouveaux sorts)." }, "Classic" },
  { "quest", "Repos = tuer en chemin", "Avec du repos, chaque monstre vaut double : ne contourne pas ceux de ta route.",
    { "Le repos ne touche pas l'XP des quêtes." }, "Classic" },
  { "quest", "XP des quêtes basses", "Une quête 6 niveaux sous toi rapporte 80 % de son XP, puis 20 % de moins par niveau.",
    { "À 10 niveaux d'écart : 10 %. Rends-la avant de monter." }, "Classic, à vérifier" },
  { "quest", "Pierre de foyer", "Lie-la au relais où tu rendras tes quêtes ; prête + long trajet = foyer.",
    { "Le module Trajet compare déjà foyer, vol et marche." }, "Classic" },
  { "quest", "Aspect du guépard", "Pour marcher : +30 % de vitesse, repasse en Faucon avant de tirer.", hunter = true, level = 20,
    { "Un coup reçu t'hébète : pas au milieu des monstres." }, "Forever (description du sort)" },
  { "money", "Munitions du bon palier", "Les meilleures flèches ou balles du marchand dès leur niveau.", hunter = true,
    { "Forever : Flèche pointue 3,6 dégâts/s, Flèche rasoir (niv 25) 7,6, Flèche barbelée (niv 40) 13.",
      "Elles s'ajoutent à chaque tir." }, "Forever (tables du client)" },
  { "money", "Carquois ou giberne", "Toujours équipé : tir plus rapide et place pour les munitions.", hunter = true,
    { "Le module Chasseur te prévient s'il manque." }, "Classic" },
  { "money", "Sacs pleins = butin perdu", "4 cases de sac remplies, gris vendus à chaque passage chez un marchand.",
    { "Forever : Petite sacoche marron 6 places (5 pa), Sacoche marron en cuir 8 places (25 pa)." }, "Forever (tables du client)" },
  { "money", "HV ou marchand", "Tissus, peaux, compos : à l'HV ; le reste au marchand.",
    { "La page HV de TreeAI ne conseille qu'avec un scan récent." }, "Classic" },
  { "group", "Kills partagés", "En groupe, un monstre de quête tué compte pour chacun (s'il est à portée).",
    { "Faites les objectifs de kill ensemble, séparez-vous pour les objets." }, "Classic" },
  { "group", "XP de groupe", "L'XP d'un monstre se partage (bonus ×1,17 à 3, ×1,3 à 4) : en gros groupe, vise les quêtes.",
    { "À 4, un monstre rapporte ≈ 33 % de son XP à chacun." }, "Classic, à vérifier" },
  { "group", "Duo avec un familier", "Ton familier tanke pour vous deux : il part en premier, ton ami tape après 2 s.",
    hunter = true, { "Marque la cible : tout le monde frappe la même." }, "Classic" },
  { "group", "Rendre ensemble", "Une quête à suite : rendez-la en même temps pour prendre la suite ensemble.",
    { "Le module Groupe te dit qui attendre." }, "Classic" },
  { "dungeon", "Familier en donjon", "Défensif, et Grondement coupé si quelqu'un tanke.", hunter = true,
    { "Sinon il prend l'aggro et le pack tue ton familier puis le soigneur." }, "Classic" },
  { "dungeon", "Piège givrant sur un add", "Pose-le avant le pull : l'add reste figé pendant qu'on tue le reste.",
    hunter = true, level = 20, { "Tout dégât le libère : pas de Flèches multiples sur lui." }, "Classic" },
  { "dungeon", "Laisser le tank prendre", "Attends 2-3 coups du tank avant tes gros tirs.",
    { "Visée en ouverture = aggro assurée sur toi." }, "Classic" },
  { "dungeon", "Munitions pour un donjon", "Prévois ≈ 1 000 munitions avant d'entrer.", hunter = true,
    { "La page Donjons fait la liste de préparation du groupe." }, "TreeAI (Donjon)" },
  { "safety", "Feindre la mort", "Les monstres t'oublient : pour lâcher des adds ou un pull raté.", hunter = true,
    level = 30, { "Passe le familier en passif pour un retour au calme complet.",
      "Peut être résisté : vérifie que tu sors bien du combat.",
      "Relève-toi, pose un piège givrant, puis reprends le pull à ton rythme." }, "Forever (description du sort)" },
  { "safety", "Fuir un combat perdu", "Laisse le familier tanker, Aspect du guépard, cours : le ressusciter coûte moins qu'une mort.",
    hunter = true, { "Une mort coûte de l'usure et une course au corps." }, "Classic" },
  { "safety", "Course au corps", "Reviens à ton corps : le gardien des esprits coûte de l'usure et du mal de résurrection.",
    { "Classic : 25 % d'usure et jusqu'à 10 min de faiblesse ; le module Vitesse calcule le meilleur choix." },
    "Classic, à vérifier" },
  { "safety", "JcJ sauvage", "Ennemi plus haut qui te vise : piège givrant ou Feindre la mort, puis fuis.", hunter = true,
    { "Ne lance pas un pull pendant qu'il te regarde." }, "Classic" },
  { "safety", "Élites seul", "Élite à ton niveau : familier heureux, recharges prêtes (Tir rapide dès le 26) ; plus haut, à deux.",
    hunter = true,
    { "Les élites rapportent 2 fois l'XP d'un monstre normal." }, "Classic" },
}
for _, tc in ipairs(TECH) do tc.theme, tc.title, tc.line, tc.steps, tc.src = tc[1], tc[2], tc[3], tc[4] or {}, tc[5] end

-- ===================================================================================== données du client Forever
-- plats « Bien nourri » qui donnent aussi +5 % d'XP des monstres (sort 1243969) : [objet] = niveau requis
-- (tables ItemEffect / ItemSparse / Spell du client 1.60.1 build 70862, via wago.tools)
local XPFOOD = {
  [724] = 5, [733] = 5, [787] = 1, [1017] = 15, [1082] = 5, [2680] = 1, [2681] = 1, [2682] = 1, [2683] = 5, [2684] = 5,
  [2685] = 15, [2687] = 5, [2888] = 1, [3220] = 5, [3662] = 5, [3663] = 1, [3664] = 15, [3665] = 15, [3666] = 15,
  [3726] = 5, [3727] = 15, [3728] = 25, [3729] = 15, [4457] = 25, [4592] = 5, [4593] = 15, [4594] = 25, [5095] = 5,
  [5472] = 1, [5474] = 1, [5476] = 5, [5477] = 1, [5479] = 5, [5480] = 15, [6038] = 25, [6290] = 1, [6316] = 5,
  [6887] = 35, [6888] = 1, [6890] = 1, [8364] = 25, [8957] = 45, [10841] = 25, [12209] = 15, [12210] = 15, [12212] = 35,
  [12213] = 5, [12214] = 15, [12215] = 25, [12216] = 25, [12218] = 35, [12224] = 1, [13851] = 25, [13927] = 35,
  [13928] = 35, [13929] = 35, [13930] = 35, [13931] = 35, [13932] = 35, [13933] = 45, [13934] = 45, [13935] = 45,
  [17222] = 35, [18045] = 35, [18254] = 35, [20074] = 25, [20452] = 45, [21072] = 10, [21217] = 30, [249865] = 1,
  [249866] = 5, [249867] = 15, [249868] = 25, [249869] = 35, [249870] = 45, [249871] = 1, [249872] = 5, [249873] = 15,
  [249874] = 25, [249875] = 35, [249876] = 45, [250065] = 35, [250066] = 45, [250067] = 45, [250068] = 45, [250069] = 45,
  [250070] = 45, [250071] = 45, [250072] = 45, [250073] = 25, [250074] = 25, [250075] = 45, [250076] = 35, [250077] = 5,
  [250078] = 25, [250079] = 1, [250080] = 5, [263509] = 5, [263512] = 5, [267341] = 55, [268912] = 5, [268913] = 5,
  [274971] = 25, [274976] = 25, [286152] = 35, [287973] = 1, [287974] = 1,
}
local WELLFED_XP = 1243969                                   -- « Bien nourri – bonus d'EXP »
-- bandages : [objet] = { PV rendus, secourisme requis } (tables du client Forever)
local BANDAGE = { [1251] = { 66, 1 }, [2581] = { 114, 20 }, [3530] = { 161, 50 }, [3531] = { 301, 75 },
  [6450] = { 400, 100 }, [6451] = { 640, 125 }, [8544] = { 800, 150 }, [8545] = { 1104, 175 },
  [14529] = { 1360, 200 }, [14530] = { 2000, 225 } }
local BANDAGED_IDS = { [11196] = true, [1305047] = true }    -- « Bandage appliqué récemment » (1 min)
local BANDAGE_SEC = 8
-- munitions de marchand par palier : { objet, niveau d'objet, niveau requis, prix des 200 (pc), nom de repli }
local AMMO = {
  arrow = { { 2512, 5, 1, 10, "Flèche grossière" }, { 2515, 15, 10, 50, "Flèche pointue" },
    { 3030, 30, 25, 300, "Flèche rasoir" }, { 11285, 45, 40, 1000, "Flèche barbelée" } },
  bullet = { { 2516, 5, 1, 10, "Balle légère" }, { 2519, 15, 10, 50, "Balle lourde" },
    { 3033, 30, 25, 300, "Balle dure" }, { 11284, 45, 40, 1000, "Balles de précision" } },
}
local AMMO_DPS = { [5] = 1.82, [15] = 3.62, [30] = 7.64, [45] = 13.03 }   -- ItemDamageAmmo (qualité commune)
local GROWL = { [2649] = true, [14916] = true, [14917] = true, [14918] = true, [14919] = true, [14920] = true,
  [14921] = true, [14922] = true, [1853] = true }
local SP = { CHEETAH = 5118, PACK = 13159, GROWL = 2649, CALL = 883, FEIGN = 5384, RAPID = 3045, FREEZING = 1499 }
-- sorts clés du chasseur : { sort, niveau, thème, ligne « comment s'en servir » } (noms lus dans le client)
local TOOLS = {
  { 5116, 8, "pull", "ralentit un monstre qui fonce sur toi ou un fuyard" },
  { 136, 12, "pet", "soigne-le pendant que tu manges : il tient le pull suivant" },
  { 2974, 12, "pull", "au contact : ralentis-le puis recule pour tirer" },
  { 1513, 14, "pull", "une 2e bête arrive : effraie-la, tue la 1re" },
  { 13795, 16, "pull", "pose-le puis tire : la cible marche dedans" },
  { 2643, 18, "pull", "sur 2-3 monstres tenus par ton familier" },
  { 19434, 20, "pull", "en ouverture pendant que la cible arrive" },
  { 1499, 20, "safety", "un add arrive : pose-le sur son chemin, tue le 1er" },
  { 781, 20, "pull", "au contact : baisse ta menace, le familier reprend l'aggro" },
  { 5118, 20, "quest", "pour marcher : +30 % de vitesse, Faucon avant de tirer" },
  { 3045, 26, "pull", "pour les élites : +40 % de vitesse de tir 15 s" },
  { 13809, 28, "pull", "pose-le avant de tirer sur un groupe : ils arrivent ralentis" },
  { 5384, 30, "safety", "les monstres t'oublient : lâche des adds ou un pull raté" },
  { 13813, 34, "pull", "sous un pack, avant Flèches multiples" },
  { 1510, 40, "pull", "packs en donjon : grosse zone, chère en mana" },
}
-- XP d'une quête rendue trop tard (Classic, à vérifier sur Forever) : écart joueur − quête → part de l'XP
local QMUL = { [6] = 0.8, [7] = 0.6, [8] = 0.4, [9] = 0.2 }
local function qmul(d) if d <= 5 then return 1 end return QMUL[d] or 0.1 end

-- ===================================================================================== module
local defaults = { enabled = true, nowLine = true, card = true }
local options = { { key = "nowLine", type = "toggle", label = "Montrer le meilleur conseil dans « Maintenant » (sinon : page seulement)",
  section = "Affichage" },
  { key = "card", type = "toggle", label = "Aussi en tête de la carte « Prochaine étape » (si aucun autre avis n'y est)" } }
for _, tp in ipairs(TIPS) do
  defaults[tp.key] = true
  options[#options + 1] = { key = tp.key, type = "toggle", label = tp.label .. (tp.hunter and " (chasseur)" or ""),
    section = THEME[tp.theme].label }
end
local M = T:RegisterModule("Coach", {
  title = "Conseils pro",
  description = "Le geste d'un joueur expert au bon moment : un conseil à la fois, avec le pourquoi en chiffres. "
    .. "Page « Conseils pro » de la fenêtre TreeAI : tous les conseils (ON / OFF) et les techniques à appliquer.",
  defaults = defaults,
  charDefaults = { tools = {} },
  options = options,
})
T.Coach = M
M.TIPS, M.TECH, M.THEMES, M.XPFOOD, M.AMMO, M.TOOLS = TIPS, TECH, THEMES, XPFOOD, AMMO, TOOLS
M.LOGIN_QUIET = 45   -- s après la connexion : on laisse passer les avis du démarrage
M.MIN_SHOW = 20      -- s : un conseil reste au moins ce temps (sauf s'il devient faux ou qu'un urgent arrive)
M.GAP = 15           -- s entre deux conseils différents
M.MAX_10MIN = 6      -- conseils différents montrés au plus par tranche de 10 min

-- ===================================================================================== utilitaires
local function g(name) return rawget(G, name) end
local function pass(ok, ...) if ok then return ... end return nil end
local function try(fn, ...) if type(fn) ~= "function" then return nil end return pass(pcall(fn, ...)) end
-- (coût) issecretvalue relu une fois par passage, appelé sans pcall (il ne lève jamais) ; lectures du joueur directes
local issecret = rawget(G, "issecretvalue")
local function refreshSecret() local f = rawget(G, "issecretvalue") issecret = type(f) == "function" and f or nil end
refreshSecret()
local function secret(v) return v ~= nil and issecret ~= nil and issecret(v) and true or false end
local function sv(v) if v == nil or (issecret and issecret(v)) then return nil end return v end
local function num(v) if v == nil or (issecret and issecret(v)) then return nil end return tonumber(v) end
local function now() local f = G.GetTime return f and f() or 0 end
local function call1(name, a) local f = G[name] if type(f) == "function" then return f(a) end end
local function opt(k) return M.db and M.db[k] ~= false end
-- état de combat : suivi par PLAYER_REGEN_DISABLED / ENABLED (relu au jeu une fois par passage, et à l'activation)
local fighting = false
local function readCombat()
  local f = G.InCombatLockdown
  local ok, v = false, false
  if type(f) == "function" then ok, v = pcall(f) end
  fighting = (ok and v) and true or false
  return fighting
end
local function inCombat() return fighting end
-- passage d'évaluation : les lectures (vie, auras, état) sont faites une fois par passage
local passN, passT = 0, 0
local function newPass() passN, passT = passN + 1, now() readCombat() end
local function level() return num(call1("UnitLevel", "player")) or 1 end
local CLASS
local function hunter()
  if CLASS == nil then CLASS = (T.PlayerClass and T:PlayerClass()) or false end
  return CLASS == "HUNTER"
end
local function groupN() return num(call1("GetNumGroupMembers")) or 0 end
local function inInstance()
  local a, k = try(g("IsInInstance"))
  a, k = sv(a), sv(k)
  return (a and k ~= "none") and true or false, k
end
local stPass, stCache = -1, nil
local function pstate()
  if stPass ~= passN or not stCache then stCache, stPass = T.PlayerState and T:PlayerState() or {}, passN end
  return stCache
end
local function knows(id) return API.IsSpellKnown and API.IsSpellKnown(id) or false end
local function spell(id, fb) local n = API.SpellName and API.SpellName(id) return (type(n) == "string" and n ~= "") and n or fb end
local function itemName(id, fb)
  local n = API.ItemName and API.ItemName(id)
  return (type(n) == "string" and n ~= "" and not secret(n)) and n or fb or ("objet " .. tostring(id))
end
local function cut(s, n) return T.Cut and T.Cut(s, n) or tostring(s or "") end
local function pct(a, b) if a and b and b > 0 then return floor(a * 100 / b + 0.5) end end
local Fmt = T.Fmt or {}
local function nfmt(n) return Fmt.Num and Fmt.Num(n) or tostring(floor((tonumber(n) or 0) + 0.5)) end
local function money(c) return Fmt.Money and Fmt.Money(c) or (tostring(c) .. " pc") end
local function dec(v) return (tostring(floor(v * 10 + 0.5) / 10):gsub("%.", ",")) end
local function push(list, e, cap) list[#list + 1] = e if #list > cap then tremove(list, 1) end end
local function mod(n) local m = T.modules and T.modules[n] return (m and m._active) and m or nil end
local function petAlive()
  return try(g("UnitExists"), "pet") and not try(g("UnitIsDead"), "pet") and true or false
end
local function hostileTarget()
  return try(g("UnitExists"), "target") and try(g("UnitCanAttack"), "player", "target")
    and not try(g("UnitIsDead"), "target") and not try(g("UnitIsPlayer"), "target") and true or false
end
M.Q = { mul = qmul }

-- propriétaire déjà à l'écran ? La barre de combat (QuickFight) a son bouton manger / bandage / aspect, la barre de
-- quête (Quick) un bouton monture qui propose le Guépard : on ne redit pas leur conseil
local function qfCovers(key)
  local Q = mod("QuickFight")
  return (Q and Q.db and Q.db.bar ~= false and Q.db[key] ~= false) and true or false
end
local function quickMount()
  local Q = mod("Quick")
  return (Q and Q.db and Q.db.bar ~= false and Q.db.btnMount ~= false) and true or false
end
M.QFCovers = qfCovers

-- ===================================================================================== lectures (hors combat)
-- vie / mana : dernière lecture HORS combat (Forever : secrète en combat et en instance)
local V = { at = -1e9 }
M.V = V
local function readVitals()
  if inCombat() or V.pass == passN then return V end
  V.pass = passN
  local hp, mx = num(call1("UnitHealth", "player")), num(call1("UnitHealthMax", "player"))
  V.hp, V.hpMax, V.hpP = hp, mx, pct(hp, mx)
  local fp = G.UnitPower
  if num(call1("UnitPowerType", "player")) == 0 and type(fp) == "function" then
    local fpm = G.UnitPowerMax
    local mp, mpx = num(fp("player", 0)), type(fpm) == "function" and num(fpm("player", 0)) or nil
    V.mana, V.mp, V.mpMax, V.mpP = true, mp, mpx, pct(mp, mpx)
  else
    V.mana, V.mp, V.mpMax, V.mpP = false, nil, nil, nil
  end
  V.at = now()
  return V
end
M.ReadVitals = readVitals

-- auras du joueur : repas, boisson, Bien nourri, aspect de voyage, bandage récent (« illisible » ≠ « absent »)
local A = { at = -1e9 }
local EATN, DRINKN, BANDN, TRAVELN, WFXPN
local function auraNames()
  if EATN then return end
  EATN, DRINKN, BANDN, TRAVELN = { Nourriture = true, Food = true }, { Boisson = true, Drink = true },
    { ["Recently Bandaged"] = true }, {}
  for _, id in ipairs({ 433, 1248377 }) do local n = spell(id) if n then EATN[n] = true end end      -- Nourriture, Denrée nutritive
  for _, id in ipairs({ 430, 1249906 }) do local n = spell(id) if n then DRINKN[n] = true end end    -- Boisson, Boisson nutritive
  for id in pairs(BANDAGED_IDS) do local n = spell(id) if n then BANDN[n] = true end end
  for _, id in ipairs({ SP.CHEETAH, SP.PACK }) do local n = spell(id) if n then TRAVELN[n] = true end end
  WFXPN = spell(WELLFED_XP)
end
local function readAuras(force)
  if not force and A.pass == passN then return A end
  local t = now()
  auraNames()
  A.pass = passN
  A.at, A.eat, A.drink, A.eatLeft, A.eatDur, A.drinkLeft, A.drinkDur, A.wf, A.travel, A.band = t, nil, nil, nil, nil, nil, nil, nil, nil, nil
  local list, hidden = {}, 0
  if API.Auras then list, hidden = API.Auras("player", "HELPFUL") end
  for _, a in ipairs(list or {}) do
    local nm, id = a.name, a.spellId
    local left = (tonumber(a.expires) or 0) > 0 and (a.expires - t) or nil
    local dur = tonumber(a.duration)
    if dur and dur <= 0 then dur = nil end                       -- 0 = sans durée : on garde le repli (18 s)
    if nm and EATN[nm] then A.eat, A.eatDur, A.eatLeft = true, dur, left end
    if nm and DRINKN[nm] then A.drink, A.drinkLeft, A.drinkDur = true, left, dur end
    if id == WELLFED_XP or (WFXPN and nm == WFXPN) then A.wf = true end
    if id == SP.CHEETAH or id == SP.PACK or (nm and TRAVELN[nm]) then A.travel = true end
  end
  A.hidden = hidden or 0
  if API.Auras then
    for _, a in ipairs((API.Auras("player", "HARMFUL")) or {}) do
      if BANDAGED_IDS[a.spellId or 0] or (a.name and BANDN[a.name]) then A.band = true end
    end
  end
  return A
end
M.ReadAuras = readAuras

-- sacs (mémo jusqu'au prochain changement de sacs) : plat XP, bandage utilisable, boisson / repas, cases vides
local B = { gen = -1, at = -1e9 }
local function isFirstAid(name, line)
  return line == 129 or line == 2942 or (type(name) == "string" and (name:find("ecourisme", 1, true)
    or name:find("Premiers soins", 1, true) or name:find("First Aid", 1, true))) or false
end
local function firstAidRank()
  local p = { try(g("GetProfessions")) }
  for i = 1, 6 do
    local idx = num(p[i])
    if idx then
      local name, _, rank, _, _, _, line = try(g("GetProfessionInfo"), idx)
      if isFirstAid(sv(name), num(line)) then return num(rank) end
    end
  end
  for _, l in ipairs(API.SkillLines and API.SkillLines() or {}) do     -- repli : lignes de compétence (Era / C_SkillInfo)
    if not l.isHeader and isFirstAid(sv(l.name), num(l.id)) then return num(l.rank) end
  end
  return nil
end
local function usable(id)
  local CI = g("C_Item")
  if CI and CI.IsUsableItem then
    local ok = try(CI.IsUsableItem, id)
    if ok == false then return false end
  end
  return true
end
local function consumableSet(kind)
  local D = T.Data and T.Data.Consumables
  local set = {}
  for _, r in ipairs(D and D[kind] or {}) do if type(r) == "table" and r[1] then set[r[1]] = r[2] or 1 end end
  return set
end
local WATER, FOOD
local function readBags()
  local gen = API.BagGen and API.BagGen() or 0
  local t = now()
  if gen == B.gen and t - B.at < 30 then return B end
  B.gen, B.at = gen, t
  B.xpfood, B.xpfoodN, B.band, B.drink, B.food, B.empty = nil, 0, nil, nil, nil, 0
  for bag = 1, (API.NumBags and API.NumBags() or 4) do
    if API.BagSlots and API.BagSlots(bag) == 0 then B.empty = B.empty + 1 end
  end
  WATER, FOOD = WATER or consumableSet("water"), FOOD or consumableSet("food")
  local lvl = level()
  local rank = firstAidRank()
  if API.IterBags then
    for _, _, it in API.IterBags() do
      local id = it and it.itemID
      if id then
        local req = XPFOOD[id]
        if req and req <= lvl then
          if not B.xpfood or req > (XPFOOD[B.xpfood] or 0) then B.xpfood = id end
          B.xpfoodN = B.xpfoodN + (it.count or 1)
        end
        local bd = BANDAGE[id]
        if bd and (not rank or rank >= bd[2]) and (not B.band or bd[1] > BANDAGE[B.band][1]) and usable(id) then B.band = id end
        if WATER[id] and WATER[id] <= lvl and not B.drink then B.drink = id end
        if (FOOD[id] or XPFOOD[id]) and (FOOD[id] or XPFOOD[id]) <= lvl and not B.food then B.food = id end
      end
    end
  end
  return B
end
M.ReadBags = readBags

-- barre du familier : Grondement (automatique ?), mode (agressif / défensif / passif)
local P = { at = -1e9 }
local function readPetBar(force)
  local t = now()
  if not force and P.ok ~= nil and t - P.at < 30 then return P end
  P.at, P.ok, P.growl, P.growlAllowed, P.growlAuto, P.mode = t, false, nil, nil, nil, nil
  local fn = g("GetPetActionInfo")
  if type(fn) ~= "function" or not try(g("UnitExists"), "pet") then return P end
  local gname = spell(SP.GROWL)
  for i = 1, 10 do
    -- Forever (Blizzard_ActionBar/Shared/PetActionBar.lua) : name, texture, isToken, isActive, autoCastAllowed,
    -- autoCastEnabled, spellID
    local name, _, isToken, isActive, allowed, enabled, sid = try(fn, i)
    name, isToken, isActive, allowed, enabled, sid = sv(name), sv(isToken), sv(isActive), sv(allowed), sv(enabled), num(sid)
    if type(name) == "string" then
      if isToken then
        if isActive and name:find("^PET_MODE_") then P.mode = name:sub(10):lower() end
      elseif (sid and GROWL[sid]) or (gname and name == gname) then
        P.growl, P.growlAllowed, P.growlAuto = true, allowed and true or false, enabled and true or false
      end
    end
  end
  P.ok = true
  return P
end
M.ReadPetBar = readPetBar

-- ===================================================================================== mesures (session)
-- combats : vie / mana au pull (dernière lecture hors combat) et à la fin (lisible à nouveau) ; morts par le message
-- d'XP (Detect, seul lecteur) + écart d'XP (PLAYER_XP_UPDATE) = XP de base d'un monstre, repos à part
local fights, kills, eats = {}, {}, {}
M.fights, M.kills, M.eats = fights, kills, eats
local fight, lastTL, lastXP, lastMax, lastExh, pendKill, pendXP
local movingSince, walkSec = nil, 0

function M.FightStart()
  local t = now()
  local fresh = t - (V.at or -1e9) <= 15
  fight = { t0 = t, hp0 = fresh and V.hpP or nil, mp0 = fresh and V.mana and V.mpP or nil, pet = petAlive(), grp = groupN() }
  if try(g("UnitCanAttack"), "player", "target") then
    local lv = num(try(g("UnitLevel"), "target"))
    if lv and lv > 0 then fight.tl = lv end
    local c = sv(try(g("UnitClassification"), "target"))
    fight.elite = (c == "elite" or c == "rareelite" or c == "worldboss") and true or nil
  end
  if fight.tl then lastTL = fight.tl end
  if movingSince then walkSec, movingSince = walkSec + max(0, t - movingSince), nil end
end
function M.FightEnd()
  local f = fight
  fight = nil
  if not f then return end
  local st = pstate()
  if st.dead or st.ghost then return end
  passN = passN + 1
  readVitals()
  local t = now()
  if f.hp0 and V.hpP and t - f.t0 >= 2 then
    f.dur, f.loss = t - f.t0, max(0, f.hp0 - V.hpP)
    if f.mp0 and V.mpP then f.mloss = max(0, f.mp0 - V.mpP) end
    push(fights, f, 12)
  end
end
local function matchKill(k, x)
  k.base, k.rested = x.base, x.rested
  push(kills, k, 12)
end
function M.OnXP()
  local xp, mx = num(try(g("UnitXP"), "player")), num(try(g("UnitXPMax"), "player"))
  local ex = num(try(g("GetXPExhaustion"))) or 0
  if xp and lastXP then
    local d = xp - lastXP
    if d < 0 and lastMax then d = d + lastMax end
    if d > 0 and d < 200000 then
      local used = (lastExh and ex < lastExh) and (lastExh - ex) or 0
      local x = { t = now(), base = max(0, d - used), rested = used }
      if pendKill and x.t - pendKill.t <= 1.5 then matchKill(pendKill, x) pendKill = nil else pendXP = x end
    end
  end
  lastXP, lastMax, lastExh = xp or lastXP, mx or lastMax, ex
end
function M.OnKill(data)
  local k = { t = now(), quest = type(data) == "table" and data.objective ~= nil or false, lvl = lastTL, grp = groupN() }
  if pendXP and k.t - pendXP.t <= 1.5 then matchKill(k, pendXP) pendXP = nil else pendKill = k end
end
-- repas : durée et vie rendue (pour chiffrer un combat en secondes de repas)
local eating
local function trackEat()
  local a = readAuras(true)
  local t = now()
  if a.eat and not eating then
    readVitals()
    eating = { t0 = t, hp0 = V.hpP, dur = a.eatDur }
  elseif not a.eat and eating then
    readVitals()
    if eating.hp0 and V.hpP and V.hpP > eating.hp0 and t - eating.t0 >= 3 then
      push(eats, { sec = t - eating.t0, gain = V.hpP - eating.hp0, dur = eating.dur }, 8)
    end
    eating = nil
  end
end

--- vie perdue moyenne (en % de ta vie) sur les combats qui passent le filtre, s'il y en a au moins n
local function avgLoss(filter, n)
  local s, k = 0, 0
  for i = #fights, 1, -1 do
    local f = fights[i]
    if f.loss and (not filter or filter(f)) then s, k = s + f.loss, k + 1 end
  end
  if k >= (n or 5) then return s / k, k end
end
M.AvgLoss = avgLoss
local function avgManaLoss(n)
  local s, k = 0, 0
  for i = #fights, 1, -1 do local f = fights[i] if f.mloss then s, k = s + f.mloss, k + 1 end end
  if k >= (n or 5) then return s / k, k end
end
--- secondes de repas pour rendre `p` % de vie : mesuré sur tes repas, sinon ≈ un repas (18 s) pour 100 %
local function eatSecFor(p)
  local s, gsum = 0, 0
  for _, e in ipairs(eats) do s, gsum = s + e.sec, gsum + e.gain end
  if #eats >= 2 and gsum > 0 then return p * s / gsum, true end
  return p * 18 / 100, false
end
--- XP de base d'un monstre de ton niveau : mesurée (médiane de tes kills solo à ±1 niveau) ou formule Classic
local function sameLevelXP(lvl)
  local vals = {}
  for _, k in ipairs(kills) do
    if k.base and k.base > 0 and k.lvl and math.abs(k.lvl - lvl) <= 1 and (k.grp or 0) <= 1 then vals[#vals + 1] = k.base end
  end
  if #vals >= 3 then
    table.sort(vals)
    return vals[floor((#vals + 1) / 2)], true
  end
  return lvl * 5 + 45, false
end
M.SameLevelXP = sameLevelXP
--- XP/h tirée des monstres (base + repos), mesurée sur ta session (≥ 10 min et 8 monstres) ; nil sinon
local function killXPPerHour()
  if #kills < 8 then return nil end
  local span = kills[#kills].t - kills[1].t
  if span < 600 then return nil end
  local s = 0
  for i = 2, #kills do s = s + (kills[i].base or 0) + (kills[i].rested or 0) end
  return s * 3600 / span
end

-- ===================================================================================== moteur pur (testable)
local E = {}
M.Engine = E
--- quêtes finies : n, XP total, couvre le niveau ?
function E.TurnIn(list, need)
  local n, xp = 0, 0
  for _, q in ipairs(list or {}) do
    if q.complete and tonumber(q.xp) then n, xp = n + 1, xp + q.xp end
  end
  return n, xp, (need or 0) > 0 and xp >= need
end
--- quête finie qui perd le plus d'XP au niveau suivant (règle Classic) -> quête, perte, part perdue (0-1)
--- nouveaux sorts chez le maître de classe au niveau lvl ? (règle Classic, la même que Guichets : niveaux pairs)
function E.TrainAt(lvl) lvl = tonumber(lvl) or 0 return lvl >= 2 and lvl % 2 == 0 end

function E.Penalty(list, lvl)
  local best, bl, bp
  for _, q in ipairs(list or {}) do
    local ql, qx = tonumber(q.level), tonumber(q.xp)
    if q.complete and ql and ql > 0 and qx and qx > 0 then
      local d = lvl - ql
      if d >= 5 and d <= 9 then
        local part = 1 - qmul(d + 1) / qmul(d)
        local loss = floor(qx * part + 0.5)
        if loss >= 150 and (not bl or loss > bl) then best, bl, bp = q, loss, part end
      end
    end
  end
  return best, bl, bp
end
--- palier de munitions : objet équipé -> type, palier actuel, meilleur palier permis au niveau
function E.AmmoTier(ammoID, lvl)
  for kind, list in pairs(AMMO) do
    for i, r in ipairs(list) do
      if r[1] == ammoID then
        local best = i
        for j = #list, i + 1, -1 do if list[j][3] <= lvl then best = j break end end
        return kind, i, best
      end
    end
  end
  return nil
end

-- ===================================================================================== conseils (chaque test rend
-- nil ou { text, why = {…}, score, urgent, ttl, sig, kind, topic })
local CHECK = {}
M.CHECK = CHECK
local function quests() local Q = T.modules and T.modules.Quests return (Q and Q.lastList) or {} end

CHECK.wellFed = function()
  if level() >= 60 then return nil end
  local a = readAuras()
  if a.wf or (a.hidden or 0) > 0 then return nil end
  local b = readBags()
  local id = b.xpfood
  if not id then return nil end
  local v = readVitals()
  local soon = (v.hpP and v.hpP < 75) or a.eat
  local name = itemName(id)
  local why = { "Forever : un plat cuisiné mangé au moins 10 s donne « Bien nourri » ET +5 % d'XP sur chaque monstre tué (pas sur les quêtes).",
    fmt("Dans tes sacs : %s ×%d.", name, b.xpfoodN or 1) }
  local xph = killXPPerHour()
  if xph then why[#why + 1] = fmt("Tes monstres : ≈ %s XP/h mesurés, donc ≈ +%s XP/h.", nfmt(xph), nfmt(xph * 0.05)) end
  if soon then why[#why + 1] = "Tu vas manger de toute façon : autant que ce soit lui." end
  return { text = fmt("Mange « %s » 10 s : Bien nourri, +5 %% d'XP sur les monstres", cut(name, 28)), why = why,
    score = 60 * (soon and 1.4 or 1), urgent = soon, sig = id }
end

CHECK.eatDrink = function()
  if qfCovers("eat") then return nil end
  local a, v = readAuras(), readVitals()
  local b = readBags()
  if a.eat and not a.drink and v.mana and v.mpP and v.mpP < 60 and b.drink then
    local left = a.eatLeft or a.eatDur or 18
    if left < 6 then return nil end
    return { text = fmt("Bois aussi pendant que tu manges (« %s ») : ≈ %d s de repos en moins", cut(itemName(b.drink), 26),
        floor((a.eatDur or 18) + 0.5)),
      why = { fmt("Mana à %d %% : tu boirais juste après ton repas.", v.mpP),
        "Repas et boisson tournent en même temps : assis une fois au lieu de deux." },
      score = 75, urgent = true, ttl = max(8, min(20, left)), sig = "d" }
  elseif a.drink and not a.eat and v.hpP and v.hpP < 60 and b.food then
    local left = a.drinkLeft or 18
    if left < 6 then return nil end
    return { text = fmt("Mange aussi pendant que tu bois (« %s ») : ≈ %d s de repos en moins", cut(itemName(b.food), 26),
        floor((a.drinkDur or 18) + 0.5)),
      why = { fmt("Vie à %d %% : tu mangerais juste après ta boisson.", v.hpP),
        "Repas et boisson tournent en même temps : assis une fois au lieu de deux." },
      score = 75, urgent = true, ttl = max(8, min(20, left)), sig = "f" }
  end
  return nil
end

CHECK.bandage = function()
  if qfCovers("bandage") then return nil end
  local v, a = readVitals(), readAuras()
  if not (v.hp and v.hpMax and v.hpP and v.hpP < 85) or a.band or a.eat or a.drink then return nil end
  if v.mana and v.mpP and v.mpP < 50 then return nil end      -- tu vas boire : manger en même temps vaut mieux
  local b = readBags()
  local id = b.band
  if not id then return nil end
  local heal = BANDAGE[id][1]
  local miss = v.hpMax - v.hp
  if miss < heal * 0.5 or miss > heal * 1.25 then return nil end
  local foodSec = 18
  if #eats > 0 and eats[#eats].dur then foodSec = floor(eats[#eats].dur + 0.5) end
  return { text = fmt("Bande-toi au lieu de manger : %s rend %d PV en ≈ %d s", cut(itemName(id), 30), heal, BANDAGE_SEC),
    why = { fmt("Il te manque %d PV : ce bandage les rend presque tous (tables du client Forever).", miss),
      fmt("Un repas dure ≈ %d s : ≈ %d s gagnées.", foodSec, max(0, foodSec - BANDAGE_SEC)),
      "Ne bouge pas pendant le bandage ; ensuite 1 min avant le suivant." },
    score = 55, urgent = true, ttl = 25, sig = id }
end

CHECK.drinkNow = function()
  if qfCovers("eat") then return nil end
  local v, a = readVitals(), readAuras()
  if not (v.mana and v.mpP) or a.drink or v.mpP >= 60 then return nil end
  local b = readBags()
  if not b.drink then return nil end
  local ml, n = avgManaLoss(5)
  if not ml or ml < 8 or v.mpP >= ml * 1.3 then return nil end
  local left = v.mpP >= ml and "≈ 1 combat" or "moins d'un combat"
  return { text = fmt("Bois avant le pull : il te reste %s de mana (−%d %% par combat, mesuré)", left, floor(ml + 0.5)),
    why = { fmt("Mesuré sur tes %d derniers combats : −%d %% de mana chacun ; tu es à %d %%.", n, floor(ml + 0.5), v.mpP),
      hunter() and "À court de mana en plein combat : plus de Morsure de serpent ni de Tir des arcanes, le combat traîne et coûte de la vie."
        or "À court de mana en plein combat : plus de sorts, le combat traîne et coûte de la vie.",
      fmt("Boisson dans tes sacs : %s.", itemName(b.drink)) },
    score = 52, ttl = 30 }
end

CHECK.chainPull = function()
  local a, v = readAuras(), readVitals()
  if not (a.eat and v.hpP and v.hpP >= 70) or (v.mana and v.mpP and v.mpP < 50) then return nil end
  local loss, n = avgLoss(function(f) return not f.elite end, 5)
  if not loss or loss < 3 then return nil end
  local k = floor((v.hpP - 25) / loss)
  if k < 2 then return nil end
  local sec = floor((a.eatDur or 18) + 0.5)
  return { text = fmt("Enchaîne : à %d %% de vie tu tiens %d combats (−%d %% par combat, mesuré)", v.hpP, min(k, 9), floor(loss + 0.5)),
    why = { fmt("Mesuré sur tes %d derniers combats (monstres normaux) : −%d %% de vie en moyenne.", n, floor(loss + 0.5)),
      fmt("Ce repas coûte ≈ %d s ; marge gardée : 25 %% de vie.", sec), "Mange quand ta vie ne tient plus qu'un combat." },
    score = 35, ttl = 30 }
end

CHECK.petTank = function()
  if not (hunter() and petAlive()) then return nil end
  local loss, n = avgLoss(function(f) return f.pet and (f.grp or 0) <= 2 and not f.elite end, 6)
  if not loss or loss < 30 then return nil end
  local pb = readPetBar()
  if pb.growl and pb.growlAllowed and not pb.growlAuto then return nil end   -- « Grondement en auto » le dit mieux
  local sec, measured = eatSecFor(loss)
  return { text = fmt("Laisse ton familier prendre l'aggro 2 s avant de tirer : tu perds %d %% de vie par combat", floor(loss + 0.5)),
    why = { fmt("Mesuré sur tes %d derniers combats avec ton familier : −%d %% de vie en moyenne (lu hors combat).", n, floor(loss + 0.5)),
      "Envoie-le (Marque + familier), attends 2 morsures, puis Morsure de serpent et tir auto : il garde l'aggro.",
      fmt("Chaque combat te coûte %s%d s de repas%s.", measured and "" or "≈ ", floor(sec + 0.5),
        measured and " (mesuré sur tes repas)" or "") },
    score = 65 * min(2, loss / 30), ttl = 60 }
end

CHECK.greenMobs = function()
  local lvl = level()
  if #kills < 5 then return nil end
  local s, lo, t = 0, 99, now()
  for i = #kills - 4, #kills do
    local k = kills[i]
    if k.quest or not k.lvl or not k.base or k.base <= 0 or (k.grp or 0) > 1 or lvl - k.lvl < 5 or t - k.t > 600 then return nil end
    s, lo = s + k.base, min(lo, k.lvl)
  end
  local avg = floor(s / 5 + 0.5)
  local ref, measured = sameLevelXP(lvl)
  if avg > ref * 0.6 then return nil end
  return { text = fmt("Monstres trop faibles (niv %d) : %d XP chacun, vise les niv %d à %d (%s%d XP)", lo, avg, lvl - 2, lvl + 2,
      measured and "" or "≈ ", ref),
    why = { fmt("Tes 5 derniers monstres hors quêtes : %d XP de base chacun (repos à part).", avg),
      measured and fmt("Un monstre de ton niveau : %d XP (mesuré sur tes kills).", ref)
        or fmt("Un monstre de ton niveau : ≈ %d XP (formule Classic 5 × niveau + 45).", ref),
      "Classic : l'XP baisse à chaque niveau d'écart sous le tien et tombe à 0 sur les monstres gris." },
    score = 50, ttl = 45 }
end

CHECK.groupXP = function()
  local n = groupN()
  if n < 3 or inInstance() or #kills < 6 then return nil end
  local s, t = 0, now()
  for i = #kills - 5, #kills do
    local k = kills[i]
    if k.quest or not k.base or k.base <= 0 or (k.grp or 0) < 3 or t - k.t > 600 then return nil end
    s = s + k.base
  end
  local avg = floor(s / 6 + 0.5)
  local ref = sameLevelXP(level())
  if avg > ref * 0.5 then return nil end
  return { text = fmt("À %d, ces monstres hors quête vous rapportent %d XP chacun : allez sur vos quêtes", n, avg),
    why = { fmt("Mesuré sur vos 6 derniers monstres hors quêtes : %d XP chacun (un monstre seul : ≈ %d).", avg, ref),
      "En groupe l'XP d'un monstre se partage ; les monstres de quête font en plus avancer les quêtes de chacun." },
    score = 30, ttl = 45, sig = n }
end

local function toolOf(id) for _, tl in ipairs(TOOLS) do if tl[1] == id then return tl end end end
CHECK.newTool = function()
  if not hunter() then return nil end
  local c = M.char
  if type(c) ~= "table" then return nil end
  c.tools = type(c.tools) == "table" and c.tools or {}
  local fresh, best = not c.toolsInit, nil
  for _, tl in ipairs(TOOLS) do
    if not c.tools[tl[1]] and knows(tl[1]) then
      if fresh then c.tools[tl[1]] = true                 -- 1re fois : ce que tu sais déjà ne t'est pas « appris »
      elseif not best or tl[2] > best[2] then best = tl end
    end
  end
  c.toolsInit = true
  if not best then return nil end
  local name = spell(best[1], "ce sort")
  return { text = fmt("Nouveau : « %s » — %s", name, best[4]),
    why = { fmt("« %s » : sort de chasseur du niveau %d (maître de classe).", name, best[2]), "Technique complète : page « Conseils pro », thème « "
      .. THEME[best[3]].label .. " »." },
    score = 66, ttl = 120, sig = best[1], onShown = function() c.tools[best[1]] = true end }
end

CHECK.growlAuto = function()
  if not (hunter() and petAlive()) or groupN() > 2 or inInstance() then return nil end
  local pb = readPetBar()
  if not (pb.growl and pb.growlAllowed) or pb.growlAuto then return nil end
  local gname = spell(SP.GROWL, "Grondement")
  local why = { fmt("« %s » = la provocation de ton familier : il tient les monstres, tu tires sans prendre de coups.", gname),
    "Solo et duo : en automatique. En donjon avec un tank : coupé." }
  local loss, n = avgLoss(function(f) return f.pet end, 4)
  if loss then why[#why + 1] = fmt("Tu perds en ce moment %d %% de vie par combat (sur %d combats).", floor(loss + 0.5), n) end
  return { text = fmt("Mets « %s » en automatique (clic droit sur son bouton) : ton familier garde l'aggro", gname),
    why = why, score = 66, ttl = 60, sig = "g" }
end

local TANKS = { WARRIOR = true, PALADIN = true, DRUID = true }
local function groupTank()
  for i = 1, 4 do
    local u = "party" .. i
    if try(g("UnitExists"), u) then
      local role = sv(try(g("UnitGroupRolesAssigned"), u))
      local _, cls = try(g("UnitClass"), u)
      cls = sv(cls)
      if role == "TANK" or ((role == nil or role == "NONE") and cls and TANKS[cls]) then
        local nm = sv(try(g("UnitName"), u))
        return type(nm) == "string" and nm or "ton tank"
      end
    end
  end
end
CHECK.growlDungeon = function()
  if not (hunter() and petAlive()) then return nil end
  local inst, kind = inInstance()
  if not inst or (kind ~= "party" and kind ~= "raid") or groupN() < 3 then return nil end
  local tank = groupTank()
  if not tank then return nil end
  local pb = readPetBar()
  if not (pb.growl and pb.growlAuto) then return nil end
  local gname = spell(SP.GROWL, "Grondement")
  return { text = fmt("Coupe « %s » en donjon (clic droit dessus) : il vole l'aggro à %s", gname, cut(tank, 16)),
    why = { "Avec un tank, la provocation du familier lui prend les monstres : le pack se retourne sur ton familier puis sur le soigneur.",
      "Remets-le en automatique en sortant." },
    score = 80, urgent = true, ttl = 90, sig = "d" }
end

CHECK.petDefensive = function()
  if not (hunter() and petAlive()) or not inInstance() or groupN() < 2 then return nil end
  local pb = readPetBar()
  if pb.mode ~= "aggressive" then return nil end
  return { text = "Passe ton familier en défensif : en agressif il attaque des packs tout seul",
    why = { "En agressif, il attaque tout monstre qui approche : un 2e pack arrive sur le groupe.",
      "Défensif : il frappe ce qui t'attaque ou ce que tu lui désignes." },
    score = 80, urgent = true, ttl = 90, sig = "a" }
end

CHECK.petLevel = function()
  if not (hunter() and petAlive()) then return nil end
  local pl, me = num(try(g("UnitLevel"), "pet")), level()
  if not pl or pl <= 0 or me - pl < 3 then return nil end
  return { text = fmt("Ton familier a %d niveaux de retard (niv %d) : fais-le tuer des monstres de ton niveau", me - pl, pl),
    why = { "Forever : ton familier ne gagne de l'XP que sur les monstres tués (pas sur les quêtes) et s'arrête à ton niveau.",
      "Plus bas que ses cibles, il rate plus et tient moins l'aggro (règles de niveau, Classic).",
      "Autre choix : apprivoiser une bête de ton niveau (Chercher → Familier)." },
    score = 45, ttl = 60, sig = pl }
end

CHECK.restedKill = function()
  local lvl = level()
  if lvl >= 60 then return nil end
  local st = pstate()
  if st.resting or inInstance() then return nil end
  local rested = num(try(g("GetXPExhaustion"))) or 0
  local mx = num(try(g("UnitXPMax"), "player")) or 0
  if rested <= 0 or mx <= 0 or rested < mx * 0.2 then return nil end
  local per, measured = sameLevelXP(lvl)
  local n = floor(rested / max(1, per))
  if n < 8 then return nil end
  local full = rested >= mx * 1.48
  local nTxt = (measured and "" or "≈ ") .. nfmt(n)
  return { text = full and fmt("Repos au max : tes %s prochains monstres valent double, tue ceux de ta route", nTxt)
      or fmt("Repos : tes %s prochains monstres valent double, tue ceux sur ton chemin", nTxt),
    why = { fmt("Repos : %s XP de bonus (%d %% d'un niveau) ; il double l'XP des monstres, pas celle des quêtes.", nfmt(rested),
        floor(rested * 100 / mx)),
      measured and fmt("Un monstre de ton niveau : %d XP (mesuré)." , per)
        or fmt("Un monstre de ton niveau : ≈ %d XP (formule Classic 5 × niveau + 45).", per),
      "Pas de détour pour eux : ne contourne simplement pas ceux qui sont sur ta route." },
    score = 45, ttl = 60 }
end

CHECK.cheetah = function()
  if not hunter() or not knows(SP.CHEETAH) or qfCovers("aspect") or quickMount() then return nil end
  local t = now()
  if not movingSince or t - movingSince < 15 then return nil end
  local st = pstate()
  if st.mounted or st.taxi or st.dead or st.ghost or inInstance() or inCombat() then return nil end
  if try(g("IsSwimming")) or hostileTarget() then return nil end
  local a = readAuras()
  if a.travel or (a.hidden or 0) > 0 then return nil end
  local name = spell(SP.CHEETAH, "Aspect du guépard")
  local walked = walkSec + (t - movingSince)
  return { text = fmt("Passe en « %s » pour marcher : +30 %% de vitesse (Faucon avant de tirer)", name),
    why = { fmt("Tu marches depuis %d s sans aspect de voyage.", floor(t - movingSince)),
      fmt("Marche à pied cette session : %s → ≈ %s gagnées avec +30 %%.", Fmt.Time and Fmt.Time(walked) or (floor(walked) .. " s"),
        Fmt.Time and Fmt.Time(walked * 0.3 / 1.3) or (floor(walked * 0.3 / 1.3) .. " s")),
      "Un coup reçu t'hébète : repasse en Faucon avant le pull." },
    kind = "buff", score = 50, urgent = true, ttl = 30, sig = "c" }
end

CHECK.turninLevel = function()
  local lvl = level()
  if lvl >= 60 then return nil end
  local xp, mx = num(try(g("UnitXP"), "player")), num(try(g("UnitXPMax"), "player"))
  if not (xp and mx) then return nil end
  local n, total, enough = E.TurnIn(quests(), mx - xp)
  if not enough or n == 0 then return nil end
  local what = n > 1 and fmt("tes %d quêtes finies", n) or "ta quête finie"
  local nxt = lvl + 1
  local train = E.TrainAt(nxt)
  local gain = train and "puis ton maître de classe" or (nxt >= 10 and "+1 point de talent") or "plus de vie et de mana"
  local why = { fmt("%s XP à rendre, il te manque %s XP pour le niveau %d.", nfmt(total), nfmt(mx - xp), nxt),
    train and fmt("Niveau %d : nouveaux rangs de sorts chez ton maître (règle Classic : niveaux pairs).", nxt)
      or fmt("Niveau %d impair : ton maître n'aura du neuf qu'au %d (règle Classic : niveaux pairs).", nxt, nxt + 1) }
  if nxt >= 10 then why[#why + 1] = "Et un point de talent à placer (touche N)." end
  return { text = fmt("Rends %s : niveau %d d'un coup, %s", what, nxt, gain),
    why = why, score = 68, ttl = 120, sig = lvl }
end

CHECK.questPenalty = function()
  local lvl = level()
  if lvl >= 60 then return nil end
  local xp, mx = num(try(g("UnitXP"), "player")), num(try(g("UnitXPMax"), "player"))
  if not (xp and mx) then return nil end
  local list = quests()
  local done = 0
  for _, q in ipairs(list) do if q.complete and tonumber(q.xp) then done = done + q.xp end end
  if mx - xp > max(mx * 0.15, done) then return nil end             -- le niveau n'est pas tout près
  local q, loss, part = E.Penalty(list, lvl)
  if not q then return nil end
  local d = lvl - q.level
  return { text = fmt("Rends « %s » avant le niveau %d : après, elle rapportera %d %% d'XP en moins (−%s)", cut(q.title, 24),
      lvl + 1, floor(part * 100 + 0.5), nfmt(loss)),
    why = { fmt("« %s » est niveau %d, toi %d : au niveau %d elle passe de %s à ≈ %s XP.", cut(q.title, 30), q.level, lvl, lvl + 1,
        nfmt(q.xp), nfmt(q.xp - loss)),
      fmt("Règle Classic (à vérifier sur Forever) : %d niveaux d'écart = %d %% de l'XP, %d niveaux = %d %%.", d, floor(qmul(d) * 100 + 0.5),
        d + 1, floor(qmul(d + 1) * 100 + 0.5)),
      "Plusieurs quêtes à rendre : commence par celle-ci." },
    score = 72, urgent = true, ttl = 90, sig = tostring(q.id) .. ":" .. lvl }
end

CHECK.ammoTier = function()
  if not hunter() then return nil end
  local ammo = num(try(g("GetInventoryItemID"), "player", 0))
  if not ammo or ammo <= 0 or (num(try(g("GetInventoryItemID"), "player", 18)) or 0) <= 0 then return nil end
  local lvl = level()
  local kind, cur, best = E.AmmoTier(ammo, lvl)
  if not kind or best <= cur then return nil end
  local c, b = AMMO[kind][cur], AMMO[kind][best]
  local dd = (AMMO_DPS[b[2]] or 0) - (AMMO_DPS[c[2]] or 0)
  if dd <= 0.5 then return nil end
  local bn, cn = itemName(b[1], b[5]), itemName(c[1], c[5])
  local why = { fmt("Tables du client Forever : %s %s dégâts/s, %s %s dégâts/s, ajoutés à chaque tir.", cn, dec(AMMO_DPS[c[2]]), bn,
    dec(AMMO_DPS[b[2]])) }
  local speed = num((try(g("UnitRangedDamage"), "player")))
  if speed and speed > 0.5 then why[#why + 1] = fmt("Avec ton arme (%s s) : ≈ +%d dégâts par tir.", dec(speed), floor(dd * speed + 0.5)) end
  why[#why + 1] = fmt("Prix : %s les 200 chez les marchands de munitions (niveau %d requis).", money(b[4]), b[3])
  return { text = fmt("Passe à « %s » (niv %d) : +%s dégâts/s pour %s les 200", cut(bn, 26), b[3], dec(dd), money(b[4])),
    why = why, score = 70, ttl = 90, sig = ammo .. ":" .. b[1] }
end

CHECK.emptyBag = function()
  local b = readBags()
  if (b.empty or 0) == 0 then return nil end
  return { text = b.empty > 1 and fmt("Équipe des sacs dans tes %d cases vides : plus de place pour le butin", b.empty)
      or "Équipe un sac dans ta case de sac vide : plus de place pour le butin",
    why = { "Sacs pleins = butin et objets de quête laissés au sol, retours en ville en plus.",
      "Forever (marchand de fournitures) : Petite sacoche marron 6 places 5 pa, Sacoche marron en cuir 8 places 25 pa." },
    score = 40, ttl = 60, sig = b.empty }
end

CHECK.eliteSolo = function()
  if inCombat() or groupN() > 1 or not hostileTarget() or try(g("UnitAffectingCombat"), "target") then return nil end
  local c = sv(try(g("UnitClassification"), "target"))
  if c ~= "elite" then return nil end                            -- rares : module Rares
  local tl, me = num(try(g("UnitLevel"), "target")), level()
  if not tl or tl <= 0 then return nil end
  local pet = hunter() and petAlive()
  if not (tl >= me + 2 or (tl >= me and not pet)) then return nil end
  local name = sv(try(g("UnitName"), "target"))
  local rapid = hunter() and knows(SP.RAPID)                     -- Tir rapide : chasseur, appris au niveau 26
  local why = { "Un élite a plusieurs fois la vie et les dégâts d'un monstre normal (et rapporte 2 fois son XP, règle Classic).",
    hunter() and ("À deux il tombe sans risque ; seul, il faut tout : familier heureux, " .. (rapid and "Tir rapide, " or "")
      .. "piège, potion.") or "À deux il tombe sans risque ; seul, il faut tout : tes sorts à recharge, une potion, un bandage." }
  if hunter() and not pet then why[#why + 1] = "Sans familier, personne ne tient l'aggro à ta place." end
  local keep = rapid and "Tir rapide" or "tes sorts à recharge"
  return { text = fmt("Élite niv %d et tu es seul : fais-le à deux, ou garde %s pour lui", tl, keep),
    why = why, score = 58, urgent = true, ttl = 40, sig = tostring(name) .. ":" .. tl }
end

-- ===================================================================================== choix : un seul conseil à la fois
local active = {}
local cool, coolSig = {}, {}
local shown, shownAt, lastHide, shownText = nil, -1e9, -1e9, nil
local shows = {}           -- horodatages des conseils montrés (≤ MAX_10MIN par 10 min)
local shownN = {}          -- [clé] = fois montré cette session (≤ max du conseil)
local loginAt = 0
M.active = active

local function eligible(tp)
  if not tp or not opt(tp.key) then return false end
  if tp.hunter and not hunter() then return false end
  return true
end
local function spent(key) local tp = TIP[key] return tp and tp.max and (shownN[key] or 0) >= tp.max or false end
--- évalue un conseil (rien en combat, mort, fantôme, en vol) -> résultat ou nil, rangé dans `active`
local function evaluate(key)
  local tp = TIP[key]
  local res
  if eligible(tp) and not inCombat() then
    local st = pstate()
    if not (st.dead or st.ghost or st.taxi) then
      local ok, r = pcall(CHECK[key])
      if ok and type(r) == "table" and r.text then res = r end
    end
  end
  if res then res.key, res.ttl, res.at = key, res.ttl or tp.ttl or 60, passT end
  active[key] = res
  return res
end
--- évaluation publique (tests, page) : relit tout (nouveau passage)
function M.Evaluate(key) newPass() return evaluate(key) end
local function cooled(key, r, t)
  local u = cool[key]
  if u and t < u and (coolSig[key] == nil or r == nil or coolSig[key] == r.sig) then return true end
  return false
end
local function dismissed(key, r)
  if not (T.IsDismissed and r) then return false end
  return T:IsDismissed("Coach:" .. key, r.text) and true or false
end
local function tipLines(r)
  local tip = {}
  for i, l in ipairs(r.why or {}) do tip[#tip + 1] = (i == 1 and "Pourquoi : " or "") .. l end
  tip[#tip + 1] = "Clic : tous les conseils pro (et les couper un par un)"
  return tip
end
local function openPage()
  local W = T.Window
  if W and W._active and W.Open then W.Open("coach") end
end
-- carte « Prochaine étape » (le HUD de la 4.0) : notre ligne y passe en avis « i » (contrat W.CARD_NOTICE), jamais à la
-- place d'un autre avis (mode sûr, quêtes automatiques)
local cardId
local function cardOff()
  local W = T.Window
  if cardId and W and type(W.CARD_NOTICE) == "table" then W.CARD_NOTICE[cardId] = nil end
  cardId = nil
end
local function cardOn(id)
  local W = T.Window
  if not (opt("card") and W and W._active and type(W.CARD_NOTICE) == "table") then return end
  local S = T._suggestions or {}
  for k in pairs(W.CARD_NOTICE) do
    if k ~= id and not k:find("^Coach:") and S[k] then return end
  end
  if cardId ~= id then cardOff() end
  W.CARD_NOTICE[id] = W.CARD_NOTICE[id] or {}
  cardId = id
end
M.CardId = function() return cardId end
local function show(key, r)
  if not (opt("nowLine") and M.Suggest) then return end
  if shown == key and shownText == r.text and T._suggestions and T._suggestions["Coach:" .. key] then return end
  shownText = r.text
  local e = M:Suggest(key, r.text, 2, { ttl = (r.ttl or 60) + 10, cooldown = 3600, sound = false, chat = false, tab = "now",
    kind = r.kind or "advice", topic = r.topic, tooltip = tipLines(r), onClick = openPage })
  if type(e) == "table" then
    e.clickHint = "Clic : tous les conseils pro (et les couper un par un)"
    cardOn("Coach:" .. key)
  end
end
local function hide(key, coolIt)
  local r = active[key]
  if coolIt then
    local tp = TIP[key]
    cool[key], coolSig[key] = now() + (tp and tp.cd or 900), r and r.sig
  end
  M:Clear(key)
  if cardId == "Coach:" .. key then cardOff() end
  if shown == key then shown, lastHide, shownText = nil, now(), nil end
end
local function recentShows(t)
  local n = 0
  for i = #shows, 1, -1 do if t - shows[i] < 600 then n = n + 1 else break end end
  return n
end
--- le plus rentable gagne ; un conseil montré reste MIN_SHOW s, puis ttl s ; rien en combat
function M.Pick()
  local t = now()
  if not M._active then return nil end
  if not opt("nowLine") then if shown then hide(shown, false) end return nil end
  if inCombat() or t - loginAt < M.LOGIN_QUIET then return shown end
  if shown then
    local r = evaluate(shown)                     -- toujours vrai ?
    if not r or cooled(shown, r, t) then hide(shown, t - shownAt >= 5)          -- devenu faux : vu s'il est resté 5 s
    elseif dismissed(shown, r) or t - shownAt > (r.ttl or 60) then hide(shown, true) end   -- masqué (clic droit) / vu
  end
  local best, bs
  for _ = 1, 3 do                                  -- le gagnant masqué par le joueur : on passe au suivant
    best, bs = nil, nil
    for key, r in pairs(active) do
      if r and key ~= shown and eligible(TIP[key]) and not cooled(key, r, t) and not spent(key) and t - (r.at or 0) < 30 then
        local sc = (r.score or 0) * (r.urgent and 1.3 or 1)
        if not bs or sc > bs or (sc == bs and key < best) then best, bs = key, sc end
      end
    end
    if shown and active[shown] and not cooled(shown, active[shown], t) then
      local r0 = active[shown]
      local s0 = (r0.score or 0) * (r0.urgent and 1.3 or 1)
      if not bs or s0 >= bs then best, bs = shown, s0 end
    end
    if not best or not dismissed(best, active[best]) then break end
    cool[best], coolSig[best] = t + 7200, active[best].sig
  end
  if not best then return shown end
  local r = active[best]
  if shown == best then show(best, r) return shown end
  if shown then
    local r0 = active[shown]
    if t - shownAt < M.MIN_SHOW and not (r.urgent and not (r0 and r0.urgent)) then return shown end
    hide(shown, t - shownAt >= M.MIN_SHOW)           -- remplacé après avoir été lu : il a eu son tour
  elseif t - lastHide < M.GAP and not r.urgent then
    return nil
  end
  if recentShows(t) >= M.MAX_10MIN and not r.urgent then return nil end
  r = evaluate(best)                               -- encore vrai à l'instant ?
  if not r then return nil end
  show(best, r)
  shown, shownAt = best, t
  shownN[best] = (shownN[best] or 0) + 1
  shows[#shows + 1] = t
  if #shows > 20 then tremove(shows, 1) end
  if r.onShown then pcall(r.onShown) end
  return shown
end
function M.Shown() return shown end

-- ===================================================================================== boucle + évènements
local dirty = {}
local ROT = {}
for _, tp in ipairs(TIPS) do ROT[#ROT + 1] = tp.key end
local rot = 0
local function mark(...) for i = 1, select("#", ...) do dirty[select(i, ...)] = true end end
M.Mark = mark
function M:AutoRefresh()
  newPass()
  if fighting then return end
  refreshSecret()
  readVitals()
  local n = 0
  for key in pairs(dirty) do
    dirty[key] = nil
    evaluate(key)
    n = n + 1
    if n >= 4 then break end
  end
  rot = rot % #ROT + 1                             -- un conseil de fond par passage (chacun ≈ toutes les minutes)
  if not dirty[ROT[rot]] then evaluate(ROT[rot]) end
  M.Pick()
end
--- tout réévaluer (tests, page ouverte) puis choisir
function M.RunAll()
  newPass()
  if fighting then return end
  refreshSecret()
  readVitals()
  for _, key in ipairs(ROT) do evaluate(key) end
  dirty = {}
  return M.Pick()
end

local function afterFight()
  M.FightEnd()
  mark("bandage", "petTank", "drinkNow", "greenMobs", "groupXP", "eliteSolo", "chainPull")
end
function M:OnEnable()
  loginAt = now()
  readCombat()
  CLASS = nil
  if lastXP == nil then
    lastXP, lastMax = num(try(g("UnitXP"), "player")), num(try(g("UnitXPMax"), "player"))
    lastExh = num(try(g("GetXPExhaustion"))) or 0
  end
  self:RegisterEvent("PLAYER_REGEN_DISABLED", function()
    fighting = true
    M.FightStart()
    if shown then hide(shown, false) end          -- en combat : pas de conseil (la ligne revient s'il est encore vrai)
  end)
  self:RegisterEvent("PLAYER_REGEN_ENABLED", function()
    fighting = false
    if T.After then T:After(0.6, afterFight) else afterFight() end
  end)
  -- la vie juste avant un pull : seulement la vie (2 lectures), au plus 2 fois par seconde
  self:RegisterEvent("UNIT_HEALTH", function(_, _, u)
    if u ~= "player" then return end
    local t = now()
    if t - (V.hpAt or -1e9) < 0.5 or inCombat() then return end
    local hp, mx = num(call1("UnitHealth", "player")), num(call1("UnitHealthMax", "player"))
    V.hp, V.hpMax, V.hpP, V.hpAt, V.at = hp, mx, pct(hp, mx), t, t
  end)
  self:RegisterEvent("UNIT_AURA", function(_, _, u)
    if u ~= "player" or fighting then return end
    passN = passN + 1                               -- auras changées : nouvelle lecture
    trackEat()
    mark("eatDrink", "chainPull", "wellFed", "bandage", "drinkNow", "cheetah")
  end)
  self:RegisterEvent("PLAYER_XP_UPDATE", function() M.OnXP() mark("restedKill", "turninLevel", "questPenalty") end)
  self:On("DETECT", function(_, _, kind, data) if kind == "kill" then M.OnKill(data) end end)
  self:RegisterEvent("QUEST_LOG_UPDATE", function() mark("turninLevel", "questPenalty") end)
  self:RegisterEvent("BAG_UPDATE_DELAYED", function() mark("ammoTier", "wellFed", "bandage", "emptyBag", "eatDrink") end)
  self:RegisterEvent("UNIT_INVENTORY_CHANGED", function(_, _, u) if u == "player" then mark("ammoTier") end end)
  self:RegisterEvent("PET_BAR_UPDATE", function() P.at = -1e9 mark("growlAuto", "growlDungeon", "petDefensive") end)
  self:RegisterEvent("UNIT_PET", function(_, _, u)
    if u == "player" then P.at = -1e9 mark("growlAuto", "growlDungeon", "petDefensive", "petLevel", "petTank") end
  end)
  self:RegisterEvent("PLAYER_TARGET_CHANGED", function() if not inCombat() then mark("eliteSolo") end end)
  self:RegisterEvent("GROUP_ROSTER_UPDATE", function() mark("growlAuto", "growlDungeon", "petDefensive", "groupXP") end)
  self:RegisterEvent("ZONE_CHANGED_NEW_AREA", function() P.at = -1e9 mark("growlDungeon", "petDefensive", "restedKill") end)
  self:RegisterEvent("SPELLS_CHANGED", function() mark("newTool") end)
  self:RegisterEvent("PLAYER_LEVEL_UP", function() mark("newTool", "ammoTier", "petLevel", "questPenalty", "turninLevel") end)
  self:RegisterEvent("PLAYER_STARTED_MOVING", function() if not inCombat() and not movingSince then movingSince = now() end end)
  self:RegisterEvent("PLAYER_STOPPED_MOVING", function()
    if movingSince then walkSec, movingSince = walkSec + max(0, now() - movingSince), nil end
  end)
  readVitals()
  mark("newTool")
end
function M:OnDisable()
  for key in pairs(active) do active[key] = nil end
  if shown then M:Clear(shown) end
  shown = nil
  cardOff()
end
function M:OnOptionChanged(key, v)
  if v == false and key then
    active[key] = nil
    if shown == key then hide(key, false) end
    if key == "nowLine" and shown then hide(shown, false) end
    if key == "card" then cardOff() end
  end
  local W = T.Window
  if W and W.Dirty then W.Dirty() end
end
--- interrupteur d'un conseil (page, options) : ON / OFF tout de suite, sans /reload
function M.SetTip(key, on)
  if not (M.db and TIP[key]) then return end
  M.db[key] = on and true or false
  M:OnOptionChanged(key, M.db[key])
end
--- remet à zéro les temps de repos des conseils (bouton de la page)
function M.ResetCooldowns()
  cool, coolSig, shows, shownN = {}, {}, {}, {}
  lastHide = -1e9
end
function M.ResetSession()
  for i = #fights, 1, -1 do fights[i] = nil end
  for i = #kills, 1, -1 do kills[i] = nil end
  for i = #eats, 1, -1 do eats[i] = nil end
  for key in pairs(active) do active[key] = nil end
  if shown then M:Clear(shown) end
  cardOff()
  shown, shownAt, lastHide, loginAt, shownText = nil, -1e9, -1e9, -1e9, nil
  cool, coolSig, shows, dirty, shownN = {}, {}, {}, {}, {}
  fight, pendKill, pendXP, movingSince, walkSec, eating = nil, nil, nil, nil, 0, nil
  lastXP, lastMax, lastExh = num(try(g("UnitXP"), "player")), num(try(g("UnitXPMax"), "player")), num(try(g("GetXPExhaustion"))) or 0
  V.at, V.pass, A.at, A.pass, B.gen, P.at = -1e9, -1, -1e9, -1, -1, -1e9
  readCombat()
end
function M.SetMoving(since) movingSince = since end

-- ===================================================================================== page « Conseils pro »
local function visibleTech(tc, isH) return not (tc.hunter and not isH) end
local function techText(tc, lvl)
  local lock = tc.level and lvl < tc.level
  return tc.title .. " : " .. tc.line .. (lock and fmt(" (niv %d)", tc.level) or "")
end
local function tipRow(tp, W, Ly)
  local on = opt(tp.key)
  local r = active[tp.key]
  local live = on and r and not cooled(tp.key, r, now())
  local text = tp.label .. (live and (" — en ce moment : " .. T.PlainText(r.text)) or "")
  if not W.Match(text, tp.when) then return false end
  local tip = { tp.when }
  if live then for _, l in ipairs(r.why or {}) do tip[#tip + 1] = l end end
  Ly.row({ text = text, right = on and (live and "actif" or "ON") or "OFF", icon = THEME[tp.theme].icon, kind = live and "warn" or "info",
    tooltip = tip, onClick = function() M.SetTip(tp.key, not on) W.Render() end,
    clickHint = on and "Clic : couper ce conseil" or "Clic : rallumer ce conseil" })
  return true
end
function M.RenderPage(Ly)
  local W = T.Window
  if not W then return end
  if not M._active then
    Ly.empty("« Conseils pro » est coupé.", { "Allumer « Conseils pro »", function() T:SetModuleEnabled("Coach", true) end,
      "Clic : allume le module (sans /reload)" })
    return
  end
  M.RunAll()
  local lvl, isH = level(), hunter()
  Ly.text("Le geste d'un joueur expert au bon moment, chiffres à l'appui. Un seul conseil à la fois dans « Maintenant » ; "
    .. "ici, tout ce qui est vrai en ce moment, chaque conseil (clic = ON / OFF) et les techniques à appliquer.", "S", "muted")
  -- en ce moment
  local live = {}
  for _, tp in ipairs(TIPS) do
    local r = active[tp.key]
    if r and opt(tp.key) and eligible(tp) then live[#live + 1] = { tp = tp, r = r } end
  end
  table.sort(live, function(a, b) return (a.r.score or 0) > (b.r.score or 0) end)
  Ly.header("En ce moment", nil, #live > 0 and #live or nil, ICON .. "Spell_Nature_TimeStop")
  local any = false
  for _, it in ipairs(live) do
    if W.Match(it.r.text) then
      any = true
      Ly.row({ text = it.r.text, right = (shown == it.tp.key) and "affiché" or nil, icon = THEME[it.tp.theme].icon, kind = "warn",
        tooltip = tipLines(it.r), clickHint = "Le pourquoi est dans l'infobulle" })
    end
  end
  if not any then Ly.text("Rien à corriger pour l'instant : chaque conseil apparaît ici et dans « Maintenant » quand il sert.", "S", "text", 6) end
  -- les 8 thèmes
  for _, th in ipairs(THEMES) do
    local tips, techs = {}, {}
    for _, tp in ipairs(TIPS) do if tp.theme == th.id and (not tp.hunter or isH) then tips[#tips + 1] = tp end end
    for _, tc in ipairs(TECH) do if tc.theme == th.id and visibleTech(tc, isH) then techs[#techs + 1] = tc end end
    if #tips + #techs > 0 and Ly.header(th.label, "coach:" .. th.id, #tips + #techs, th.icon, true) then
      for _, tp in ipairs(tips) do tipRow(tp, W, Ly) end
      for _, tc in ipairs(techs) do
        local text = techText(tc, lvl)
        if W.Match(text, tconcat(tc.steps, " ")) then
          local tip = {}
          for _, s in ipairs(tc.steps) do tip[#tip + 1] = s end
          tip[#tip + 1] = "Source : " .. tc.src
          Ly.row({ text = text, right = "technique", icon = th.icon, kind = "info", tooltip = tip,
            clickHint = "Technique à appliquer (pas d'alerte)" })
        end
      end
    end
  end
end
do
  local W = T.Window
  if W and W.AddPage and not (W.pageIndex and W.pageIndex.coach) then
    W.AddPage({ id = "coach", label = "Conseils pro", icon = ICON .. "INV_Misc_Book_11", title = "Conseils pro",
      desc = "Le geste d'un expert au bon moment, et les techniques à appliquer",
      sub = function()
        local n = 0
        for key, r in pairs(active) do if r and opt(key) and eligible(TIP[key]) then n = n + 1 end end
        return (n > 0 and fmt("%d conseil%s vrai%s en ce moment", n, n > 1 and "s" or "", n > 1 and "s" or "")
          or "Rien à corriger en ce moment") .. fmt(" · %d conseils, %d techniques", #TIPS, #TECH)
      end,
      render = function(Ly) M.RenderPage(Ly) end,
      action = function()
        return "Réafficher les conseils", function() M.ResetCooldowns() end,
          "Clic : les conseils déjà montrés peuvent revenir tout de suite"
      end })
    -- dans la navigation : juste après « Outils »
    local pages = W.PAGES
    if type(pages) == "table" and pages[#pages] and pages[#pages].id == "coach" then
      local mine = tremove(pages)
      local at = #pages + 1
      for i, p in ipairs(pages) do if p.id == "tools" then at = i + 1 break end end
      table.insert(pages, at, mine)
    end
  end
end
