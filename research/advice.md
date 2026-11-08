# ADVICE — des conseils d'expert VRAIS pour TreeAI (4.0.1)

Branche `advice` (worktree `/home/user/advice`), base `7af369d` = release 4.0.0. Agent ADVICE.
Demande : « vérifie tout ce que tu peux donner comme vrais conseils de malade mental » puis « genre vraiment tout ce que
tu peux conseiller de dingue, rajoute-le » — chasseur horde niveau 28, en duo (parfois à 4), WoW: Forever (1.60.1, frFR).

Livré : `Modules/Coach.lua` (« Conseils pro », mode sûr) + sa page de la fenêtre TreeAI, nettoyage de
`Modules/Edge2.lua` (« Avantages »), ce document (recherche + audit de toutes les lignes de conseil), tests
(`tests/wow/coach_scenario.lua`, `tests/wow/test_coach.py`, plafond dans `session_sim`).

## 0. Méthode et sources

* **Ce que le client laisse lire** : `research/fidelity.md` (doc de l'API Forever 1.60.1, valeurs secrètes). En combat
  sont secrets : la vie / le mana (joueur compris, hypothèse fidelity §6), les auras, la menace, les incantations, le nom
  des PNJ ; en instance, la vie l'est même hors combat. Donc **tout ce qui suit se décide hors combat**, sur des lectures
  faites avant / après le combat.
* **Données du client Forever** (wago.tools, produit `wowf`, build 1.60.1.70862, enUS + frFR) : `Spell`, `SpellName`,
  `ItemSparse`, `ItemEffect`, `ItemXItemEffect`, `ItemDamageAmmo` ; `Data/Consumables.lua` (déjà généré depuis ces tables).
* **Code d'interface de Blizzard pour Forever** (`Gethe/wow-ui-source`, branche `forever`) :
  `Blizzard_ActionBar/Shared/PetActionBar.lua` → `name, texture, isToken, isActive, autoCastAllowed, autoCastEnabled,
  spellID = GetPetActionInfo(i)` ; les modes du familier sont des jetons `PET_MODE_*`.
* **Guides Forever publics** (faits seulement, aucun texte repris) : Warcraft Tavern « Hunter Pets » (humeur 125 / 100 /
  75 %, un familier ne gagne d'XP que sur les kills, pas sur les quêtes, et s'arrête à ton niveau) et « Hunter Leveling »
  (Tir rapide +40 % 15 s, 5 min ; Grondement ; aspects ; Feindre la mort) ; actualités Warcraft Tavern / Icy Veins (plat
  cuisiné : +5 % d'XP des kills, il faut manger 10 s) ; warcraft.wiki.gg « World of Warcraft: Forever » (Sac de couchage
  douillet = repos hors ville, chaîne dès le niveau 14 ; double spé au 40).
* **Règles Classic** (marquées « Classic, à vérifier ») : XP des quêtes trop basses, bonus d'XP de groupe, XP d'un monstre
  (5 × niveau + 45), accumulation du repos, gardien des esprits.

Vérifié dans les tables Forever pour ce travail :

| fait | preuve (client 1.60.1) |
|---|---|
| plat cuisiné → « Bien nourri » + **+5 % d'XP des monstres** | sort 1243969 « Bien nourri – bonus d'EXP » (« Experience gained from kills increased by $w1% ») ; 107 objets dont l'effet (ItemEffect) est un sort « If you spend at least 10 sec eating… $@spelldesc1243969 » |
| ces plats ont une aura **« Denrée nutritive »** / **« Boisson nutritive »** (sorts 1248377… / 1249906…), pas « Nourriture » | SpellName frFR |
| « bandage récent » : **« Un bandage a été appliqué récemment »** (11196) et **« Bandage appliqué récemment »** (1305047) | SpellName frFR |
| bandages : Lin 66, Lin épais 114, Laine 161, Laine épaisse 301, Soie 400… (secourisme 1 / 20 / 50 / 75 / 100…) | ItemSparse (RequiredSkillRank) + Consumables.lua |
| munitions : Flèche pointue (niv 10, ilvl 15) **3,6** dégâts/s ; **Flèche rasoir** (niv 25, ilvl 30) **7,6** ; Flèche barbelée (niv 40) 13 ; balles : Balle lourde / **Balle dure** / Balles de précision idem | ItemDamageAmmo (qualité commune) + ItemSparse ; prix : 50 pc, **3 pa**, 10 pa les 200 |
| Visée **partage sa recharge avec Flèches multiples** ; apprise au **niveau 20** chez le maître | Spell 19434 ; « Hunter abilities (Forever) » |
| Aspect du guépard : vitesse +$s1 %, hébété si tu prends un coup | Spell 5118 (+30 % d'après les guides Forever) |
| sacs : Petite sacoche marron 6 places 5 pa ; Sacoche marron en cuir 8 places 25 pa ; Sac lourd marron 10 places 2 po | ItemSparse frFR |
| `GetPetActionInfo`, `CanLootUnit(guid)`, `UnitGroupRolesAssigned`, `C_Item.IsUsableItem`, `GetProfessions` : présents | forever_ref.lua / doc API |

## 1. Recherche : chaque technique, vraie ? détectable ? combien ça rapporte ?

« Auto » = conseil automatique de Coach (une ligne dans « Maintenant » au bon moment) ; « Page » = technique montrée à la
demande dans « Conseils pro » ; « Proprio » = déjà dit par un autre module (on ne double pas).

### Repos et ressources (le temps mort est le premier gisement d'XP/h)

| technique | vrai sur Forever ? | comment TreeAI le voit | gain | ligne (FR) | statut |
|---|---|---|---|---|---|
| Manger ET boire en même temps | Classic (deux buffs distincts) | aura « Nourriture »/« Denrée nutritive » sans « Boisson » (ou l'inverse) hors combat + mana (ou vie) < 60 % + boisson (repas) dans les sacs | ≈ 18 s par repos (durée lue sur l'aura) | « Bois aussi pendant que tu manges (« Jus de melon ») : ≈ 18 s de repos en moins » | Auto `eatDrink` (se tait si la barre de combat montre « Manger et boire ») |
| Bandage au lieu d'un repas quand il suffit | heal des bandages = tables Forever ; canal 8 s (Classic) | fin de combat : PV manquants entre 50 et 125 % du soin du meilleur bandage utilisable (secourisme lu), pas de « bandage récent », pas besoin de boire | ≈ 10 s par repos | « Bande-toi au lieu de manger : Bandage épais en laine rend 301 PV en ≈ 8 s » | Auto `bandage` (se tait si la barre de combat a son bouton) |
| Boire AVANT d'être à sec | règle de jeu (sans mana : plus de Morsure / Tir des arcanes) | mana perdu par combat MESURÉ (≥ 5 combats, lu avant / après) ; mana actuel < 1,3 combat | évite un combat lent à sec | « Bois avant le pull : il te reste ≈ 1 combat de mana (−25 % par combat, mesuré) » | Auto `drinkNow` |
| Ne pas manger pour rien | règle de jeu | tu t'assois pour manger à ≥ 70 % de vie alors que ta vie perdue mesurée te laisse ≥ 2 combats | la durée du repas (≈ 18 s) | « Enchaîne : à 80 % de vie tu tiens 3 combats (−15 % par combat, mesuré) » | Auto `chainPull` |
| Plat « Bien nourri » = +5 % d'XP des kills | **Forever, vérifié** (sort 1243969, 107 plats) | pas d'aura 1243969, un de ces plats dans les sacs à ton niveau | +5 % des XP de monstres (XP/h mesurée dans l'infobulle) | « Mange « Côtes de porc aux herbes » 10 s : Bien nourri, +5 % d'XP sur les monstres » | Auto `wellFed` |
| Repos d'XP : auberge / Sac de couchage douillet | Classic (5 % / 8 h, ×¼ dehors, 150 % max) + Forever (sac de couchage) | — (hors ligne) | — | « Déconnecte-toi à l'auberge… » | Page ; Proprio : Leveling / Session (ligne de déconnexion) |
| Nourrir le familier pendant ton repas | Classic | aura de repas + humeur < heureux | 0 s perdue | — | Page ; Proprio : Chasseur (proposition § 5) |
| Soigner le familier pendant ton repas | Classic | PV du familier hors combat | évite un « Ressusciter » | — | Page ; Proprio : barre de combat (bouton « Soigner le familier ») |

### Pull et combat (en combat Forever cache tout : on mesure avant / après)

| technique | vrai ? | détection | gain | ligne | statut |
|---|---|---|---|---|---|
| Le familier tanke, tu tires après 2 morsures | Classic (menace) | vie perdue par combat MESURÉE (≥ 6 combats avec familier, solo/duo, monstres normaux) ≥ 30 % | repas en moins : la perte × secondes de repas mesurées | « Laisse ton familier prendre l'aggro 2 s avant de tirer : tu perds 40 % de vie par combat » | Auto `petTank` |
| Monstres trop faibles = XP perdue | Classic (formule) + mesure | 5 derniers kills hors quête, ≥ 5 niveaux sous toi, XP de base mesurée < 60 % d'un monstre de ton niveau | jusqu'à ×2 d'XP/h | « Monstres trop faibles (niv 21) : 60 XP chacun, vise les niv 26 à 30 (≈ 185 XP) » | Auto `greenMobs` |
| Élite au-dessus de toi, seul | Classic | cible élite hors combat, seul, niveau ≥ toi+2 (ou ≥ toi sans familier) | évite une mort (usure + course) | « Élite niv 31 et tu es seul : fais-le à deux, ou garde Tir rapide pour lui » (chasseur avec Tir rapide appris ; sinon « …garde tes sorts à recharge pour lui ») | Auto `eliteSolo` (rares : module Rares) |
| Nouveau sort clé : comment s'en servir | descriptions des sorts Forever | sort appris depuis la dernière lecture (1re lecture = ce que tu sais déjà) | utiliser l'outil tout de suite | « Nouveau : « Piège de givre » — pose-le avant de tirer sur un groupe : ils arrivent ralentis » | Auto `newTool` (une fois par sort) |
| Ouverture à Visée (35 m, cible marquée) | Forever (niv 20, recharge partagée avec Multi) | — (en combat) | gros coup gratuit pendant l'approche | « Cible marquée à 35 m : Visée pendant qu'elle arrive, puis Tir des arcanes. » | Page |
| Pull en ligne de vue des lanceurs | Classic | — | regroupe un pack | « Lanceurs de sorts : tire puis cache-toi derrière un mur, ils viennent à toi. » | Page |
| Kiter : Coupure d'ailes, recule, tire | Classic ; zone morte 5–8 m à vérifier sur Forever | — | pas de mêlée longue | « Coupure d'ailes, recule de 10 m, tire ; pas de mêlée qui dure. » | Page |
| Fuyards à 15–20 % | Classic | — (nom / vie secrets en combat) | évite un 2e pack | « Humanoïdes vers 15-20 % de vie : Trait de choc avant qu'ils fuient… » | Page |
| Enchaîner (chain pull) | Classic | — | moins de temps mort | « Pendant que le familier finit le 1er, marque et prépare le suivant. » | Page |
| Piège avant le pull, Effrayer une bête, Tir rapide sur élite | Forever (descriptions + guide) | — | contrôle / burst | voir § 3 | Page |
| Marque avant un élite / un rare | Forever (Marque = PA à distance de tous) | cible élite hors combat | | — | Proprio : Chasseur |
| Aspect du faucon avant le pull, pas de Guépard en combat | Forever | auras hors combat / en combat | | — | Proprio : Chasseur |
| Ne pas couper le tir auto avec Visée / Multi | Classic | début d'incantation vs barre de tir | | — | Proprio : Chasseur |
| Feindre la mort réussi → piège | Forever (niv 30) | sortie de combat 2 s après FD | | — | Proprio : Chasseur |

### Familier

| technique | vrai ? | détection | gain | ligne | statut |
|---|---|---|---|---|---|
| Grondement en auto en solo / duo | Classic ; Grondement existe (sorts 2649…, « Grondement ») | barre du familier (`GetPetActionInfo`) : Grondement présent, auto permis mais coupé, groupe ≤ 2, hors donjon | moins de coups pris | « Mets « Grondement » en automatique (clic droit sur son bouton) : ton familier garde l'aggro » | Auto `growlAuto` |
| Familier en retard de niveaux | **Forever (guide) : XP des kills seulement, pas des quêtes** | `UnitLevel("pet")` ≤ toi − 3 | toucher / aggro du familier | « Ton familier a 4 niveaux de retard (niv 24) : fais-le tuer des monstres de ton niveau » | Auto `petLevel` |
| Humeur heureux (+25 %), nourriture du sac | Forever (125 / 100 / 75 %) | `C_PetInfo.GetPetHappiness` | +25 % de dégâts | — | Proprio : Chasseur |
| Points de dressage, rangs de techniques | Forever (`GetPetTrainingPoints`, tables Pets) | | | — | Proprio : Chasseur |
| Modes : défensif, passif pour fuir | Classic | jeton `PET_MODE_*` actif | | « Défensif en général, passif pour fuir… » | Page (+ Auto en donjon) |
| Rendre l'aggro au familier | Classic | — | | « Il perd la cible : Coupure d'ailes ou Trait de choc, recule… » | Page |

### Quêtes et trajet

| technique | vrai ? | détection | gain | ligne | statut |
|---|---|---|---|---|---|
| Quêtes finies = niveau d'un coup, puis maître | XP lue dans le journal ; nouveaux sorts aux **niveaux pairs** (Classic, même règle que Guichets) | somme de l'XP des quêtes finies ≥ XP manquante | un niveau (+ rangs au pair, + 1 point de talent dès 10) | « Rends tes 3 quêtes finies : niveau 30 d'un coup, puis ton maître de classe » ; niveau impair : « …niveau 29 d'un coup, +1 point de talent » (le maître n'est cité qu'aux niveaux pairs) | Auto `turninLevel` (déplacé d'Avantages) |
| Rendre une quête AVANT un niveau | Classic, à vérifier (80 / 60 / 40 / 20 / 10 % à 6 / 7 / 8 / 9 / 10+ niveaux) | quête finie à 5–9 niveaux sous toi, niveau suivant proche (≤ 15 % ou ≤ l'XP à rendre), perte ≥ 150 XP | la part perdue (ex. −500 XP) | « Rends « X » avant le niveau 29 : après, elle rapportera 25 % d'XP en moins (−500) » | Auto `questPenalty` |
| Repos : tuer en chemin | Classic (repos = ×2 sur les kills) | repos ≥ 20 % du niveau, hors ville / instance ; monstres couverts = repos ÷ XP mesurée (ou ≈ formule) | bonus gratuit | « Repos : tes ≈ 48 prochains monstres valent double, tue ceux sur ton chemin » | Auto `restedKill` |
| Guépard pour marcher | Forever (sort 5118) | en mouvement 15 s hors combat, pas de monture / aspect de voyage, pas d'ennemi ciblé | 23 % du temps de marche | « Passe en « Aspect du guépard » pour marcher : +30 % de vitesse (Faucon avant de tirer) » | Auto `cheetah` (se tait si la barre de quête montre son bouton monture = Guépard) |
| Rendre par paquets, prendre tout le relais | Classic | | | « Finis toutes les quêtes d'un relais avant d'y retourner… » | Page ; Proprio : Planner / ZonePlan (ordre des arrêts) |
| Foyer / vol / marche | | Route (temps mesurés) | | — | Proprio : Route, Guichets, Vitesse |

### Argent et sacs

| technique | vrai ? | détection | gain | ligne | statut |
|---|---|---|---|---|---|
| Munitions du bon palier | **Forever, vérifié** (ItemDamageAmmo) | munition équipée = palier de marchand inférieur au meilleur permis par ton niveau | +4 dégâts/s (≈ +12 par tir avec une arme de 3 s), 3 pa les 200 | « Passe à « Flèche rasoir » (niv 25) : +4 dégâts/s pour 3 pa les 200 » | Auto `ammoTier` |
| Case de sac vide | règle de jeu | `GetContainerNumSlots(1..4) == 0` | moins de butin perdu | « Équipe un sac dans ta case de sac vide : plus de place pour le butin » | Auto `emptyBag` |
| Nombre de munitions / carquois | Forever | | | — | Proprio : Chasseur |
| Vendre les gris, réparer | | | | — | Proprio : Inventaire, barre de quête |
| HV : liquide seulement, scan frais | | | | — | Proprio : HV |

### Duo et groupe

| technique | vrai ? | détection | gain | ligne | statut |
|---|---|---|---|---|---|
| À 3+ : l'XP d'un monstre se partage | Classic, à vérifier (×1,17 à 3, ×1,3 à 4) | 6 derniers kills en groupe ≥ 3, hors quête, XP de base mesurée < 50 % d'un monstre solo | XP/h | « À 3, ces monstres hors quête vous rapportent 40 XP chacun : allez sur vos quêtes » | Auto `groupXP` |
| Kills de quête partagés | Classic | | | « En groupe, un monstre de quête tué compte pour chacun… » | Page |
| Duo avec familier : il part en premier | Classic | | | « Ton familier tanke pour vous deux… » | Page |
| Rendre ensemble une quête à suite | Classic | | | — | Proprio : Groupe (« Attends X… ») |
| Partager les quêtes | | | | — | Proprio : Groupe, barre de quête |

### Donjons

| technique | vrai ? | détection | gain | ligne | statut |
|---|---|---|---|---|---|
| Grondement coupé avec un tank | Classic | en instance, groupe ≥ 3 avec un TANK (rôle) ou guerrier / paladin / druide, Grondement en auto | évite un wipe | « Coupe « Grondement » en donjon (clic droit dessus) : il vole l'aggro à X » | Auto `growlDungeon` |
| Familier défensif en donjon | Classic | jeton `PET_MODE_AGGRESSIVE` actif en instance | évite un 2e pack | « Passe ton familier en défensif : en agressif il attaque des packs tout seul » | Auto `petDefensive` |
| Piège givrant sur un add, laisser le tank prendre, munitions | Classic | | | voir § 3 | Page ; Proprio : Donjon (préparation, ≈ 1 000 munitions) |

### Sécurité et JcJ

| technique | vrai ? | détection | gain | ligne | statut |
|---|---|---|---|---|---|
| Feindre la mort (niv 30) : adds, pull raté, puis piège | Forever (sort 5384) | sort appris → `newTool` | survie | « Les monstres t'oublient : pour lâcher des adds ou un pull raté. » (étape : relève-toi, piège givrant, reprends) | Page + Auto `newTool` |
| Fuir un combat perdu (familier tanke, Guépard) | Classic | — | une mort évitée | « Laisse le familier tanker, Aspect du guépard, cours… » | Page |
| Course au corps vs gardien | Classic, à vérifier (25 % d'usure, jusqu'à 10 min) | | | — | Proprio : Vitesse (calcule le meilleur choix) |
| Ennemi JcJ plus haut | | | | — | Proprio : PvPMark |

### Ce qui n'est PAS fait (et pourquoi)

* « 200 flèches ≈ 18 min : achète un carquois de 1 400 à Bois-Brisé (sur ta route) » : le compte et la durée sont déjà du
  Chasseur (mesurés) ; « sur ta route » demanderait les marchands de munitions sur le trajet : Questie connaît des
  vendeurs mais pas leur stock à ton niveau de façon sûre → pas de ligne inventée (QuickTravel le fait seulement chez un
  marchand déjà vu).
* « Bois maintenant : 2 pulls à 40 % coûtent 35 s » : boire à 40 % ou à 0 % prend le même temps de boisson (durée fixe) —
  la vraie règle d'expert est « ne pars pas avec moins d'un combat de mana » (`drinkNow`, mesuré).
* Détection en combat (aggro du familier, fuyard, zone morte, cible qui incante) : secret sur Forever → techniques à la
  demande, jamais d'alerte devinée.
* Temps de réapparition, « camp de farm » : trop peu d'observations → rien (retiré en 4.0, on ne le remet pas).

## 2. Conseils automatiques (Coach.lua) — règles

* Évalués hors combat seulement (rotation d'un conseil par passage d'AutoRefresh + ceux que leurs évènements marquent) ;
  la ligne s'efface au pull et revient après si elle est encore vraie.
* **Un seul** conseil pro à la fois dans « Maintenant » : score = poids du conseil × ampleur mesurée (× 1,3 si c'est
  maintenant ou jamais : repas en cours, fin de combat, donjon). Un conseil montré reste ≥ 20 s (sauf s'il devient faux ou
  qu'un urgent arrive), puis sa durée (20–120 s) ; ensuite son temps de repos (5–60 min) et un nombre de fois au plus
  par session pour les gestes qu'on apprend vite (Guépard 3, repas + boisson 4, bandage 5, case de sac 2…) ; 15 s entre
  deux conseils ; au plus 6 conseils différents par 10 min ; 45 s de calme après la connexion. Masqué au clic droit = ne
  revient pas. Mesure `session_sim` (60 min, Forever) : ≈ 20 kinstr/min (plafond 30), une ligne pro à l'écran < 1 % du
  temps, jamais deux à la fois.
* Prio 2 (jamais l'encadré), `sound = false`, rien dans le chat, clic = la page « Conseils pro ».
* Visible là où le joueur regarde : « Maintenant » (fenêtre / panneau) ET en tête de la carte « Prochaine étape » (le HUD
  de la 4.0) en avis « i » (contrat `W.CARD_NOTICE`), seulement si aucun autre avis n'y est (mode sûr, quêtes
  automatiques) ; option « Aussi en tête de la carte ».
* Infobulle : « Pourquoi : … » avec les chiffres (mesure + source), dernière ligne « Clic : tous les conseils pro ».
* Interrupteur par conseil : Options → « Conseils pro » (une case par conseil, rangées par thème) et clic sur la ligne du
  conseil dans la page ; « Montrer le meilleur conseil dans « Maintenant » » (sinon : page seulement).
* Mesures (session, pas d'onglet en plus) : vie / mana au pull (dernière lecture hors combat) et à la fin, XP de base d'un
  monstre (message d'XP lu par Detect + écart d'XP, repos à part), durée des repas, temps de marche.

## 3. Page « Conseils pro »

Fenêtre TreeAI, juste après « Outils ». En tête : ce qui est vrai en ce moment (chaque ligne avec son pourquoi), puis 8
thèmes repliables : chaque conseil automatique (état « actif » / « ON » / « OFF », clic = couper / rallumer) et les
**42 techniques** à appliquer (étiquette « technique », détails + source dans l'infobulle ; celles du chasseur cachées
aux autres classes ; « (niv N) » quand le sort n'est pas encore appris). Bouton du bas : « Réafficher les conseils ».

## 4. Audit de toutes les lignes de conseil (modules demandés)

Méthode : lecture des producteurs (`Suggest`, `suggest`, `say`, `SetTabLines`, `add`) de chaque module, vérité contre les
tables Forever / la doc API / les guides, utilité (action possible ?), formulation. Les modules déjà corrigés par QA-SYSTEMS
(S1–S32) ne sont pas redits.

| id | module : ligne | constat | correction |
|---|---|---|---|
| A1 | **Edge2** `lootLeft` | comptait CHAQUE kill comme « corps pas fouillé » : faux pour les bêtes sans butin | ✅ ne compte que les corps où le jeu signale du butin (`CanLootUnit(guid)` à la fin du combat), ≥ 2 : « Fouille tes corps : 2 avec du butin derrière toi » |
| A2 | **Edge2** `turninLevel` | même conseil que le geste d'expert « rends tes quêtes » de Coach | ✅ déplacé dans Coach (un seul propriétaire) ; « Avantages » = 7 faits |
| A3 | **QuickFight** `EAT_NAMES` (l. 115) | les plats Forever ont l'aura « Denrée nutritive » / « Boisson nutritive » (sorts 1248377 / 1249906) : la barre croit que tu ne manges pas (bouton « Manger » + minuteur faux) | ajouter `C_Spell.GetSpellName(1248377)`, `(1249906)` (et 433 / 430) aux noms au chargement |
| A4 | **QuickFight** `BANDAGED_NAMES` (l. 117) | nom frFR réel : « Un bandage a été appliqué récemment » (11196) et « Bandage appliqué récemment » (1305047) ; seul 11196 est testé par ID | `S.BANDAGED` → tester 11196 **et** 1305047 ; noms par `C_Spell.GetSpellName` |
| A5 | **Stats** `EAT` (l. 322) et **Export** `EAT` (l. 356) | même trou que A3 : le temps de repos par combat ne compte pas les plats Forever | mêmes noms (1248377 / 1249906) |
| A6 | **Quests** `xpCache` (l. 154) | l'XP de récompense est gardée pour toujours : après un niveau, `GetQuestLogRewardXP` change (pénalité) mais pas le cache | vider `xpCache` sur `PLAYER_LEVEL_UP` |
| A7 | **Gear** l. 293 (chasseur) | « son DPS compte triple » : chiffre sans source | « Ton arc / fusil fait l'essentiel des dégâts : son DPS pèse plus que tout le reste ; l'Agilité donne PA à distance + critique + armure. Arme lente (≥ 2,8 s) = Visée et Flèches multiples plus fortes (Forever : elles partagent leur recharge). Mailles au niv 40 (cuir avant), la mêlée compte peu. » |
| A8 | **Prof** l. 437 (cuisine) | « plats Forever = petit bonus d'XP + stats » : vague | « Cuisine : cuisine la viande de tes zones au feu de camp (orange = 100 %) ; plats Forever « Bien nourri » = +5 % d'XP des monstres + une stat. » |
| A9 | **Leveling** l. 392 (`PLAYER_CAMPING`) | « Déconnecte-toi plutôt à une auberge : ton repos y monte 4 fois plus vite » : faux si le joueur a le Sac de couchage douillet de Forever (repos hors ville) | se taire si le sac est connu / posé (à vérifier en jeu : ID de l'objet), sinon garder |
| A10 | **Leveling** `MILESTONES[40]`, **Session** monture 40 | niveau / prix de la monture Forever non vérifiés | garder, mais « (Classic : à vérifier sur Forever) » dans l'infobulle tant que non confirmé |
| A11 | **Hunter** `hungry` | conseil juste, mais au mauvais moment (toutes les 4 min) | le proposer quand tu t'assois pour manger (aura de repas) : 0 s perdue — même texte |
| A12 | **Leveling** repos (onglet Infos) | « Ton repos double l'XP des monstres (19 % d'un niveau) » : vrai, info ; Coach dit le geste (« tue ceux sur ton chemin ») | rien (pas dans « Maintenant », pas de doublon visible) |
| A13 | **Dungeon** « Munitions (≈ 1 000 pour un donjon) » | estimation, marquée ≈ | rien |
| A14 | **Quick** « Pas de monture : Aspect du guépard (+30 % de vitesse) » | vrai (sort 5118, guides Forever) | rien ; Coach `cheetah` se tait quand ce bouton est là |

Le reste est **vrai et utile** tel quel : Chasseur (humeur 75 / 100 / 125 %, munitions mesurées, aspect, marque, tir
auto, FD → piège, dressage), Combat (récap de mort, « Récupère avant le pull »), Rares (après S5), Stats (chiffres datés),
Groupe (« Attends X : la quête ouvre une suite »), Vitesse (choix gardien / corps chiffré), Guichets (prix réels),
Inventaire (achat mesuré, réparation), Talents (après S4), Métiers (après S8), Donjon (après S3 / S6 / S22), barres
Quick* (après S2), HV (scan frais, liquide).

## 5. Propositions exactes aux propriétaires (pas modifiées ici)

* QuickFight (A3, A4) : `local EAT_NAMES = { … }` complété au chargement :
  `for _, id in ipairs({ 433, 430, 1248377, 1249906 }) do local n = spellName(id) if n then EAT_NAMES[n] = (id == 430 or id == 1249906) and "d" or "f" end end`
  et `BANDAGED = { [11196] = true, [1305047] = true }` + leurs noms.
* Stats / Export (A5) : mêmes noms dans `EAT`.
* Quests (A6) : `self:RegisterEvent("PLAYER_LEVEL_UP", function() xpCache = {} end)`.
* Gear (A7), Prof (A8) : textes exacts ci-dessus.
* Hunter (A11) : `CheckPet` aussi sur `UNIT_AURA` du joueur quand une aura de repas apparaît.

## 6. À vérifier en jeu

1. Vie du joueur lisible hors combat (sinon Coach ne mesure rien : les conseils mesurés se taisent, sans erreur).
2. `GetPetActionInfo` : le nom du jeton actif (`PET_MODE_AGGRESSIVE` / `DEFENSIVE` / `PASSIVE` / `ASSIST`) et le
   Grondement de ta famille de familier (Forever a regroupé les familles).
3. `CanLootUnit(UnitGUID("target"))` sur un corps à butin (Avantages).
4. Aura « Bien nourri – bonus d'EXP » (1243969) présente quand le plat le donne ; plats de ton niveau dans tes sacs.
5. XP des quêtes basses (règle Classic 80 / 60 / 40 / 20 / 10 %) : comparer l'XP affichée avant / après un niveau.
6. `UnitGroupRolesAssigned` en donjon (sinon la classe du tank sert de repli).
