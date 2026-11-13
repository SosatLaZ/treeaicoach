# GEAR 5.0 — « un vrai assistant pour l'armure » (13 nov 2026)

Ce document dit **d'où vient chaque nombre** de l'assistant d'équipement, ce qui est lu dans le jeu, ce qui est un
modèle assumé, et ce que j'ai appris en lisant d'autres addons (idées seulement, aucun code ni table copiés).

## 1. La spé : pourquoi « TreeAI ne peut pas lire tes talents sur Forever » était faux

**Cause racine.** L'addon appelait `GetTalentTabInfo` / `GetTalentInfo` (API Classic Era). Sur WoW: Forever
(client 1.60.1, famille « Mainline », type de jeu `camelot`) ces fonctions n'existent pas : les talents sont un arbre
`C_Traits` à **3 groupes de nœuds**, un par arbre classique. La fenêtre de talents de Blizzard pour Forever
(`Blizzard_PlayerSpells/Camelot/ClassTalents/Blizzard_ClassTalentsFrame.lua`, branche `forever` de
Gethe/wow-ui-source, 1.60.1.70862) affiche l'en-tête de chaque arbre ainsi :

```
config  = C_ClassTalents.GetActiveConfigID()
          (repli du jeu : C_SpecializationInfo.GetCombatConfigIDForSpecGroup(C_SpecializationInfo.GetActiveSpecGroup()))
arbre   = C_Traits.GetConfigInfo(config).treeIDs[1]
groupes = C_Traits.GetGroupDisplayInfoByTreeID(arbre)      -- { groupID, orderIndex, displayName, icon }
points  = C_Traits.GetGroupCurrencyInfo(config, groupIDs)  -- [i].currencyInfos[1].spent = points dépensés
```

`GearSpec.lua` lit exactement cela. Autres faits du client : une seule « spécialisation » par classe sur Forever
(`SpecializationVisuals` : 1485 = chasseur…), donc `GetSpecialization()` ne dit rien de l'arbre ; `IsPlayerSpell` /
`IsSpellKnown` ne sont que des replis dépréciés, `C_SpellBook.IsSpellKnown` est la fonction du client.

**Ordre des lectures** (`T.Gear.Spec()` → `{ class, tree, name, source, conf, points }`) :

| source | lecture | coût |
|---|---|---|
| `api` | `GetTalentTabInfo` (Era) · groupes `C_Traits` (Forever) · `GetSpecialization` (clients à spés, ≥ 2 spés) | ≤ 6 appels, à la demande |
| `api` | nœuds `C_Traits` : `GetTreeNodes` → `GetNodeInfo.ranksPurchased` → `GetEntryInfo.definitionID` → `GetDefinitionInfo.spellID` → (arbre, rang, colonne) par `Data/TalentSpells.lua` | différé (`T:Spread`), nœud → talent mémorisé |
| `spells` | tous les rangs de chaque talent (`Data/TalentSpells.lua`, table `Talent.SpellRank` du client) testés par `C_SpellBook.IsSpellKnown` / `IsPlayerSpell` / `IsSpellKnown` ; sinon les noms du grimoire (Visée → Précision…) | différé, une fois par changement de talents |
| `manual` | 3 boutons (page Équipement), demandé UNE fois (`/tai spe`) | — |

La ligne « Spé : Précision (lue dans tes talents | déduite de tes sorts | choisie par toi) » est toujours affichée ;
un clic la change. `T.Gear.SpecProbe()` rend ce que chaque lecture a répondu sur le client réel (pour le rapport
d'erreurs de STABLE). `Talents.Read()` lit maintenant les rangs par les nœuds `C_Traits` (ou au moins les points par
arbre) : le conseiller de talents n'écrit plus « ne peut pas lire ».

**À vérifier en jeu** : je n'ai pas le client. Les signatures viennent de la documentation générée de Blizzard
(`SharedTraitsDocumentation.lua`, `SpecializationInfoDocumentation.lua`) et du code de sa propre fenêtre ; si un
appel répond autrement, la lecture suivante prend le relais et `SpecProbe` le dira.

## 2. Le modèle de valeur

### 2.1 Règles du jeu utilisées (Classic / Forever)

| règle | valeur | source |
|---|---|---|
| puissance d'attaque → dégâts | 14 PA = 1 DPS | page « Stats & Attributes » de Forever (warcrafttavern.com/forever/guides/stats), règle Classic |
| Force → PA mêlée | 2 (guerrier, paladin, chaman, druide), 1 (autres) | idem |
| Agilité → PA à distance | 2 (chasseur), 1 (voleur, guerrier) | idem |
| Agilité → PA mêlée | 1 (chasseur, voleur, druide félin) | idem |
| Agilité → critique | 1 % pour 53 (chasseur), 29 (voleur), 20 (autres) au niveau 60 | idem ; ramené au niveau par `niveau / 60` (approximation assumée, part faible de la valeur de l'Agilité) |
| critique | +100 % de dégâts → 1 % de critique = +1 % des dégâts de base | idem |
| toucher | 5 % de ratés contre une cible de ton niveau, 9 % contre un boss de raid | idem → plafond 5 % avant 58, 9 % ensuite ; au-delà le toucher vaut 0 |
| toucher / critique des objets | « scores » : 10 toucher = 1 %, 14 critique = 1 % | tables du client (ItemSparse : Gantelets diablosaures = `32:14 38:28` pour « +1 % critique, +28 PA ») ; en jeu `GetCombatRatingBonusForCombatRatingValue(indice, 100)` donne la conversion exacte à ton niveau, utilisée quand elle répond |
| sorts | ~80–100 % de la puissance des sorts sur un sort long, plateau après le niveau 20 | idem (qualitatif) → pas de DPS pour les lanceurs de sorts, des points |

### 2.2 Spés à dégâts physiques : un nombre en DPS

Chasseur (les 3 arbres), voleur, guerrier Armes / Fureur, paladin Vindicte (et par défaut), chaman Amélioration (et
par défaut), druide farouche. `B` = tes dégâts de base lus sur la fiche hors combat (`UnitRangedDamage` /
`UnitDamage` ÷ vitesse, arrondis à 5 DPS pour qu'un buff ne fasse pas danser les nombres ; fiche illisible : le DPS
de l'arme portée).

```
arme à distance (chasseur)   DPS × (1 + b × vitesse)   b = 0 avant le niveau 18, 0,10 avec Flèches multiples,
                                                         0,17 avec Visée (sort connu, ou Précision ≥ 11 points)
arme de mêlée                DPS × 1 (main droite), × 0,5 (main gauche en ambidextrie), × 0 pour le chasseur
                             (« stat stick ») et le druide en forme
PA / PA à distance           1/14
Force, Agilité               PA correspondante /14 (+ part de critique : (B/100) / Agi-par-1 %)
critique, toucher            (B/100) × % donné par le score ; toucher = 0 une fois le plafond atteint
```

Le terme `b × vitesse` : un tir spécial frappe avec les dégâts de l'arme (DPS × vitesse). Avec une Flèche multiple
toutes les 10 s on ajoute `DPS × vitesse / 10` ; Visée ajoute un coup d'arme net d'un tir auto perdu pendant
l'incantation, compté 0,07. C'est un **modèle** (rotation « à chaque recharge »), dit dans l'infobulle (« arme
lente = Flèches multiples plus fortes ») ; d'où le « ≈ ». Les munitions (DPS ajouté) et la hâte du carquois ne sont
pas dans le nombre d'un objet : les munitions ont leur propre ligne d'infobulle (« +x DPS par rapport à … »).

**Confort** (Endurance, Intelligence, Esprit, Armure, mana / 5 s) : ce ne sont pas des dégâts. Ils sont convertis
à part, en fraction de la stat principale — Endurance 0,45 (niv < 20), 0,40 (< 40), 0,35 (< 58), 0,25 (58+), × 0,6 en
groupe (« Endurance par contexte ») ; Intelligence 0,15 (chasseur), 0,25–0,30 (paladin, chaman) ; Esprit 0,05 ;
Armure 0,01 par point. Le nombre affiché (« ≈ +3,2 DPS ») est la part **dégâts** seule ; le confort apparaît dans
le détail (« +5 Endurance : compté +0,3 (survie) »). Un objet qui fait **perdre** des dégâts n'est jamais « mieux »
pour une spé à dégâts, même avec plus d'Endurance (« ≈ -0,7 DPS, mais plus de survie »).

### 2.3 Lanceurs de sorts, soigneurs, tanks : des points, affichés en %

Stat principale = 1 ; le nombre affiché est « +12 % » = 12 % de mieux que la pièce portée (« vaut 3,4 fois la pièce
portée » au-delà de +100 %). Poids pour la montée, ce qui change avant 20 et à 58+ :

| profil | montée (20–57) | avant 20 | 58+ |
|---|---|---|---|
| mage, druide Équilibre | Int 1 · Puiss. sorts 1,3 · Esprit 0,45 · End 0,45 · mana/5 2 · 1 % toucher 9 · 1 % crit 9 | Esprit 0,7 · End 0,5 | Puiss. sorts 2,2 · Esprit 0,2 · End 0,3 · toucher 28 · crit 22 |
| démoniste | Int 0,8 · Puiss. sorts 1,3 · End 0,7 · Esprit 0,3 · toucher 9 · crit 7 | — | Puiss. sorts 2,2 · End 0,4 · toucher 28 · crit 20 |
| prêtre Ombre | Puiss. sorts 1,3 · Int 0,8 · Esprit 0,6 · End 0,5 · toucher 9 · crit 4 | — | Puiss. sorts 2,2 · Esprit 0,3 · toucher 28 · crit 12 |
| chaman Élémentaire | Int 1 · Puiss. sorts 1,2 · Esprit 0,3 · End 0,4 · toucher 9 · crit 9 | — | Puiss. sorts 2 · toucher 26 · crit 22 |
| soigneur (prêtre, druide, chaman Resto) | Int 1 · Soins 1 · Esprit 0,8 · End 0,4 · mana/5 2,5 · crit 5 | dégâts des sorts 0,8 | Soins 1,2 · mana/5 3 |
| paladin Sacré | Int 1 · Soins 1 · mana/5 2,5 · Esprit 0,3 · End 0,4 · crit 8 | — | — |
| tank (guerrier / paladin Protection) | End 1 · Défense 1 · Armure 0,1 · Agi 0,6 · Force 0,6 · 1 % esquive / parade 12 · blocage 6–8 | — | — |

Ces poids sont des **conventions de classement** (ordre de grandeur des guides Classic : au 60 un mage vaut ~1 % de
toucher ≈ 13 points de dégâts des sorts, 1 % de critique ≈ 10 ; en montée l'Intelligence et l'Endurance réduisent
les pauses), pas des mesures : c'est pourquoi ces spés reçoivent un pourcentage relatif et jamais un « DPS ».
Le toucher des sorts vaut 0 au-delà de 3 % (cible de ton niveau) / 16 % (boss de raid au 60).

### 2.4 Lecture d'un objet — « jamais une devinette »

1. `C_Item.GetItemStats(lien)` / `GetItemStats` (suffixes aléatoires compris) ;
2. sinon les tables du client (`Data/Gear.lua`, 5 090 objets) si le lien n'a pas de suffixe aléatoire ; la vitesse
   des armes vient toujours de ces tables ;
3. sinon l'infobulle (`C_TooltipInfo.GetHyperlink` / `GetInventoryItem`) : « +N Stat », armure, DPS, vitesse,
   « Unique », ensemble (n/m), durabilité, enchantement / lunette (ligne `ItemEnchantmentPermanent`) ;
4. rien de lisible → `known = false` : « inconnu », aucun nombre, aucune comparaison (et jamais « mieux » contre un
   objet porté illisible).

Autres règles : armure par niveau (mailles au 40 pour chasseur / chaman, plaques au 40) ; arme de jet jamais
proposée à un chasseur à la place de l'arc ; arme dont la maîtrise n'est pas apprise = « à apprendre chez un maître
d'armes » (seulement si la lecture des maîtrises marche sur ce client) ; objets réservés à d'autres classes (masque
des tables) ; objets de test restés dans les tables (« [PÉRIMÉ] … », « 3800 Test 2h Axe 63 blue ») jamais
proposés ; enchantement / lunette de l'objet porté compté contre l'échange quand il se lit, signalé sinon ;
niveau maximum respecté (rien « au niveau 61 »).

## 3. « Stuff rentable »

Par emplacement, un verdict : **Équipe** (sacs, lus en direct) · **Achète** (marchand / HV, dans le budget libre)
· **Fais la quête X** · **Donjon X** · **Fais fabriquer** · **trop cher** · **Attends le niveau N** (un objet
≥ 15 % meilleur arrive dans ≤ 2 niveaux, ou l'achat serait remplacé au niveau suivant) · **plus tard** (source pas
encore faisable) · **Garde**. Classement : `gain ÷ (1 + minutes + coût ÷ valeur de ta minute)` ; la valeur de la
minute est ton or/heure mesuré (Stats) sinon une valeur par niveau — elle ne sert qu'à **classer**, jamais affichée.
Durée d'utilité : niveau où un objet ≥ 10 % meilleur des tables devient portable. Budget : ton or moins la réserve
« monture du niveau 40 » (0 / 50 / 100 po, demandé une fois entre 30 et 39 ; ≈ 100 po en Classic pour la monture
et l'apprentissage — c'est TA réserve, pas un prix lu). Sources : sacs (API), récompenses du journal
(`GetQuestLogItemLink`), QuestieDB en lecture s'il est là (marchands, récompenses, monstres / boss), prix HV de
`T.Auction` s'il a scanné, artisanat (tables du client + métiers du groupe).

## 4. Ce que j'ai appris d'autres addons (idées seulement)

- **Pawn** (VgerMods/Pawn, README) : une « échelle » = une liste stat → points ; score d'un objet = somme
  pondérée ; flèche verte sur les améliorations ; onglet de comparaison (porté à gauche, candidat à droite) ; les
  effets non convertibles en stats ne sont pas notés ; poids par défaut par spé venus de simulateurs. Repris comme
  idées : un nombre par objet, la flèche dans les sacs, le détail par stat. **Différence voulue** : pour les spés
  physiques je n'utilise pas une échelle relative mais les règles du jeu (14 PA = 1 DPS…), ce qui donne un nombre
  en DPS vérifiable par le joueur sur sa fiche.
- Guides Classic (Icy Veins, Wowhead, warcrafttavern) : priorités « toucher jusqu'au plafond > critique /
  Agilité > PA » pour le chasseur au 60 ; pour la montée, l'arme à distance d'abord. Cohérent avec le modèle :
  à 30, +1 DPS d'arc ≈ 1,3 DPS, +1 Agi ≈ 0,15 DPS.

## 5. Coût (mesuré sur le simulateur, chasseur 30, équipement presque vide = pire cas ; 50 kinstr ≈ 1 ms)

| travail | avant (4.4) | 5.0 |
|---|---|---|
| plan complet | ~490 kinstr, 1,7 Mo de déchets, tranche la plus lourde 95 kinstr ; relancé à chaque changement de sac (5 s) et toutes les 10 min | ~300 kinstr, 65 tranches ≤ 12–20 kinstr ; seulement à la demande (page ouverte, niveau gagné, marchand / HV, quête rendue) |
| objet équipé | plan complet | son emplacement seul : ~50 kinstr, par tranches |
| changement de sac | plan complet 5 s plus tard | 1,4 kinstr (le sac touché) ; verdict en cache par lien |
| `T.Gear.Score(lien)` répété | — | ~30 instructions (cache) |
| `T.Gear.Spec()` / poids répétés | relus à chaque appel | ~15 instructions (cache, périmé par les événements) |
| au repos | cycle AutoRefresh du cœur tenu éveillé | rien : pas de méthode AutoRefresh, pas de minuterie |

- Le premier plan n'est jamais calculé « pour rien » : sans plan, les sacs suffisent pour dire « Équipe X ».
- Spé : lecture légère à la demande après un événement de talents ; lectures longues (nœuds, sorts) différées par
  `T:Spread`, jamais en combat ; la question « Quelle est ta spé ? » n'est posée qu'après une 2e lecture (25 s après
  la connexion, quand le jeu a chargé les talents).

## 6. Pas fait / limites connues

- Bonus d'ensemble : la présence d'un ensemble (n/m) est lue dans l'infobulle, mais le bonus n'est pas chiffré.
- Hâte du carquois et DPS des munitions : pas dans le nombre d'un objet (les munitions ont leur ligne).
- Coût de réparation : le jeu ne le donne que chez un réparateur ; la page montre l'usure par emplacement.
- Butin de donjon : seulement ce que QuestieDB (ou le module Donjon) connaît ; pas de table de butin copiée.
- La conversion Agilité → critique par niveau est une approximation linéaire (part faible de la valeur).
