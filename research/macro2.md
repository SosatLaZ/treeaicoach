# MACRO2 — revue experte des macros TreeAI (WoW: Forever frFR)

Généré depuis `treeaicoach/wow/macros` (même source que `Data/Macros.lua`). Corps montrés au niveau 60, toutes options présentes (en jeu : seules les lignes / clauses dont le sort est appris restent ; `item:99999` = meilleur objet du sac ; `13`/`14` = bijou « Utiliser »).

## Règles vérifiées pour chaque macro (linter + tests)

- Noms frFR **exacts** du client Forever (build 1.60.1.70862, table SpellName frFR de wago) : 100 % des sorts de la base comparés (`test_spell_names_match_french_client_table`). En jeu le nom vient de l'ID (GetSpellInfo) : sans rang, le meilleur rang appris est lancé ; repli sur le nom FR.
- ≤ 255 octets UTF-8 pour **chaque** rendu réel (9 classes × 3 spés × 18 niveaux, racial + bijoux compris).
- Aucun sort en clair : chaque clause `/cast` est un jeton de la base → un sort inconnu est refusé à la génération.
- Syntaxe des conditions : unités connues, `mod:shift/ctrl/alt`, `@mouseover` toujours qualifié (`harm`/`help`/`exists`), pas de `[harm,help]` contradictoire, pas de clause toujours vraie avant une autre, `!` devant le nom, `reset=` valide (`N/combat/target/shift/alt/ctrl`), `#showtooltip` en 1re ligne.
- Une seule action sur le temps de recharge global par appui (les attaques au prochain coup, cooldowns, raciaux, bijoux et ordres au familier peuvent accompagner).
- Bascules jamais coupées : `!Tir automatique`, `!Tir` (baguette), aspects, formes, Aura de précision ; Camouflage/Rôder/Forme d'Ombre gardés par `[nostealth]`/`[noform]`.
- `/castsequence` toujours avec `reset=` ; jamais Feindre la mort + `reset=combat`.
- Ciblage Classic : survol hostile > cible (`[@mouseover,harm,nodead][]`), soins survol > cible amicale > toi, Alt = toi ; pas de `@focus`.
- Consommables (`item:ID`) réécrits quand le sac change, **hors combat uniquement** (EditMacro est interdit en combat) ; sur Forever l'écriture passe par « Macros à mettre à jour : clique pour les mettre à jour ».
- Niveau : une variante n'est construite que si tous ses sorts obligatoires sont dans le grimoire ; à chaque sort appris la macro grandit (hors combat, même ligne cliquable) et les nouvelles macros sont proposées.
- Druide : `[form:N]` réécrit d'après la vraie barre de formes (un druide sans Forme aquatique a le félin en 2).

## Panneau (TIDY)

Liste groupée **Installées / Prêtes à installer / Plus tard** (icône du sort, touche conseillée ou niveau), fiche empilée par un seul moteur (icône + nom, à quoi ça sert, **Où la mettre**, état « sur ta barre / pas encore », texte exact, pourquoi), grille de boutons de même largeur : « Installer tout », « Installer », « Glisser sur la barre » (met sur le curseur la prochaine macro qui n'est sur aucune barre ; une ligne de la liste se glisse aussi), « Mettre à jour », « Diagnostic » (la fiche devient la liste OK / X + raison par macro), « Copier-coller », « Ouvrir les macros », « Retirer les macros TAI ». Plus rien dans le chat : ligne d'état sous la liste.

QuickFight (barre « Raccourcis ») garde ses boutons contextuels (pull, familier mort, repas…) : ce ne sont pas des macros du jeu, aucune macro n'est en double ; ils lisent les mêmes choix d'objets (`Macros.NewContext`). Seul changement : ses aspects sont lancés avec `!`.

## Changements

- **TAI FD Piege** (HUNTER) : réécrite : `/castsequence reset=combat/8` remplacé par `[combat]` (Feindre la mort fait sortir du combat, ce qui relançait la séquence au début : le 2e clic refaisait Feindre la mort au lieu du piège) ; `/stopcasting` et `/petfollow` seulement en combat ; hors combat elle pose directement le piège (pré-pull).
- **TAI Familier** (HUNTER) : Nourrir : `[mod:ctrl,pet]` (le familier doit être là) ; la ligne d'objet optionnelle ne fait plus disparaître la macro quand le sac n'a pas de nourriture (bug : sans nourriture, Appel/Guérison disparaissaient).
- **TAI Pet** (HUNTER) : `/petattack` sans survol exige une cible hostile vivante (`[nomod,harm,nodead]`).
- **TAI Grondement** (HUNTER) : plus dans le jeu par défaut (réglage ponctuel, place libérée pour TAI Monture).
- **TAI Salve** (HUNTER) : nouvelle (niveau 40, groupe, option) : Salve `[@cursor]`.
- **TAI Pieges** (HUNTER) : texte : les pièges ne se posent que hors combat (règle Classic).
- **TAI Voyage** (DRUID) : `!` ajouté devant Forme de félin (recliquer en intérieur faisait quitter la forme).
- **TAI Provoc** (DRUID) : variante Forme d'ours redoutable : au niveau 40 la Forme d'ours quitte le grimoire, la macro disparaissait.
- **TAI Charge** (DRUID) : option (place).
- **TAI Evocation** (MAGE) : option : un sort seul sans condition n'apporte rien par rapport au grimoire.
- **TAI Bouclier** (SHAMAN) : option (sort seul).
- **TAI Loup** (SHAMAN) : option (sort seul).
- **TAI Armure** (WARLOCK) : option (buff de 30 min).
- **TAI Bannir** (WARLOCK) : option.
- **TAI Monture** (toutes classes) : nouvelle : monture du sac (objet Divers/Monture choisi hors combat), recliquer = descendre, Maj = Pierre de foyer.
- **TAI Survie** (toutes classes) : pierres de soins : niveau requis réel (12/24/36/48) — une Pierre de soins majeure n'est plus proposée à un niveau 28.
- **TAI Bandage** (toutes classes) : Secourisme requis pour UTILISER (1/20/50/75/100/125/150/175/200/225), plus celui de fabrication.
- **Linter / générateur** : validateur de conditions (`@mouseover` non qualifié, conditions contradictoires, clause toujours vraie, `!` mal placé, `reset=` invalide), règle « sort en clair interdit », règle des bascules (`!`), règle des séquences (`reset=` obligatoire, pas de Feindre la mort + `reset=combat`), champ « Où la mettre » exporté pour chaque macro (`o`), table des formes de druide (`D.forms`).
- **Gestionnaire en jeu** : « Nouvelles macros prêtes : clique pour installer » crée vraiment les nouvelles macros (avant : simple mise à jour des installées, rien n'était créé) ; niveaux requis de `Data/Macros.lua` gardés quand la liste client met 1 (bandages) ; objets `hearth` / `mount` ; une ligne d'objet optionnelle ne supprime plus une macro de sorts ; messages dans le panneau au lieu du chat ; diagnostic dans le panneau.

## Macros communes

### TAI Repas — Mange + boit (Maj : boire seul, Ctrl : manger seul)

Classes : PALADIN, HUNTER, PRIEST, SHAMAN, MAGE, WARLOCK, DRUID · touche Maj+B · par défaut  
Où : Barre secondaire, sous la barre principale (raccourci conseillé : Maj+B).

```
#showtooltip
/use [nomod:shift] item:99999
/use [nomod:ctrl] item:99999
```
Pourquoi : Nourriture et boisson du sac les plus fortes pour ton niveau, mises à jour quand ton sac change (les invoquées passent d'abord, elles sont gratuites). Manger et boire ne déclenchent pas de temps de recharge global : un seul appui lance les deux.

### TAI Repas — Mange la meilleure nourriture du sac

Classes : WARRIOR, ROGUE · touche Maj+B · par défaut  
Où : Barre secondaire, sous la barre principale (raccourci conseillé : Maj+B).

```
#showtooltip
/use item:99999
```
Pourquoi : La meilleure nourriture utilisable du sac, mise à jour automatiquement.

### TAI Survie — Pierre de soins, puis potion de soins au 2e appui

Classes : toutes · touche Maj+H · par défaut  
Où : Barre secondaire, sous la barre principale (raccourci conseillé : Maj+H).

```
#showtooltip
/castsequence reset=combat item:99999, item:99999
```
Pourquoi : Les deux ont des temps de recharge séparés : pierre d'abord (gratuite), potion ensuite. La séquence revient au début à la fin du combat.

### TAI Mana — Gemme de mana, puis potion de mana

Classes : MAGE · touche Maj+J · par défaut  
Où : Barre secondaire, sous la barre principale (raccourci conseillé : Maj+J).

```
#showtooltip
/castsequence reset=combat item:99999, item:99999
```
Pourquoi : Gemme (gratuite, recharge séparée) puis potion au 2e appui.

### TAI Mana — Meilleure potion de mana du sac

Classes : PALADIN, HUNTER, PRIEST, SHAMAN, WARLOCK, DRUID · touche Maj+J · par défaut  
Où : Barre secondaire, sous la barre principale (raccourci conseillé : Maj+J).

```
#showtooltip
/use item:99999
```
Pourquoi : La potion de mana la plus forte utilisable à ton niveau.

### TAI Bandage — Bandage : survol > cible amicale > toi (Alt : toi)

Classes : toutes · touche Maj+N · par défaut  
Où : Barre secondaire, sous la barre principale (raccourci conseillé : Maj+N).

```
#showtooltip
/use [mod:alt,@player][@mouseover,help,nodead][@target,help,nodead][@player] item:99999
```
Pourquoi : Bandage le plus fort permis par ton Secourisme, posé sur l'allié survolé sans changer de cible.

### TAI Munitions — Équipe les meilleures munitions (arc/arbalète = flèches, fusil = balles)

Classes : HUNTER · touche - · par défaut  
Où : Barre de côté : un clic quand tu en as besoin.

```
#showtooltip
/equip item:99999
```
Pourquoi : Choisit flèches ou balles selon ton arme, au meilleur niveau dans le sac.

### TAI Poisons — Poison main droite (Maj : main gauche)

Classes : ROGUE · touche - · par défaut  
Où : Barre de côté : un clic quand tu en as besoin.

```
#showtooltip
/use [nomod] item:99999
/use [mod:shift] item:99999
/use [mod:shift] 17; 16
```
Pourquoi : Applique le poison du sac sur l'arme (16 = main droite, 17 = main gauche).

### TAI Baguette — Baguette sur survol ou cible (ne s'arrête pas si on rappuie)

Classes : PRIEST, MAGE, WARLOCK · touche - · par défaut  
Où : Barre de côté : un clic quand tu en as besoin.

```
#showtooltip
/cast [@mouseover,harm,nodead][] !Tir
```
Pourquoi : Le « ! » empêche de couper la baguette en rappuyant : économise ton mana entre deux sorts.

### TAI Monture — Monture (Maj : Pierre de foyer) ; descend si tu es monté

Classes : toutes · touche Maj+Z · par défaut  
Où : Barre secondaire : entre deux quêtes (et Maj+clic pour rentrer).

```
#showtooltip
/use [mod:shift] item:99999
/stopmacro [mod:shift]
/dismount [mounted]
/use [nomounted] item:99999
```
Pourquoi : La monture du sac (choisie automatiquement, hors combat) ; recliquer te fait descendre. Avant ta première monture, la macro ne lance que la Pierre de foyer.

### TAI Marqueur — Crâne sur la cible (Maj : croix) pour le groupe

Classes : toutes · touche Ctrl+1 · option  
Où : Barre secondaire, sous la barre principale (raccourci conseillé : Ctrl+1).

```
/run SetRaidTarget("target",IsShiftKeyDown() and 7 or 8)
```
Pourquoi : Marque la cible à tuer en premier (crâne) ou à contrôler (croix).

## WARRIOR

### TAI Charge — Hors combat : Charge (Combat) ; en combat : Interception (Berserker)

Spé : Armes, Fureur · solo + groupe · touche 1 · dès le niveau 4 · par défaut  
Où : Case 1 de la barre principale (touche 1).

```
#showtooltip
/startattack [harm,nodead]
/cast [nocombat,nostance:1] Posture de combat; [nocombat] Charge; [nostance:3] Posture berserker; Interception
```
Pourquoi : Même touche toute la partie : hors combat elle passe en Posture de combat puis charge ; en combat elle passe en Berserker puis Interception (niveau 30). /startattack lance l'attaque auto pour générer de la rage.

### TAI Charge — Hors combat : Charge ; en combat : retour en Posture défensive

Spé : Protection · solo + groupe · touche 1 · dès le niveau 4 · par défaut  
Où : Case 1 de la barre principale (touche 1).

```
#showtooltip
/startattack [harm,nodead]
/cast [nocombat,nostance:1] Posture de combat; [nocombat] Charge; [nostance:2] Posture défensive
```
(variante 2, avant le sort principal)
```
#showtooltip
/startattack [harm,nodead]
/cast [nostance:1] Posture de combat; Charge
```
Pourquoi : Tank : Charge hors combat (Posture de combat), puis le même bouton te remet en Posture défensive dès que le combat commence — tu ne restes jamais en mauvaise posture.

### TAI Attaque — Frappe mortelle (Maj : Heurtoir)

Spé : Armes · solo + groupe · touche 2 · dès le niveau 1 · par défaut  
Où : Case 2 de la barre principale (touche 2).

```
#showtooltip
/startattack
/cast [mod:shift] Heurtoir; Frappe mortelle
```
(variante 2, avant le sort principal)
```
#showtooltip
/startattack
/cast [mod:shift] Heurtoir; Frappe transperçante
```
Pourquoi : Ton coup principal sur le temps de recharge global : Frappe transperçante en montée, Frappe mortelle dès que le talent est pris (réduit aussi les soins reçus). Maj = Heurtoir.

### TAI Attaque — Sanguinaire (Maj : Tourbillon en Berserker)

Spé : Fureur · solo + groupe · touche 2 · dès le niveau 1 · par défaut  
Où : Case 2 de la barre principale (touche 2).

```
#showtooltip
/startattack
/cast [mod:shift,stance:3] Tourbillon; [mod:shift] Heurtoir; Sanguinaire
```
(variante 2, avant le sort principal)
```
#showtooltip
/startattack
/cast [mod:shift,stance:3] Tourbillon; [mod:shift] Heurtoir; Frappe transperçante
```
Pourquoi : Coup principal de Fureur. Tourbillon n'existe qu'en Posture berserker : sans elle Maj lance Heurtoir.

### TAI Attaque — Heurt de bouclier (Maj : Vengeance)

Spé : Protection · solo + groupe · touche 2 · dès le niveau 1 · par défaut  
Où : Case 2 de la barre principale (touche 2).

```
#showtooltip
/startattack
/cast [mod:shift] Vengeance; Heurt de bouclier
```
(variante 2, avant le sort principal)
```
#showtooltip
/startattack
/cast [mod:shift] Vengeance; Fracasser armure
```
(variante 3, avant le sort principal)
```
#showtooltip
/startattack
/cast Frappe transperçante
```
Pourquoi : Menace d'abord : Fracasser armure puis Heurt de bouclier ; Vengeance (après une esquive / parade / blocage) sur Maj, c'est ta meilleure menace par rage.

### TAI Frappe — Frappe héroïque (Maj : Enchaînement) — vide la rage

Spé : toutes · solo + groupe · touche 3 · dès le niveau 1 · par défaut  
Où : Case 3 de la barre principale (touche 3).

```
#showtooltip
/startattack
/cast [mod:shift] Enchaînement; Frappe héroïque
```
Pourquoi : Frappe héroïque remplace ta prochaine attaque blanche et ne déclenche pas le temps de recharge global : appuie quand tu as trop de rage. Maj = Enchaînement (2 cibles).

### TAI Fulgu — Fulgurance (passe en Posture de combat si besoin)

Spé : Armes, Fureur · solo + groupe · touche 4 · dès le niveau 12 · par défaut  
Où : Case 4 de la barre principale (touche 4).

```
#showtooltip
/startattack
/cast [nostance:1] Posture de combat; Fulgurance
```
Pourquoi : Fulgurance n'existe qu'en Posture de combat après une esquive : 1er appui change de posture (Maîtrise tactique garde ta rage), 2e appui frappe.

### TAI Exec — Exécution sous 20 % (Maj : Brise-genou)

Spé : toutes · solo + groupe · touche E · dès le niveau 8 · par défaut  
Où : Barre principale, près de tes sorts (raccourci conseillé : E).

```
#showtooltip
/startattack
/cast [mod:shift] Brise-genou; Exécution
```
(variante 2, avant le sort principal)
```
#showtooltip
/startattack
/cast Brise-genou
```
Pourquoi : Exécution convertit toute ta rage en dégâts sous 20 % de vie ; Brise-genou empêche les fuyards de partir chercher de l'aide.

### TAI Interrupt — Volée de coups (Berserker) ou Coup de bouclier

Spé : toutes · solo + groupe · touche Maj+4 · dès le niveau 12 · par défaut  
Où : Barre secondaire, sous la barre principale (raccourci conseillé : Maj+4).

```
#showtooltip
/cast [stance:3] Volée de coups; Coup de bouclier
```
Pourquoi : Interrompt un sort : Volée de coups en Berserker, sinon Coup de bouclier (bouclier requis).

### TAI Provoc — Provocation (survol), Ctrl : Cri de défi

Spé : Protection · groupe · touche T · dès le niveau 10 · par défaut  
Où : Barre principale, près de tes sorts (raccourci conseillé : T).

```
#showtooltip
/cast [mod:ctrl] Cri de défi; [nostance:2] Posture défensive; [@mouseover,harm,nodead][] Provocation
```
Pourquoi : Reprend un monstre sur un allié survolé sans perdre ta cible ; passe en Posture défensive d'abord. Ctrl = Cri de défi quand tout le pack s'échappe.

### TAI Provoc — Coup railleur (Combat) pour reprendre un monstre

Spé : Armes, Fureur · groupe · touche T · dès le niveau 16 · option  
Où : Barre principale, près de tes sorts (raccourci conseillé : T).

```
#showtooltip
/cast [nostance:1] Posture de combat; Coup railleur
```
Pourquoi : Hors tank : reprend 6 s un monstre qui tape le soigneur.

### TAI Cris — Cri de guerre (Maj : Cri démoralisant)

Spé : toutes · solo + groupe · touche 5 · dès le niveau 1 · par défaut  
Où : Case 5 de la barre principale (touche 5).

```
#showtooltip
/cast [mod:shift] Cri démoralisant; Cri de guerre
```
Pourquoi : Cri de guerre = puissance d'attaque pour tout le groupe ; Cri démoralisant réduit les dégâts des monstres autour (bonne menace de zone pour le tank).

### TAI Rage — Rage sanguinaire (+ Rage berserker en Berserker)

Spé : toutes · solo + groupe · touche R · dès le niveau 10 · par défaut  
Où : Barre principale, près de tes sorts (raccourci conseillé : R).

```
#showtooltip Rage sanguinaire
/cast Rage sanguinaire
/cast [stance:3] Rage berserker
```
Pourquoi : Rage sanguinaire est hors temps de recharge global : rage gratuite au début du combat. En Berserker, le même appui lance Rage berserker (immunité peur).

### TAI Burst — Témérité (+ Souhait mortel) + racial + bijoux

Spé : Armes, Fureur · solo + groupe · touche Maj+R · dès le niveau 1 · par défaut  
Où : Barre secondaire, sous la barre principale (raccourci conseillé : Maj+R).

```
#showtooltip
/cast [mod:shift] Souhait mortel; [nostance:3] Posture berserker; Témérité
/cast Fureur sanguinaire
/cast Berserker
/use 13
/use 14
```
(variante 2, avant le sort principal)
```
#showtooltip
/cast Souhait mortel
/cast Fureur sanguinaire
/cast Berserker
/use 13
/use 14
```
(variante 3, avant le sort principal)
```
#showtooltip
/cast Fureur sanguinaire
/cast Berserker
/use 13
/use 14
```
Pourquoi : Tous tes gros cooldowns sur une touche : un seul est sur le temps de recharge global, les raciaux et bijoux « Utiliser » partent avec (lignes ajoutées seulement si tu les as).

### TAI Mur — Mur protecteur (Maj : Dernier rempart)

Spé : Protection · solo + groupe · touche Maj+V · dès le niveau 20 · par défaut  
Où : Barre secondaire, sous la barre principale (raccourci conseillé : Maj+V).

```
#showtooltip
/cast [mod:shift] Dernier rempart; [nostance:2] Posture défensive; Mur protecteur
```
(variante 2, avant le sort principal)
```
#showtooltip
/cast Dernier rempart
```
Pourquoi : Bouton panique du tank : -75 % de dégâts (Posture défensive requise), Maj = Dernier rempart.

### TAI Blocage — Maîtrise du blocage (Posture défensive)

Spé : Protection · solo + groupe · touche Q · dès le niveau 16 · par défaut  
Où : Barre principale, près de tes sorts (raccourci conseillé : Q).

```
#showtooltip
/cast [nostance:2] Posture défensive; Maîtrise du blocage
```
Pourquoi : Blocage garanti = déclenche Vengeance ; le geste n°1 du tank sur les élites.

### TAI Tonnerre — Coup de tonnerre (Combat), Maj : Brise-genou

Spé : toutes · groupe · touche Maj+Q · dès le niveau 6 · option  
Où : Barre secondaire, sous la barre principale (raccourci conseillé : Maj+Q).

```
#showtooltip
/startattack
/cast [mod:shift] Brise-genou; [nostance:1] Posture de combat; Coup de tonnerre
```
Pourquoi : Ralentit les attaques de tous les monstres autour : menace de zone et moins de dégâts.

### TAI Victoire — Ivresse de la victoire après un kill

Spé : toutes · solo · touche Maj+2 · dès le niveau 20 · par défaut  
Où : Barre secondaire, sous la barre principale (raccourci conseillé : Maj+2).

```
#showtooltip
/startattack
/cast Ivresse de la victoire
```
Pourquoi : Rendue disponible par chaque monstre tué qui rapporte de l'XP : soin + dégâts gratuits, parfait en quête pour enchaîner sans manger.

### TAI Peur — Cri d'intimidation (Maj : Hurlement perçant)

Spé : toutes · solo + groupe · touche - · dès le niveau 22 · option  
Où : Barre de côté : un clic quand tu en as besoin.

```
#showtooltip
/cast [mod:shift] Hurlement perçant; Cri d’intimidation
```
Pourquoi : Fait fuir les monstres autour : sauve un pull raté (attention aux adds qui fuient).

## PALADIN

### TAI Lumiere — Lumière sacrée (Maj : Éclair lumineux) survol > cible > toi

Spé : toutes · solo + groupe · touche 1 · dès le niveau 1 · par défaut  
Où : Case 1 de la barre principale (touche 1).

```
#showtooltip
/cast [mod:shift,@mouseover,help,nodead][mod:shift,help,nodead][mod:shift,@player] Eclair lumineux; [mod:alt,@player][@mouseover,help,nodead][@target,help,nodead][@player] Lumière sacrée
```
Pourquoi : Soin sans changer de cible : survol d'abord, sinon ta cible amicale, sinon toi (Alt = toi). Éclair lumineux sur Maj : plus rapide et moins cher pour les petits dégâts.

### TAI Jugement — Jugement (Maj : Jugement rapide) + attaque

Spé : toutes · solo + groupe · touche 2 · dès le niveau 4 · par défaut  
Où : Case 2 de la barre principale (touche 2).

```
#showtooltip
/startattack [harm,nodead]
/cast [mod:shift,@mouseover,harm,nodead][mod:shift] Jugement rapide; [@mouseover,harm,nodead][] Jugement
```
Pourquoi : Le Jugement est hors temps de recharge global dans Forever et ne consomme plus le sceau : à utiliser à chaque recharge. Jugement rapide (talent Protection) a sa propre recharge.

### TAI Sceau — Sceau de piété (Maj : Croisé, Ctrl : Justice, Alt : Lumière)

Spé : Vindicte · solo + groupe · touche 3 · dès le niveau 1 · par défaut  
Où : Case 3 de la barre principale (touche 3).

```
#showtooltip
/cast [mod:shift] Sceau du Croisé; [mod:ctrl] Sceau de justice; [mod:alt] Sceau de lumière; Sceau d'autorité
```
(variante 2, avant le sort principal)
```
#showtooltip
/cast [mod:shift] Sceau du Croisé; [mod:ctrl] Sceau de justice; [mod:alt] Sceau de lumière; Sceau de piété
```
Pourquoi : Sceau d'autorité dès le talent (gros coups en arme lente), sinon Sceau de piété. Justice empêche les fuyards, Lumière te soigne en tapant.

### TAI Sceau — Sceau de fureur (Maj : piété, Ctrl : Justice, Alt : Sagesse)

Spé : Protection · solo + groupe · touche 3 · dès le niveau 1 · par défaut  
Où : Case 3 de la barre principale (touche 3).

```
#showtooltip
/cast [mod:shift] Sceau de piété; [mod:ctrl] Sceau de justice; [mod:alt] Sceau de sagesse; Sceau de fureur
```
(variante 2, avant le sort principal)
```
#showtooltip
/cast Sceau de piété
```
Pourquoi : Sceau de fureur (Forever) : dégâts Sacrés et bouclier d'absorption à chaque coup avec un bouclier équipé — le sceau du tank.

### TAI Sceau — Sceau de sagesse (Maj : piété, Ctrl : Lumière, Alt : Croisé)

Spé : Sacré · solo + groupe · touche 3 · dès le niveau 1 · par défaut  
Où : Case 3 de la barre principale (touche 3).

```
#showtooltip
/cast [mod:shift] Sceau de piété; [mod:ctrl] Sceau de lumière; [mod:alt] Sceau du Croisé; Sceau de sagesse
```
(variante 2, avant le sort principal)
```
#showtooltip
/cast [mod:ctrl] Sceau de lumière; [mod:alt] Sceau du Croisé; Sceau de piété
```
Pourquoi : En soigneur, Sceau de sagesse + Jugement rend du mana à tout le groupe.

### TAI Frappe — Frappe sacrée (Maj : Consécration) + attaque

Spé : toutes · solo + groupe · touche 4 · dès le niveau 6 · par défaut  
Où : Case 4 de la barre principale (touche 4).

```
#showtooltip
/startattack
/cast [mod:shift] Consécration; Frappe sacrée
```
Pourquoi : Frappe sacrée (Forever, niveau 6) est ton coup instantané de mêlée ; Consécration pour les packs (et la menace du tank).

### TAI Exorcisme — Exorcisme (Maj : Colère divine, Ctrl : Marteau de courroux)

Spé : toutes · solo + groupe · touche 5 · dès le niveau 20 · par défaut  
Où : Case 5 de la barre principale (touche 5).

```
#showtooltip
/cast [mod:shift] Colère divine; [mod:ctrl,@mouseover,harm,nodead][mod:ctrl] Marteau de courroux; [@mouseover,harm,nodead][] Exorcisme
```
Pourquoi : Exorcisme : gros dégâts sur morts-vivants et démons ; Marteau de courroux achève sous 20 %.

### TAI Marteau — Marteau de la justice (Maj : Repentir) — interrompt

Spé : toutes · solo + groupe · touche Maj+4 · dès le niveau 8 · par défaut  
Où : Barre secondaire, sous la barre principale (raccourci conseillé : Maj+4).

```
#showtooltip
/stopcasting
/cast [mod:shift,@mouseover,harm,nodead][mod:shift] Repentir; [@mouseover,harm,nodead][] Marteau de la justice
```
Pourquoi : /stopcasting coupe ton soin en cours pour étourdir tout de suite un lanceur de sorts.

### TAI Bulle — Bouclier divin (Ctrl : Imposition des mains)

Spé : toutes · solo + groupe · touche Maj+V · dès le niveau 6 · par défaut  
Où : Barre secondaire, sous la barre principale (raccourci conseillé : Maj+V).

```
#showtooltip
/stopcasting
/cast [mod:ctrl,@mouseover,help,nodead][mod:ctrl,help,nodead][mod:ctrl,@player] Imposition des mains; Bouclier divin
```
(variante 2, avant le sort principal)
```
#showtooltip
/stopcasting
/cast [mod:ctrl,@mouseover,help,nodead][mod:ctrl,help,nodead][mod:ctrl,@player] Imposition des mains; Protection divine
```
Pourquoi : Immunité d'urgence (interrompt ton sort) ; Ctrl = Imposition des mains sur l'allié survolé.

### TAI Purifier — Épuration / Purification : survol > cible > toi

Spé : toutes · solo + groupe · touche 6 · dès le niveau 8 · par défaut  
Où : Case 6 de la barre principale (touche 6).

```
#showtooltip
/cast [mod:alt,@player][@mouseover,help,nodead][@target,help,nodead][@player] Epuration
```
(variante 2, avant le sort principal)
```
#showtooltip
/cast [mod:alt,@player][@mouseover,help,nodead][@target,help,nodead][@player] Purification
```
Pourquoi : Retire poison / maladie (et magie avec Épuration) sans changer de cible.

### TAI BoP — Bénédiction de protection (Maj : liberté)

Spé : toutes · groupe · touche Maj+6 · dès le niveau 10 · par défaut  
Où : Barre secondaire, sous la barre principale (raccourci conseillé : Maj+6).

```
#showtooltip
/cast [mod:shift,@mouseover,help,nodead][mod:shift,help,nodead][mod:shift,@player] Bénédiction de liberté; [mod:alt,@player][@mouseover,help,nodead][@target,help,nodead][@player] Bénédiction de protection
```
Pourquoi : Sauve un allié frappé au corps à corps (le monstre change de cible) ; Maj = Liberté contre ralentissements.

### TAI Benediction — Puissance (Maj : Sagesse, Ctrl : Rois)

Spé : toutes · solo + groupe · touche 7 · dès le niveau 4 · par défaut  
Où : Case 7 de la barre principale (touche 7).

```
#showtooltip
/cast [mod:shift,help,nodead][mod:shift,@player] Bénédiction de sagesse; [mod:ctrl,help,nodead][mod:ctrl,@player] Bénédiction des rois; [help,nodead][@player] Bénédiction de puissance
```
Pourquoi : Sur ta cible amicale sinon sur toi : Puissance pour les physiques, Sagesse pour les lanceurs, Rois pour tous.

### TAI Aura — Aura de dévotion (Maj : concentration, Ctrl : vindicte)

Spé : toutes · solo + groupe · touche - · dès le niveau 1 · par défaut  
Où : Barre de côté : un clic quand tu en as besoin.

```
#showtooltip
/cast [mod:shift] Aura de concentration; [mod:ctrl] Aura de vindicte; Aura de dévotion
```
Pourquoi : Une touche, trois auras : armure (tank/solo), concentration (soins sans interruption), vindicte (dégâts renvoyés en farm).

### TAI Fureur — Fureur vertueuse (Maj : la retirer)

Spé : Protection · groupe · touche - · dès le niveau 10 · par défaut  
Où : Barre de côté : un clic quand tu en as besoin.

```
#showtooltip
/cancelaura [mod:shift] Fureur vertueuse
/stopmacro [mod:shift]
/cast Fureur vertueuse
```
Pourquoi : Indispensable pour tanker (menace des sorts Sacrés), à retirer en solo pour ne pas tout attirer.

### TAI Bouclier — Bouclier sacré + attaque

Spé : Protection · solo + groupe · touche Q · dès le niveau 30 · par défaut  
Où : Barre principale, près de tes sorts (raccourci conseillé : Q).

```
#showtooltip
/startattack
/cast Bouclier sacré
```
Pourquoi : Plus de blocage et dégâts Sacrés à chaque blocage : à garder actif en tankant.

### TAI Horion — Horion sacré (soin ou dégâts) (Maj : Vigile de Lumière)

Spé : Sacré · solo + groupe · touche Q · dès le niveau 30 · par défaut  
Où : Barre principale, près de tes sorts (raccourci conseillé : Q).

```
#showtooltip
/cast [mod:shift,@mouseover,help,nodead][mod:shift,help,nodead] Vigile de Lumière; [@mouseover,exists,nodead][exists,nodead][@player] Horion sacré
```
Pourquoi : Horion sacré soigne un allié ou frappe un ennemi selon qui est survolé / ciblé. Vigile : le prochain Horion sur cet allié soigne tout le groupe sans recharge.

### TAI Rez — Rédemption sur l'allié mort survolé

Spé : toutes · groupe · touche - · dès le niveau 12 · option  
Où : Barre de côté : un clic quand tu en as besoin.

```
#showtooltip
/cast [@mouseover,help,dead][help,dead] Rédemption
```
Pourquoi : Ressuscite le corps survolé sans devoir le cibler.

## HUNTER

### TAI Tir — Tir auto + familier + Invocation de faucon (Maj : Flèches multiples)

Spé : Maîtrise des bêtes · solo + groupe · touche 1 · dès le niveau 1 · par défaut  
Où : Case 1 de la barre principale : le bouton que tu cliques le plus, en boucle.

```
#showtooltip
/cast [mod:shift] Flèches multiples; [mod:ctrl] Tir des arcanes; Invocation de faucon
/cast !Tir automatique
/petattack [harm,nodead,nomod:alt]
```
(variante 2, avant le sort principal)
```
#showtooltip
/cast [mod:shift] Flèches multiples; Tir des arcanes
/cast !Tir automatique
/petattack [harm,nodead,nomod:alt]
```
(variante 3, avant le sort principal)
```
#showtooltip
/cast !Tir automatique
/petattack [harm,nodead,nomod:alt]
```
Pourquoi : Un seul bouton à marteler : « !Tir automatique » relance le tir auto sans jamais le couper, le familier attaque ta cible (Alt = il reste), puis ton tir instantané. Invocation de faucon (talent Bêtes) partage la recharge du Tir des arcanes : elle le remplace, Ctrl garde l'Arcane.

### TAI Tir — Tir auto + familier + Tir des arcanes (Maj : multiples, Ctrl : Visée)

Spé : Précision, Survie · solo + groupe · touche 1 · dès le niveau 1 · par défaut  
Où : Case 1 de la barre principale : le bouton que tu cliques le plus, en boucle.

```
#showtooltip
/cast [mod:shift] Flèches multiples; [mod:ctrl] Visée; Tir des arcanes
/cast !Tir automatique
/petattack [harm,nodead,nomod:alt]
```
(variante 2, avant le sort principal)
```
#showtooltip
/cast !Tir automatique
/petattack [harm,nodead,nomod:alt]
```
Pourquoi : « !Tir automatique » relance le tir auto sans jamais le couper, le familier part sur ta cible (Alt = il reste). Visée (Ctrl) se lance juste après un tir auto pour ne pas le décaler.

### TAI Marque — Marque du chasseur (survol) puis familier

Spé : toutes · solo + groupe · touche 2 · dès le niveau 6 · par défaut  
Où : Case 2 : premier clic de chaque combat, avant le Tir.

```
#showtooltip
/cast [@mouseover,harm,nodead][] Marque du chasseur
/petattack [nomod:alt,harm,nodead]
```
Pourquoi : Ouvre le combat : marque (monstre survolé en priorité) puis envoie le familier — il prend la menace avant ton premier tir.

### TAI Morsure — Morsure de serpent (Maj : vipère, Ctrl : scorpide)

Spé : toutes · solo + groupe · touche 3 · dès le niveau 4 · par défaut  
Où : Case 3 : une fois par monstre qui vit plus de 10 s.

```
#showtooltip
/cast [mod:shift,@mouseover,harm,nodead][mod:shift] Morsure de vipère; [mod:ctrl,@mouseover,harm,nodead][mod:ctrl] Piqûre de scorpide; [@mouseover,harm,nodead][] Morsure de serpent
```
Pourquoi : Une seule piqûre par cible, posable au survol sur un 2e monstre sans changer de cible. Vipère sur les lanceurs de sorts, scorpide sur les gros frappeurs.

### TAI Controle — Trait de choc (Maj : dispersion, Ctrl : effrayer bête, Alt : Intimidation)

Spé : toutes · solo + groupe · touche 4 · dès le niveau 8 · par défaut  
Où : Case 4 : quand un monstre fonce sur toi ou qu'un add arrive.

```
#showtooltip
/cast [mod:shift,@mouseover,harm,nodead][mod:shift] Flèche de dispersion; [mod:ctrl,@mouseover,harm,nodead][mod:ctrl] Effrayer une bête; [mod:alt] Intimidation; [@mouseover,harm,nodead][] Trait de choc
```
Pourquoi : Tous tes contrôles sur une touche. Effrayer une bête au survol garde ta cible ; Intimidation (talent) est hors temps de recharge global.

### TAI Melee — Mangouste puis Raptor (Maj : Coupure d'ailes, Ctrl : Contre-attaque)

Spé : toutes · solo + groupe · touche Q · dès le niveau 1 · par défaut  
Où : Barre principale, à côté du Tir : seulement quand un monstre est collé à toi.

```
#showtooltip
/startattack
/cast [mod:shift] Coupure d'ailes; [mod:ctrl] Contre-attaque; [mod:alt] Coup de trotteur; Morsure de la mangouste
/cast Attaque du raptor
```
Pourquoi : Attaque du raptor remplace ton prochain coup (hors temps de recharge global) : elle part avec Mangouste (après une esquive) dans le même clic. Maj : Coupure d'ailes pour reprendre tes distances, puis reviens au Tir.

### TAI Pieges — Piège givrant (Maj : givre, Alt : explosif, Ctrl : immolation)

Spé : toutes · solo + groupe · touche E · dès le niveau 16 · par défaut  
Où : Barre principale : à poser AVANT le pull (les pièges ne se posent que hors combat).

```
#showtooltip
/cast [mod:shift] Piège de givre; [mod:alt] Piège explosif; [mod:ctrl] Piège d'Immolation; Piège givrant
```
(variante 2, avant le sort principal)
```
#showtooltip
/cast Piège d'Immolation
```
Pourquoi : Tous les pièges partagent une recharge : un seul bouton, le modificateur choisit. Hors combat uniquement : pose-le puis attire le monstre dessus (en combat, TAI FD Piege).
**Changé :** texte : les pièges ne se posent que hors combat (règle Classic).

### TAI FD Piege — En combat : Feindre la mort ; hors combat : Piège givrant

Spé : toutes · solo + groupe · touche Maj+E · dès le niveau 30 · par défaut  
Où : À côté de TAI Pieges : clique 2 fois de suite quand un add arrive.

```
#showtooltip
/stopcasting [combat]
/petfollow [combat]
/cast [combat] Feindre la mort; Piège givrant
```
Pourquoi : 1er clic : coupe ta Salve, rappelle le familier et fait le mort (tu sors du combat). 2e clic, maintenant hors combat : Piège givrant à tes pieds. Sans séquence : jamais bloquée, même si Feindre la mort est résisté (le 2e clic retente alors Feindre la mort).
**Changé :** réécrite : `/castsequence reset=combat/8` remplacé par `[combat]` (Feindre la mort fait sortir du combat, ce qui relançait la séquence au début : le 2e clic refaisait Feindre la mort au lieu du piège) ; `/stopcasting` et `/petfollow` seulement en combat ; hors combat elle pose directement le piège (pré-pull).

### TAI Aspects — Faucon (Maj : guépard, Ctrl : meute, Alt : singe), sans annuler

Spé : toutes · solo + groupe · touche X · dès le niveau 4 · par défaut  
Où : Barre principale : un clic avant chaque pull, Maj+clic pour voyager.

```
#showtooltip
/cast [mod:shift] !Aspect du guépard; [mod:ctrl] !Aspect de la meute; [mod:alt] !Aspect du singe; !Aspect du faucon
```
(variante 2, avant le sort principal)
```
#showtooltip
/cast !Aspect du singe
```
Pourquoi : Le « ! » évite d'éteindre l'aspect en recliquant. Repasse en Faucon après le Guépard : le moindre coup reçu t'hébète.

### TAI Pet — Familier : attaque survol / Maj suivre / Ctrl passif / Alt défensif

Spé : toutes · solo + groupe · touche F · dès le niveau 1 · par défaut  
Où : Barre secondaire : remplace la barre du familier.

```
/petattack [nomod,@mouseover,harm,nodead][nomod,harm,nodead]
/petfollow [mod:shift]
/petpassive [mod:ctrl]
/petdefensive [mod:alt]
```
Pourquoi : Contrôle complet du familier : envoie-le sur le monstre survolé (sinon ta cible), rappelle-le (Maj) ou empêche-le de puller (Ctrl).
**Changé :** `/petattack` sans survol exige une cible hostile vivante (`[nomod,harm,nodead]`).

### TAI Grondement — Grondement auto : coupé en groupe, actif en solo

Spé : toutes · solo + groupe · touche - · dès le niveau 1 · option  
Où : Barre de côté : un clic en entrant / sortant d'un groupe.

```
/petautocastoff [group] Grondement
/petautocaston [nogroup] Grondement
```
Pourquoi : En groupe le familier ne doit pas voler les monstres au tank ; en solo il doit les garder. Un clic règle le Grondement automatique selon ta situation.
**Changé :** plus dans le jeu par défaut (réglage ponctuel, place libérée pour TAI Monture).

### TAI Familier — Appel / Ressusciter / Guérison (Maj : ressusciter, Ctrl : nourrir)

Spé : toutes · solo + groupe · touche V · dès le niveau 10 · par défaut  
Où : Barre principale : la seule touche dont ton familier a besoin.

```
#showtooltip
/cast [mod:ctrl,pet] Nourrir le familier
/use [mod:ctrl,pet] item:99999
/stopmacro [mod:ctrl]
/cast [mod:shift][@pet,dead] Ressusciter le familier; [nopet] Appel du familier; Guérison du familier
```
(variante 2, avant le sort principal)
```
#showtooltip
/cast [mod:shift][@pet,dead] Ressusciter le familier; Appel du familier
```
Pourquoi : Appel s'il est absent, Ressusciter s'il est mort, Guérison sinon. Si l'Appel répond « familier mort » (corps disparu), Maj+clic ressuscite. Ctrl le nourrit avec la nourriture du sac adaptée à son régime (choisie hors combat quand ton sac change).
**Changé :** Nourrir : `[mod:ctrl,pet]` (le familier doit être là) ; la ligne d'objet optionnelle ne fait plus disparaître la macro quand le sac n'a pas de nourriture (bug : sans nourriture, Appel/Guérison disparaissaient).

### TAI Burst — Tir rapide + Courroux bestial + racial + bijoux

Spé : toutes · solo + groupe · touche R · dès le niveau 26 · par défaut  
Où : Barre principale : sur les élites et les combats difficiles.

```
#showtooltip Tir rapide
/cast Tir rapide
/cast Courroux bestial
/cast Fureur sanguinaire
/cast Berserker
/use 13
/use 14
```
Pourquoi : Tir rapide et Courroux bestial ne déclenchent pas le temps de recharge global : tout part en un clic (lignes ajoutées seulement si tu as le racial / un bijou à utiliser).

### TAI Defense — Désengagement (Maj : Dissuasion)

Spé : toutes · solo + groupe · touche Maj+Q · dès le niveau 20 · par défaut  
Où : Barre principale, à côté de TAI Melee.

```
#showtooltip
/cast [mod:shift] Dissuasion; Désengagement
```
Pourquoi : Désengagement retire de la menace au corps à corps (pratique en groupe) ; Dissuasion (talent Survie) pare tout pendant 10 s.

### TAI Salve — Salve sous la souris

Spé : toutes · groupe · touche - · dès le niveau 40 · option  
Où : Barre de côté : packs en donjon.

```
#showtooltip
/cast [@cursor] Salve
```
Pourquoi : La Salve tombe directement là où pointe ta souris : pas de cercle à poser.
**Changé :** nouvelle (niveau 40, groupe, option) : Salve `[@cursor]`.

### TAI Tranqui — Tir tranquillisant (enrage) sur survol ou cible

Spé : toutes · groupe · touche - · dès le niveau 60 · option  
Où : Barre de côté : boss qui s'enragent.

```
#showtooltip
/cast [@mouseover,harm,nodead][] Tir tranquillisant
```
Pourquoi : Dissipe l'enrager d'un boss sans changer de cible.

### TAI Fusee — Fusée éclairante (Maj : enchantée) sous la souris

Spé : toutes · solo + groupe · touche - · dès le niveau 32 · option  
Où : Barre de côté : contre les voleurs camouflés.

```
#showtooltip
/cast [mod:shift,@cursor] Fusée éclairante enchantée; [@cursor] Fusée éclairante
```
Pourquoi : Révèle les ennemis camouflés là où tu pointes, sans cercle de visée.

### TAI Pistage — Pistage des bêtes (Maj : humanoïdes)

Spé : toutes · solo · touche - · dès le niveau 1 · option  
Où : Barre de côté : en arrivant dans une zone de quêtes.

```
#showtooltip
/cast [mod:shift] Pistage des humanoïdes; Pistage des bêtes
```
Pourquoi : Bêtes pour les quêtes de chasse, humanoïdes pour les camps.

## ROGUE

### TAI Ouverture — Camouflage puis Embuscade (Maj : Coup bas, Ctrl : Garrot)

Spé : Assassinat, Finesse · solo + groupe · touche 1 · dès le niveau 1 · par défaut  
Où : Case 1 de la barre principale (touche 1).

```
#showtooltip
/cast [nostealth,nocombat] Camouflage; [stealth,mod:shift] Coup bas; [stealth,mod:ctrl] Garrot; [stealth] Embuscade; Attaque pernicieuse
```
Pourquoi : Même touche : Camouflage hors combat, puis l'ouverture (Embuscade, dague requise). En combat elle donne Attaque pernicieuse, jamais un appui perdu.

### TAI Ouverture — Camouflage puis Coup bas (Maj : Embuscade, Ctrl : Garrot)

Spé : Combat · solo + groupe · touche 1 · dès le niveau 1 · par défaut  
Où : Case 1 de la barre principale (touche 1).

```
#showtooltip
/cast [nostealth,nocombat] Camouflage; [stealth,mod:shift] Embuscade; [stealth,mod:ctrl] Garrot; [stealth] Coup bas; Attaque pernicieuse
```
Pourquoi : Combat joue souvent épée/masse : Coup bas (étourdit) ouvre ; Embuscade sur Maj si dague.

### TAI Combo — Mutilation (Maj : Attaque sournoise)

Spé : Assassinat · solo + groupe · touche 2 · dès le niveau 1 · par défaut  
Où : Case 2 de la barre principale (touche 2).

```
#showtooltip
/startattack
/cast [mod:shift] Attaque sournoise; Estropier
```
(variante 2, avant le sort principal)
```
#showtooltip
/startattack
/cast [mod:shift] Attaque sournoise; Attaque pernicieuse
```
Pourquoi : Générateur de points de combo : Mutilation (talent, +dégâts sur cible empoisonnée), sinon Attaque pernicieuse ; Attaque sournoise dans le dos.

### TAI Combo — Attaque pernicieuse (Ctrl : Surin main gauche)

Spé : Combat · solo + groupe · touche 2 · dès le niveau 1 · par défaut  
Où : Case 2 de la barre principale (touche 2).

```
#showtooltip
/startattack
/cast [mod:ctrl] Surin; [mod:shift] Attaque sournoise; Attaque pernicieuse
```
Pourquoi : Attaque pernicieuse pour les points ; Surin (Forever, niveau 24) frappe de la main gauche et rend de l'énergie.

### TAI Combo — Hémorragie (Maj : Attaque sournoise, Ctrl : Frappe fantomatique)

Spé : Finesse · solo + groupe · touche 2 · dès le niveau 1 · par défaut  
Où : Case 2 de la barre principale (touche 2).

```
#showtooltip
/startattack
/cast [mod:shift] Attaque sournoise; [mod:ctrl] Frappe fantomatique; Hémorragie
```
(variante 2, avant le sort principal)
```
#showtooltip
/startattack
/cast [mod:shift] Attaque sournoise; [mod:ctrl] Frappe fantomatique; Attaque pernicieuse
```
Pourquoi : Hémorragie (talent Finesse) augmente les dégâts physiques du groupe sur la cible.

### TAI Finish — Éviscération (Maj : Aiguillon, Ctrl : Débiter, Alt : Rupture)

Spé : toutes · solo + groupe · touche 3 · dès le niveau 1 · par défaut  
Où : Case 3 de la barre principale (touche 3).

```
#showtooltip
/startattack
/cast [mod:shift] Aiguillon perfide; [mod:ctrl] Débiter; [mod:alt] Rupture; Eviscération
```
Pourquoi : Tous les coups de grâce sur une touche : Débiter d'abord (vitesse), Éviscération pour achever, Aiguillon perfide pour étourdir un élite.

### TAI Kick — Coup de pied (Maj : Suriner) — interrompt

Spé : toutes · solo + groupe · touche 4 · dès le niveau 12 · par défaut  
Où : Case 4 de la barre principale (touche 4).

```
#showtooltip
/cast [mod:shift,@mouseover,harm,nodead][mod:shift] Suriner; [@mouseover,harm,nodead][] Coup de pied
```
Pourquoi : Interruption au survol possible sur un 2e lanceur à portée sans perdre ta cible.

### TAI Assommer — Vol à la tire + Assommer (survol)

Spé : toutes · groupe · touche 5 · dès le niveau 10 · par défaut  
Où : Case 5 de la barre principale (touche 5).

```
#showtooltip Assommer
/cast [stealth,@mouseover,harm,nodead][stealth] Vol à la tire
/cast [@mouseover,harm,nodead][] Assommer
```
Pourquoi : Vol à la tire ne déclenche pas le temps de recharge global : tu voles puis assommes dans le même appui (survol prioritaire pour assommer l'add de côté).

### TAI Fuite — Évasion (Maj : Disparition, Ctrl : Sprint)

Spé : toutes · solo + groupe · touche R · dès le niveau 8 · par défaut  
Où : Barre principale, près de tes sorts (raccourci conseillé : R).

```
#showtooltip
/cast [mod:shift] Disparition; [mod:ctrl] Sprint; Evasion
```
(variante 2, avant le sort principal)
```
#showtooltip
/cast Sprint
```
Pourquoi : Tous tes boutons de survie : Évasion contre la mêlée, Disparition pour tout lâcher.

### TAI Cecite — Cécité sur survol ou cible

Spé : toutes · solo + groupe · touche Maj+5 · dès le niveau 34 · par défaut  
Où : Barre secondaire, sous la barre principale (raccourci conseillé : Maj+5).

```
#showtooltip
/cast [@mouseover,harm,nodead][] Cécité
```
Pourquoi : Contrôle d'un add au survol sans lâcher ta cible.

### TAI Burst — Poussée d'adrénaline / Déluge de lames + racial + bijoux

Spé : Combat · solo + groupe · touche Maj+R · dès le niveau 30 · par défaut  
Où : Barre secondaire, sous la barre principale (raccourci conseillé : Maj+R).

```
#showtooltip
/cast [mod:shift] Déluge de lames; Poussée d'adrénaline
/cast Fureur sanguinaire
/cast Berserker
/use 13
/use 14
```
(variante 2, avant le sort principal)
```
#showtooltip
/cast Déluge de lames
/cast Fureur sanguinaire
/cast Berserker
/use 13
/use 14
```
Pourquoi : Cooldowns du Combat ; racial et bijoux « Utiliser » partent dans le même appui.

### TAI Burst — Sang froid + Éviscération

Spé : Assassinat · solo + groupe · touche Maj+R · dès le niveau 30 · par défaut  
Où : Barre secondaire, sous la barre principale (raccourci conseillé : Maj+R).

```
#showtooltip Eviscération
/cast Sang froid
/cast Eviscération
```
Pourquoi : Sang froid est hors temps de recharge global : critique garanti sur l'Éviscération lancée dans le même appui.

### TAI Marque — Marque de mort (coup de grâce différé)

Spé : toutes · solo + groupe · touche - · dès le niveau 40 · option  
Où : Barre de côté : un clic quand tu en as besoin.

```
#showtooltip
/startattack
/cast Marque de mort
```
Pourquoi : Forever, niveau 40 : stocke une partie de tes dégâts puis les inflige d'un coup ; sur les cibles qui vivent au moins sa durée.

### TAI Distraction — Distraction au curseur

Spé : toutes · solo + groupe · touche - · dès le niveau 22 · option  
Où : Barre de côté : un clic quand tu en as besoin.

```
#showtooltip
/cast [@cursor] Distraction
```
Pourquoi : Retourne une patrouille sans cercle de visée pour passer dans son dos.

## PRIEST

### TAI Soin — Soins (Maj : rapides) survol > cible > toi

Spé : toutes · solo + groupe · touche 1 · dès le niveau 1 · par défaut  
Où : Case 1 de la barre principale (touche 1).

```
#showtooltip
/cast [mod:shift,@mouseover,help,nodead][mod:shift,help,nodead][mod:shift,@player] Soins rapides; [mod:alt,@player][@mouseover,help,nodead][@target,help,nodead][@player] Soins
```
(variante 2, avant le sort principal)
```
#showtooltip
/cast [mod:alt,@player][@mouseover,help,nodead][@target,help,nodead][@player] Soins inférieurs
```
Pourquoi : Soin sans changer de cible (survol, puis cible amicale, puis toi ; Alt = toi). Soins rapides sur Maj quand ça presse.

### TAI Gros soin — Soins supérieurs survol > cible > toi

Spé : Discipline, Sacré · groupe · touche Maj+1 · dès le niveau 40 · par défaut  
Où : Barre secondaire, sous la barre principale (raccourci conseillé : Maj+1).

```
#showtooltip
/cast [mod:alt,@player][@mouseover,help,nodead][@target,help,nodead][@player] Soins supérieurs
```
Pourquoi : Le soin le plus efficace en mana sur le tank.

### TAI Bouclier — Mot de pouvoir : Bouclier (Maj : Plan d'urgence)

Spé : toutes · solo + groupe · touche 2 · dès le niveau 6 · par défaut  
Où : Case 2 de la barre principale (touche 2).

```
#showtooltip
/cast [mod:shift,@mouseover,help,nodead][mod:shift,help,nodead][mod:shift,@player] Plan d’urgence; [mod:alt,@player][@mouseover,help,nodead][@target,help,nodead][@player] Mot de pouvoir : Bouclier
```
Pourquoi : Absorbe les dégâts et empêche l'interruption des sorts : posé sur toi en solo avant d'incanter, sur le tank en groupe.

### TAI Renovation — Rénovation (Maj : Prière de guérison)

Spé : toutes · solo + groupe · touche 3 · dès le niveau 8 · par défaut  
Où : Case 3 de la barre principale (touche 3).

```
#showtooltip
/cast [mod:shift,@mouseover,help,nodead][mod:shift,help,nodead][mod:shift,@player] Prière de guérison; [mod:alt,@player][@mouseover,help,nodead][@target,help,nodead][@player] Rénovation
```
Pourquoi : Soin sur la durée au survol ; Prière de guérison (talent Sacré) rebondit sur le groupe.

### TAI Douleur — Mot de l'ombre : Douleur (Ctrl : Peste dévorante)

Spé : toutes · solo + groupe · touche 4 · dès le niveau 4 · par défaut  
Où : Case 4 de la barre principale (touche 4).

```
#showtooltip
/cast [mod:ctrl,@mouseover,harm,nodead][mod:ctrl] Peste dévorante; [@mouseover,harm,nodead][] Mot de l'ombre : Douleur
```
Pourquoi : DoT posable au survol sur plusieurs monstres sans changer de cible.

### TAI Attaque — Attaque mentale (Maj : Fouet mental)

Spé : Ombre · solo + groupe · touche 5 · dès le niveau 1 · par défaut  
Où : Case 5 de la barre principale (touche 5).

```
#showtooltip
/cast [mod:shift,@mouseover,harm,nodead][mod:shift] Fouet mental; [@mouseover,harm,nodead][] Attaque mentale
```
(variante 2, avant le sort principal)
```
#showtooltip
/cast [@mouseover,harm,nodead][] Châtiment
```
Pourquoi : Attaque mentale en ouverture, Fouet mental (talent) pour finir et ralentir.

### TAI Attaque — Châtiment (Maj : Flammes sacrées, Ctrl : Attaque mentale)

Spé : Discipline, Sacré · solo + groupe · touche 5 · dès le niveau 1 · par défaut  
Où : Case 5 de la barre principale (touche 5).

```
#showtooltip
/cast [mod:shift,@mouseover,harm,nodead][mod:shift] Flammes sacrées; [mod:ctrl,@mouseover,harm,nodead][mod:ctrl] Attaque mentale; [@mouseover,harm,nodead][] Châtiment
```
Pourquoi : En Sacré / Discipline, Flammes sacrées ouvre, Châtiment remplit.

### TAI Penitence — Pénitence : soigne l'allié ou frappe l'ennemi visé

Spé : Discipline · solo + groupe · touche Q · dès le niveau 40 · par défaut  
Où : Barre principale, près de tes sorts (raccourci conseillé : Q).

```
#showtooltip
/cast [@mouseover,exists,nodead][exists,nodead][@player] Pénitence
```
Pourquoi : Pénitence (talent Discipline, Forever) soigne un allié OU frappe un ennemi : elle suit le survol, puis ta cible, puis toi.

### TAI Dissip — Dissipation de la magie (Maj : maladies) survol > cible > toi

Spé : toutes · solo + groupe · touche 6 · dès le niveau 18 · par défaut  
Où : Case 6 de la barre principale (touche 6).

```
#showtooltip
/cast [mod:shift,@mouseover,help,nodead][mod:shift,help,nodead][mod:shift,@player] Abolir maladie; [mod:alt,@player][@mouseover,help,nodead][@target,help,nodead][@player] Dissipation de la magie
```
(variante 2, avant le sort principal)
```
#showtooltip
/cast [mod:shift,@mouseover,help,nodead][mod:shift,help,nodead][mod:shift,@player] Guérison des maladies; [mod:alt,@player][@mouseover,help,nodead][@target,help,nodead][@player] Dissipation de la magie
```
Pourquoi : Retire les effets magiques négatifs d'un allié survolé.

### TAI Peur — Cri psychique (coupe ton sort)

Spé : toutes · solo · touche Maj+5 · dès le niveau 14 · par défaut  
Où : Barre secondaire, sous la barre principale (raccourci conseillé : Maj+5).

```
#showtooltip
/stopcasting
/cast Cri psychique
```
Pourquoi : /stopcasting puis peur instantanée quand deux monstres te tapent. À éviter en donjon.

### TAI Urgence — Prière du désespoir (Maj : Oubli)

Spé : toutes · solo + groupe · touche Maj+V · dès le niveau 8 · par défaut  
Où : Barre secondaire, sous la barre principale (raccourci conseillé : Maj+V).

```
#showtooltip
/stopcasting
/cast [mod:shift] Oubli; Prière du désespoir
```
(variante 2, avant le sort principal)
```
#showtooltip
/cast Oubli
```
Pourquoi : Soin instantané gratuit sur toi ; Oubli (Maj) baisse ta menace quand un monstre te vise.

### TAI Buff — Robustesse (Maj : Prière, Ctrl : Esprit divin) cible > toi

Spé : toutes · solo + groupe · touche - · dès le niveau 1 · par défaut  
Où : Barre de côté : un clic quand tu en as besoin.

```
#showtooltip
/cast [mod:shift] Prière de robustesse; [mod:ctrl,help,nodead][mod:ctrl,@player] Esprit divin; [help,nodead][@player] Mot de pouvoir : Robustesse
```
Pourquoi : Buffs sur ta cible amicale sinon sur toi ; Prière = tout le groupe.

### TAI Feu int — Feu intérieur (Maj : Gardien de peur survol)

Spé : toutes · solo + groupe · touche - · dès le niveau 12 · par défaut  
Où : Barre de côté : un clic quand tu en as besoin.

```
#showtooltip
/cast [mod:shift,@mouseover,help,nodead][mod:shift,help,nodead][mod:shift,@player] Gardien de peur; Feu intérieur
```
Pourquoi : Armure et puissance des sorts ; Gardien de peur sur le tank avant un lanceur de peur.

### TAI Forme — Forme d'Ombre (Maj : la quitter)

Spé : Ombre · solo + groupe · touche - · dès le niveau 40 · par défaut  
Où : Barre de côté : un clic quand tu en as besoin.

```
#showtooltip
/cancelform [mod:shift]
/cast [noform,nomod] Forme d'Ombre
```
Pourquoi : Ne se coupe jamais par erreur : [noform] empêche le rappui, Maj la quitte pour soigner.

### TAI Infusion — Infusion de puissance sur survol (Maj : toi)

Spé : Discipline · groupe · touche - · dès le niveau 40 · par défaut  
Où : Barre de côté : un clic quand tu en as besoin.

```
#showtooltip
/cast [mod:shift,@player][@mouseover,help,nodead][@target,help,nodead][@player] Infusion de puissance
```
Pourquoi : + dégâts des sorts sur le mage / démoniste survolé.

### TAI Entraves — Entraves des morts-vivants au survol

Spé : toutes · groupe · touche - · dès le niveau 20 · option  
Où : Barre de côté : un clic quand tu en as besoin.

```
#showtooltip
/cast [@mouseover,harm,nodead][] Entraves des morts-vivants
```
Pourquoi : Contrôle un mort-vivant au survol sans lâcher ta cible.

### TAI Rez — Résurrection sur l'allié mort survolé

Spé : toutes · groupe · touche - · dès le niveau 10 · option  
Où : Barre de côté : un clic quand tu en as besoin.

```
#showtooltip
/cast [@mouseover,help,dead][help,dead] Résurrection
```
Pourquoi : Ressuscite le corps survolé sans le cibler.

## SHAMAN

### TAI Eclair — Éclair (Maj : Chaîne d'éclairs) ; Ctrl : Maîtrise élémentaire

Spé : toutes · solo + groupe · touche 1 · dès le niveau 1 · par défaut  
Où : Case 1 de la barre principale (touche 1).

```
#showtooltip Eclair
/cast [mod:ctrl] Maîtrise de la foudre
/cast [mod:shift,@mouseover,harm,nodead][mod:shift] Chaîne d'éclairs; [@mouseover,harm,nodead][] Eclair
```
Pourquoi : Nuke au survol ou sur la cible. Ctrl : le talent Élémentaire (hors temps de recharge global) part avec l'Éclair dans le même appui = critique garanti.

### TAI Horion — Horion de terre (Maj : givre, Ctrl : flammes)

Spé : toutes · solo + groupe · touche 2 · dès le niveau 4 · par défaut  
Où : Case 2 de la barre principale (touche 2).

```
#showtooltip
/cast [mod:shift,@mouseover,harm,nodead][mod:shift] Horion de givre; [mod:ctrl,@mouseover,harm,nodead][mod:ctrl] Horion de flammes; [@mouseover,harm,nodead][] Horion de terre
```
Pourquoi : Les horions partagent une recharge : un bouton. Terre interrompt, Givre ralentit, Flammes brûle dans la durée.

### TAI Coupure — Interrompt : coupe ton sort + Horion de terre (survol)

Spé : toutes · groupe · touche 4 · dès le niveau 4 · option  
Où : Case 4 de la barre principale (touche 4).

```
#showtooltip
/stopcasting
/cast [@mouseover,harm,nodead][] Horion de terre
```
Pourquoi : /stopcasting annule ton Éclair en cours pour interrompre tout de suite.

### TAI Frappe — Courroux naturel (Maj : Horion de terre) + attaque

Spé : Amélioration · solo + groupe · touche 3 · dès le niveau 40 · par défaut  
Où : Case 3 de la barre principale (touche 3).

```
#showtooltip
/startattack
/cast [mod:shift] Horion de terre; Courroux naturel
```
Pourquoi : Talent Amélioration : frappe des deux armes et augmente tes dégâts de Nature.

### TAI Arme — Furie-des-vents (Maj : Langue de feu, Ctrl : givre)

Spé : Amélioration · solo + groupe · touche - · dès le niveau 1 · par défaut  
Où : Barre de côté : un clic quand tu en as besoin.

```
#showtooltip
/cast [mod:shift] Arme Langue de feu; [mod:ctrl] Arme de givre; Arme Furie-des-vents
```
(variante 2, avant le sort principal)
```
#showtooltip
/cast [mod:shift] Arme Langue de feu; [mod:ctrl] Arme de givre; Arme Croque-roc
```
Pourquoi : Furie-des-vents dès le niveau 30 sur arme lente à deux mains ; avant, Croque-roc.

### TAI Arme — Langue de feu (Maj : Croque-roc)

Spé : Élémentaire, Restauration · solo + groupe · touche - · dès le niveau 1 · par défaut  
Où : Barre de côté : un clic quand tu en as besoin.

```
#showtooltip
/cast [mod:shift] Arme Croque-roc; Arme Langue de feu
```
(variante 2, avant le sort principal)
```
#showtooltip
/cast Arme Croque-roc
```
Pourquoi : Lanceur de sorts : Langue de feu sur l'arme suffit.

### TAI Bouclier — Bouclier de foudre

Spé : toutes · solo + groupe · touche - · dès le niveau 8 · option  
Où : Barre de côté : un clic quand tu en as besoin.

```
#showtooltip
/cast Bouclier de foudre
```
Pourquoi : À garder en permanence en solo : renvoie des dégâts à chaque coup reçu.
**Changé :** option (sort seul).

### TAI Soin — Vague de soins (Maj : inférieure) survol > cible > toi

Spé : toutes · solo + groupe · touche 5 · dès le niveau 1 · par défaut  
Où : Case 5 de la barre principale (touche 5).

```
#showtooltip
/cast [mod:shift,@mouseover,help,nodead][mod:shift,help,nodead][mod:shift,@player] Vague de soins inférieurs; [mod:alt,@player][@mouseover,help,nodead][@target,help,nodead][@player] Vague de soins
```
Pourquoi : Soin sans changer de cible (Alt = toi). Vague inférieure, plus rapide, sur Maj.

### TAI Chaine — Salve de guérison (Maj : Remous)

Spé : Restauration · groupe · touche Maj+5 · dès le niveau 40 · par défaut  
Où : Barre secondaire, sous la barre principale (raccourci conseillé : Maj+5).

```
#showtooltip
/cast [mod:shift,@mouseover,help,nodead][mod:shift,help,nodead][mod:shift,@player] Remous; [mod:alt,@player][@mouseover,help,nodead][@target,help,nodead][@player] Salve de guérison
```
Pourquoi : Salve rebondit sur 3 alliés ; Remous (talent) d'abord sur la cible renforce la Salve.

### TAI NS Soin — Rapidité de la nature + Vague de soins instantanée

Spé : Restauration · solo + groupe · touche Maj+V · dès le niveau 30 · par défaut  
Où : Barre secondaire, sous la barre principale (raccourci conseillé : Maj+V).

```
#showtooltip Rapidité de la nature
/cast Rapidité de la nature
/cast [mod:alt,@player][@mouseover,help,nodead][@target,help,nodead][@player] Vague de soins
```
Pourquoi : Rapidité de la nature est hors temps de recharge global : la Vague de soins du même appui devient instantanée. Le bouton qui sauve un tank.

### TAI Terre — Force de la Terre (Maj : Séisme, Ctrl : lien, Alt : Griffes)

Spé : toutes · solo + groupe · touche - · dès le niveau 4 · par défaut  
Où : Barre de côté : un clic quand tu en as besoin.

```
#showtooltip
/cast [mod:shift] Totem de Séisme; [mod:ctrl] Totem de lien terrestre; [mod:alt] Totem de Griffes de pierre; Totem de Force de la Terre
```
(variante 2, avant le sort principal)
```
#showtooltip
/cast [mod:ctrl] Totem de lien terrestre; Totem de Peau de pierre
```
Pourquoi : Un bouton par élément : on ne peut avoir qu'un totem de Terre, les modificateurs choisissent lequel.

### TAI Feu — Totem incendiaire (Maj : Magma, Ctrl : Langue de feu)

Spé : toutes · solo + groupe · touche - · dès le niveau 10 · par défaut  
Où : Barre de côté : un clic quand tu en as besoin.

```
#showtooltip
/cast [mod:shift] Totem de Magma; [mod:ctrl] Totem Langue de feu; [mod:alt] Totem de résistance au Feu; Totem incendiaire
```
Pourquoi : Incendiaire en solo, Magma sur les packs, Langue de feu pour les mêlées.

### TAI Eau — Fontaine de mana / Guérisseur (Maj : poison, Ctrl : maladie)

Spé : Élémentaire, Restauration · solo + groupe · touche - · dès le niveau 20 · par défaut  
Où : Barre de côté : un clic quand tu en as besoin.

```
#showtooltip
/cast [mod:shift] Totem de Purification du poison; [mod:ctrl] Totem de Purification des maladies; [mod:alt] Totem guérisseur; Totem Fontaine de mana
```
(variante 2, avant le sort principal)
```
#showtooltip
/cast [mod:shift] Totem de Purification du poison; Totem guérisseur
```
Pourquoi : Fontaine de mana pour les lanceurs ; Guérisseur sur Alt.

### TAI Eau — Totem guérisseur (Maj : poison, Ctrl : maladie)

Spé : Amélioration · solo + groupe · touche - · dès le niveau 20 · par défaut  
Où : Barre de côté : un clic quand tu en as besoin.

```
#showtooltip
/cast [mod:shift] Totem de Purification du poison; [mod:ctrl] Totem de Purification des maladies; [mod:alt] Totem Fontaine de mana; Totem guérisseur
```
Pourquoi : Totem guérisseur pour tenir en mêlée ; purification sur Maj / Ctrl.

### TAI Air — Totem Furie-des-vents (Maj : Glèbe, Ctrl : Mur des vents)

Spé : toutes · groupe · touche - · dès le niveau 32 · par défaut  
Où : Barre de côté : un clic quand tu en as besoin.

```
#showtooltip
/cast [mod:shift] Totem de Glèbe; [mod:ctrl] Totem de Mur des vents; [mod:alt] Totem de Grâce aérienne; Totem Furie-des-vents
```
Pourquoi : Furie-des-vents pour les mêlées du groupe ; Glèbe absorbe un sort ennemi.

### TAI Totems — Appel des éléments (Maj : Rappel totémique)

Spé : toutes · solo + groupe · touche 6 · dès le niveau 20 · par défaut  
Où : Case 6 de la barre principale (touche 6).

```
#showtooltip
/cast [mod:shift] Rappel totémique; Appel des éléments
```
Pourquoi : Forever : pose les 4 totems de ta barre de totems en un seul appui ; Maj les rappelle et rend du mana.

### TAI Purge — Expiation sur survol ou cible

Spé : toutes · groupe · touche - · dès le niveau 12 · par défaut  
Où : Barre de côté : un clic quand tu en as besoin.

```
#showtooltip
/cast [@mouseover,harm,nodead][] Expiation
```
Pourquoi : Retire un buff magique d'un ennemi survolé.

### TAI Guerison — Guérison du poison (Maj : maladies) survol > cible > toi

Spé : toutes · solo + groupe · touche - · dès le niveau 16 · par défaut  
Où : Barre de côté : un clic quand tu en as besoin.

```
#showtooltip
/cast [mod:shift,@mouseover,help,nodead][mod:shift,help,nodead][mod:shift,@player] Guérison des maladies; [mod:alt,@player][@mouseover,help,nodead][@target,help,nodead][@player] Guérison du poison
```
Pourquoi : Nettoie un allié survolé sans changer de cible.

### TAI Loup — Loup fantôme

Spé : toutes · solo · touche Maj+X · dès le niveau 20 · option  
Où : Barre secondaire, sous la barre principale (raccourci conseillé : Maj+X).

```
#showtooltip
/cast Loup fantôme
```
Pourquoi : +40 % de vitesse en extérieur, entre deux quêtes.
**Changé :** option (sort seul).

### TAI Rez — Esprit ancestral sur l'allié mort survolé

Spé : toutes · groupe · touche - · dès le niveau 12 · option  
Où : Barre de côté : un clic quand tu en as besoin.

```
#showtooltip
/cast [@mouseover,help,dead][help,dead] Esprit ancestral
```
Pourquoi : Ressuscite le corps survolé.

## MAGE

### TAI Nuke — Éclair de givre (Maj : Javelot de glace, Ctrl : Givrefeu)

Spé : Givre · solo + groupe · touche 1 · dès le niveau 1 · par défaut  
Où : Case 1 de la barre principale (touche 1).

```
#showtooltip
/cast [mod:shift,@mouseover,harm,nodead][mod:shift] Javelot de glace; [mod:ctrl,@mouseover,harm,nodead][mod:ctrl] Éclair de givrefeu; [@mouseover,harm,nodead][] Eclair de givre
```
(variante 2, avant le sort principal)
```
#showtooltip
/cast [@mouseover,harm,nodead][] Boule de feu
```
Pourquoi : Éclair de givre au survol ou sur la cible ; Javelot de glace (talent, Forever) instantané et bien plus fort sur une cible gelée (après Nova de givre).

### TAI Nuke — Boule de feu (Maj : Explosion pyrotechnique, Ctrl : Brûlure)

Spé : Feu · solo + groupe · touche 1 · dès le niveau 1 · par défaut  
Où : Case 1 de la barre principale (touche 1).

```
#showtooltip
/cast [mod:shift,@mouseover,harm,nodead][mod:shift] Explosion pyrotechnique; [mod:ctrl,@mouseover,harm,nodead][mod:ctrl] Brûlure; [@mouseover,harm,nodead][] Boule de feu
```
Pourquoi : Boule de feu en continu ; Explosion pyrotechnique en ouverture, Brûlure en mouvement.

### TAI Nuke — Déflagration des arcanes (Maj : Projectiles, Ctrl : Colère)

Spé : Arcanes · solo + groupe · touche 1 · dès le niveau 1 · par défaut  
Où : Case 1 de la barre principale (touche 1).

```
#showtooltip
/cast [mod:shift,@mouseover,harm,nodead][mod:shift] Projectiles des arcanes; [mod:ctrl,@mouseover,harm,nodead][mod:ctrl] Colère des arcanes; [@mouseover,harm,nodead][] Déflagration des arcanes
```
(variante 2, avant le sort principal)
```
#showtooltip
/cast [mod:shift,@mouseover,harm,nodead][mod:shift] Projectiles des arcanes; [@mouseover,harm,nodead][] Boule de feu
```
Pourquoi : Déflagration des arcanes (talent, Forever) se renforce à chaque lancer mais coûte de plus en plus de mana : alterne avec Projectiles.

### TAI Instant — Trait de feu (Maj : Cône de froid, Ctrl : Vague explosive)

Spé : toutes · solo + groupe · touche 2 · dès le niveau 6 · par défaut  
Où : Case 2 de la barre principale (touche 2).

```
#showtooltip
/cast [mod:shift] Cône de froid; [mod:ctrl] Vague explosive; [@mouseover,harm,nodead][] Trait de feu
```
Pourquoi : Tes instantanés pour finir un monstre ou en reculant.

### TAI Nova — Coupe ton sort + Nova de givre (Maj : Transfert)

Spé : toutes · solo + groupe · touche 3 · dès le niveau 10 · par défaut  
Où : Case 3 de la barre principale (touche 3).

```
#showtooltip
/stopcasting
/cast [mod:shift] Transfert; Nova de givre
```
Pourquoi : /stopcasting annule ton sort en cours pour geler / fuir tout de suite (sinon l'appui est perdu).

### TAI Contresort — Coupe ton sort + Contresort (survol)

Spé : toutes · solo + groupe · touche 4 · dès le niveau 24 · par défaut  
Où : Case 4 de la barre principale (touche 4).

```
#showtooltip
/stopcasting
/cast [@mouseover,harm,nodead][] Contresort
```
Pourquoi : Interruption au survol, ton sort en cours est coupé pour ne pas perdre la fenêtre.

### TAI Mouton — Métamorphose sur survol ou cible

Spé : toutes · solo + groupe · touche 5 · dès le niveau 8 · par défaut  
Où : Case 5 de la barre principale (touche 5).

```
#showtooltip
/cast [@mouseover,harm,nodead][] Métamorphose
```
Pourquoi : Mouton sur l'add survolé sans perdre ta cible principale.

### TAI AoE — Explosion des arcanes (Maj : Blizzard, Ctrl : Choc de flammes au curseur)

Spé : toutes · groupe · touche 6 · dès le niveau 14 · par défaut  
Où : Case 6 de la barre principale (touche 6).

```
#showtooltip
/cast [mod:shift,@cursor] Blizzard; [mod:ctrl,@cursor] Choc de flammes; Explosion des arcanes
```
Pourquoi : Blizzard et Choc de flammes tombent directement sous la souris : pas de cercle à poser.

### TAI Defense — Bloc de glace (Maj : Morsure du froid, Ctrl : Barrière)

Spé : toutes · solo + groupe · touche Maj+V · dès le niveau 20 · par défaut  
Où : Barre secondaire, sous la barre principale (raccourci conseillé : Maj+V).

```
#showtooltip
/stopcasting
/cast [mod:shift] Morsure du froid; [mod:ctrl] Barrière de glace; Bloc de glace
```
(variante 2, avant le sort principal)
```
#showtooltip
/cast Bouclier de mana
```
Pourquoi : Immunité d'urgence (coupe ton sort) ; Morsure du froid réinitialise Bloc et Nova.

### TAI Armure — Armure de glace (Maj : du mage, Alt : Bouclier de mana)

Spé : toutes · solo + groupe · touche - · dès le niveau 1 · par défaut  
Où : Barre de côté : un clic quand tu en as besoin.

```
#showtooltip
/cast [mod:shift] Armure du mage; [mod:alt] Bouclier de mana; Armure de glace
```
(variante 2, avant le sort principal)
```
#showtooltip
/cast [mod:alt] Bouclier de mana; Armure de givre
```
Pourquoi : Armure de glace en solo (ralentit les mêlées) ; Armure du mage en groupe (mana).

### TAI Intel — Intelligence (Maj : Illumination) survol > cible > toi

Spé : toutes · solo + groupe · touche - · dès le niveau 1 · par défaut  
Où : Barre de côté : un clic quand tu en as besoin.

```
#showtooltip
/cast [mod:shift] Illumination des arcanes; [mod:alt,@player][@mouseover,help,nodead][@target,help,nodead][@player] Intelligence des arcanes
```
Pourquoi : Buff au survol ; Illumination pour tout le groupe.

### TAI Decurse — Délivrance de la malédiction mineure : survol > cible > toi

Spé : toutes · groupe · touche - · dès le niveau 18 · par défaut  
Où : Barre de côté : un clic quand tu en as besoin.

```
#showtooltip
/cast [mod:alt,@player][@mouseover,help,nodead][@target,help,nodead][@player] Délivrance de la malédiction mineure
```
Pourquoi : Retire une malédiction d'un allié survolé.

### TAI Invoc — Invocation d'eau (Maj : nourriture)

Spé : toutes · solo + groupe · touche - · dès le niveau 6 · par défaut  
Où : Barre de côté : un clic quand tu en as besoin.

```
#showtooltip
/cast [mod:shift] Invocation de nourriture; Invocation d'eau
```
(variante 2, avant le sort principal)
```
#showtooltip
/cast Invocation de nourriture
```
Pourquoi : Ta nourriture gratuite : TAI Repas mange les invoqués en premier.

### TAI Gemme — Invoque la meilleure gemme de mana

Spé : toutes · solo + groupe · touche - · dès le niveau 28 · par défaut  
Où : Barre de côté : un clic quand tu en as besoin.

```
#showtooltip
/cast Invocation d'un rubis de mana
```
(variante 2, avant le sort principal)
```
#showtooltip
/cast Invocation d'une citrine de mana
```
(variante 3, avant le sort principal)
```
#showtooltip
/cast Invocation d'une jade de mana
```
(variante 4, avant le sort principal)
```
#showtooltip
/cast Invocation d'une agate de mana
```
Pourquoi : La macro suit ta progression : rubis > citrine > jade > agate.

### TAI Evocation — Évocation

Spé : toutes · solo + groupe · touche - · dès le niveau 20 · option  
Où : Barre de côté : un clic quand tu en as besoin.

```
#showtooltip
/cast Evocation
```
Pourquoi : Rend ton mana pendant 8 s : évite de boire entre deux packs.
**Changé :** option : un sort seul sans condition n'apporte rien par rapport au grimoire.

### TAI Burst — Présence spirituelle / Pouvoir des arcanes / Combustion + bijoux

Spé : Arcanes, Feu · solo + groupe · touche R · dès le niveau 1 · par défaut  
Où : Barre principale, près de tes sorts (raccourci conseillé : R).

```
#showtooltip
/cast Présence spirituelle
/cast Pouvoir des arcanes
/cast Combustion
/cast Fureur sanguinaire
/cast Berserker
/use 13
/use 14
```
Pourquoi : Tous ces cooldowns sont hors temps de recharge global : ils partent ensemble, ton sort suivant en profite.

## WARLOCK

### TAI Trait — Trait de l'ombre (Maj : Douleur brûlante, Ctrl : Incinérer)

Spé : toutes · solo + groupe · touche 1 · dès le niveau 1 · par défaut  
Où : Case 1 de la barre principale (touche 1).

```
#showtooltip
/cast [mod:shift,@mouseover,harm,nodead][mod:shift] Douleur brûlante; [mod:ctrl,@mouseover,harm,nodead][mod:ctrl] Incinérer; [@mouseover,harm,nodead][] Trait de l'ombre
```
Pourquoi : Nuke au survol ou sur la cible. Incinérer (talent Destruction, Forever) fait plus si la cible brûle (Immolation).

### TAI DoT — Corruption (Maj : Immolation) survol > cible

Spé : toutes · solo + groupe · touche 2 · dès le niveau 1 · par défaut  
Où : Case 2 de la barre principale (touche 2).

```
#showtooltip
/cast [mod:shift,@mouseover,harm,nodead][mod:shift] Immolation; [@mouseover,harm,nodead][] Corruption
```
(variante 2, avant le sort principal)
```
#showtooltip
/cast [@mouseover,harm,nodead][] Immolation
```
Pourquoi : DoTs posables au survol sur chaque monstre du pack sans jamais changer de cible.

### TAI Maledict — Agonie (Maj : témérité, Ctrl : éléments) ; Alt : amplifiée

Spé : toutes · solo + groupe · touche 3 · dès le niveau 4 · par défaut  
Où : Case 3 de la barre principale (touche 3).

```
#showtooltip Plaie d'agonie
/cast [mod:alt] Malédiction amplifiée
/cast [mod:shift,@mouseover,harm,nodead][mod:shift] Malédiction de témérité; [mod:ctrl] Malédiction des éléments; [@mouseover,harm,nodead][] Plaie d'agonie
```
(variante 2, avant le sort principal)
```
#showtooltip
/cast [@mouseover,harm,nodead][] Malédiction de faiblesse
```
Pourquoi : Une malédiction par cible et par démoniste. Alt : Malédiction amplifiée (talent, hors temps de recharge global) part avec l'Agonie.

### TAI Siphon — Siphon de vie (Maj : Calvaire)

Spé : Affliction · solo + groupe · touche 4 · dès le niveau 30 · par défaut  
Où : Case 4 de la barre principale (touche 4).

```
#showtooltip
/cast [mod:shift,@mouseover,harm,nodead][mod:shift] Calvaire; [@mouseover,harm,nodead][] Siphon de vie
```
Pourquoi : Siphon soigne en continu ; Calvaire (Forever, niveau 40) renforce tous tes DoTs d'Ombre.

### TAI Calvaire — Calvaire sur survol ou cible

Spé : Démonologie, Destruction · solo + groupe · touche 4 · dès le niveau 40 · par défaut  
Où : Case 4 de la barre principale (touche 4).

```
#showtooltip
/cast [@mouseover,harm,nodead][] Calvaire
```
Pourquoi : Forever, niveau 40 : gros DoT qui augmente les dégâts de tes autres DoTs d'Ombre.

### TAI Conflag — Conflagration (Maj : Brûlure de l'ombre)

Spé : Destruction · solo + groupe · touche 5 · dès le niveau 20 · par défaut  
Où : Case 5 de la barre principale (touche 5).

```
#showtooltip
/cast [mod:shift,@mouseover,harm,nodead][mod:shift] Brûlure de l'ombre; [@mouseover,harm,nodead][] Conflagration
```
(variante 2, avant le sort principal)
```
#showtooltip
/cast Brûlure de l'ombre
```
Pourquoi : Conflagration consomme ton Immolation pour une grosse explosion ; Brûlure pour achever.

### TAI Drain — Drain de vie (Maj : Siphon d'âme, Ctrl : Drain de mana)

Spé : toutes · solo + groupe · touche 6 · dès le niveau 10 · par défaut  
Où : Case 6 de la barre principale (touche 6).

```
#showtooltip
/cast [mod:shift,@mouseover,harm,nodead][mod:shift] Siphon d'âme; [mod:ctrl,@mouseover,harm,nodead][mod:ctrl] Drain de mana; [@mouseover,harm,nodead][] Drain de vie
```
(variante 2, avant le sort principal)
```
#showtooltip
/cast [@mouseover,harm,nodead][] Siphon d'âme
```
Pourquoi : Drain de vie pour tenir sans manger ; Siphon d'âme sur un monstre presque mort = fragment.

### TAI Peur — Peur (Maj : Hurlement, Ctrl : Voile mortel) survol

Spé : toutes · solo + groupe · touche Maj+5 · dès le niveau 8 · par défaut  
Où : Barre secondaire, sous la barre principale (raccourci conseillé : Maj+5).

```
#showtooltip
/cast [mod:shift] Hurlement de terreur; [mod:ctrl,@mouseover,harm,nodead][mod:ctrl] Voile mortel; [@mouseover,harm,nodead][] Peur
```
Pourquoi : Peur au survol sur l'add sans lâcher ta cible ; Voile mortel : peur instantanée + soin.

### TAI Bannir — Bannir sur survol ou cible

Spé : toutes · groupe · touche - · dès le niveau 28 · option  
Où : Barre de côté : un clic quand tu en as besoin.

```
#showtooltip
/cast [@mouseover,harm,nodead][] Bannir
```
Pourquoi : Contrôle sûr des démons et élémentaires.
**Changé :** option.

### TAI Connexion — Connexion (Maj : Captation de vie vers le démon)

Spé : toutes · solo + groupe · touche Q · dès le niveau 6 · par défaut  
Où : Barre principale, près de tes sorts (raccourci conseillé : Q).

```
#showtooltip
/cast [mod:shift] Captation de vie; Connexion
```
(variante 2, avant le sort principal)
```
#showtooltip
/cast Connexion
```
Pourquoi : Vie -> mana ; Maj soigne ton démon avec ta vie (utile pour garder le marcheur).

### TAI Demon — Démon : attaque survol / Maj suivre / Ctrl passif / Alt défensif

Spé : toutes · solo + groupe · touche F · dès le niveau 1 · par défaut  
Où : Barre principale, près de tes sorts (raccourci conseillé : F).

```
/petattack [nomod,@mouseover,harm,nodead][nomod]
/petfollow [mod:shift]
/petpassive [mod:ctrl]
/petdefensive [mod:alt]
```
Pourquoi : Contrôle du démon sans barre de familier : envoie-le sur le survol, rappelle-le, empêche-le de puller.

### TAI Demon sort — Sort du démon : Verrou / Séduction / Tourment (Maj : Sacrifice)

Spé : toutes · solo + groupe · touche Maj+F · dès le niveau 1 · par défaut  
Où : Barre secondaire, sous la barre principale (raccourci conseillé : Maj+F).

```
#showtooltip
/stopmacro [nopet]
/cast [mod:shift] Sacrifice; [@mouseover,harm,nodead][] Tourment
/cast [@mouseover,harm,nodead][] Verrou magique
/cast [@mouseover,harm,nodead][] Séduction
```
Pourquoi : Une touche pour le sort utile de chaque démon : seul celui du démon présent part (Tourment du marcheur, Verrou magique du chasseur corrompu, Séduction de la succube).

### TAI Invoc — Marcheur du Vide (Maj : diablotin, Ctrl : succube, Alt : chasseur)

Spé : toutes · solo + groupe · touche - · dès le niveau 1 · par défaut  
Où : Barre de côté : un clic quand tu en as besoin.

```
#showtooltip Invocation d'un marcheur du Vide
/cast [combat] Domination corrompue
/cast [mod:shift] Invocation d'un diablotin; [mod:ctrl] Invocation d'une succube; [mod:alt] Invocation d'un chasseur corrompu; Invocation d'un marcheur du Vide
```
(variante 2, avant le sort principal)
```
#showtooltip
/cast Invocation d'un diablotin
```
Pourquoi : Domination corrompue (talent, hors temps de recharge global) rend l'invocation instantanée en combat.

### TAI Pierres — Pierre de soins (Maj : d'âme, Ctrl : de feu, Alt : de sort)

Spé : toutes · solo + groupe · touche - · dès le niveau 10 · par défaut  
Où : Barre de côté : un clic quand tu en as besoin.

```
#showtooltip
/cast [mod:shift] Création de pierre d'âme; [mod:ctrl] Création de Pierre de feu; [mod:alt] Création de Pierre de sort; Création de Pierre de soins
```
Pourquoi : Crée la pierre ; TAI Survie l'utilise ensuite (pierre puis potion).

### TAI Armure — Armure démoniaque (Maj : Gardien de l'ombre)

Spé : toutes · solo + groupe · touche - · dès le niveau 1 · option  
Où : Barre de côté : un clic quand tu en as besoin.

```
#showtooltip
/cast [mod:shift] Gardien de l'ombre; Armure démoniaque
```
(variante 2, avant le sort principal)
```
#showtooltip
/cast Peau de démon
```
Pourquoi : Armure + régénération ; Gardien de l'ombre contre un lanceur d'Ombre.
**Changé :** option (buff de 30 min).

### TAI AoE — Pluie de feu (Maj : Flammes infernales)

Spé : toutes · groupe · touche - · dès le niveau 20 · option  
Où : Barre de côté : un clic quand tu en as besoin.

```
#showtooltip
/cast [mod:shift] Flammes infernales; [@cursor] Pluie de feu
```
Pourquoi : Pluie de feu tombe directement sous la souris.

## DRUID

### TAI Attaque — Selon la forme : Mutiler (ours), Griffe / Lambeau (félin), Colère

Spé : toutes · solo + groupe · touche 1 · dès le niveau 1 · par défaut  
Où : Case 1 de la barre principale (touche 1).

```
#showtooltip
/startattack [form:1/3,nostealth]
/cast [form:1] Mutiler; [form:3,stealth] Ravage; [form:3,mod:shift] Lambeau; [form:3] Griffe; [@mouseover,harm,nodead][] Colère
```
Pourquoi : Une touche qui suit ta forme : Mutiler en ours (prochain coup, hors temps de recharge global), Griffe en félin (Maj = Lambeau dans le dos, Ravage en Rôdeur), Colère sinon.

### TAI Fauve — Ours : Lacérer / Balayage (Maj) ; Félin : coups de grâce

Spé : Combat farouche · solo + groupe · touche 2 · dès le niveau 16 · par défaut  
Où : Case 2 de la barre principale (touche 2).

```
#showtooltip
/startattack
/cast [form:1,mod:shift] Balayage; [form:1] Lacérer; [form:3,mod:shift] Déchirure; [form:3] Morsure féroce
```
(variante 2, avant le sort principal)
```
#showtooltip
/startattack
/cast [form:1,mod:shift] Rugissement démoralisant; [form:1] Balayage; [form:3,mod:shift] Déchirure; [form:3] Morsure féroce; [form:3] Déchirure
```
Pourquoi : Ours : Lacérer (talent, menace) ou Balayage sur Maj pour les packs. Félin : Morsure féroce, Déchirure sur Maj pour les cibles qui durent.

### TAI Ours — Forme d'ours redoutable (Maj : retour humanoïde)

Spé : toutes · solo + groupe · touche 3 · dès le niveau 10 · par défaut  
Où : Case 3 de la barre principale (touche 3).

```
#showtooltip
/cancelform [mod:shift]
/cast [nomod] !Forme d’ours redoutable
```
(variante 2, avant le sort principal)
```
#showtooltip
/cancelform [mod:shift]
/cast [nomod] !Forme d’ours
```
Pourquoi : Le « ! » évite de quitter la forme en rappuyant ; Maj revient en forme normale pour soigner.

### TAI Felin — Forme de félin, puis Rôder hors combat

Spé : toutes · solo + groupe · touche 4 · dès le niveau 20 · par défaut  
Où : Case 4 de la barre principale (touche 4).

```
#showtooltip
/cast [noform:3] !Forme de félin; [nocombat,nostealth] Rôder
```
(variante 2, avant le sort principal)
```
#showtooltip
/cast !Forme de félin
```
Pourquoi : 1er appui : félin ; 2e appui hors combat : Rôder. Pas de risque de casser la forme.

### TAI Rodeur — Ouverture félin : Traquenard (Maj : Ravage)

Spé : Combat farouche · solo · touche Maj+4 · dès le niveau 24 · par défaut  
Où : Barre secondaire, sous la barre principale (raccourci conseillé : Maj+4).

```
#showtooltip
/cast [noform:3] !Forme de félin; [stealth,mod:shift] Ravage; [stealth] Traquenard; Griffure
```
Pourquoi : Depuis Rôder : Traquenard (étourdit) ou Ravage dans le dos ; en combat : Griffure.

### TAI Provoc — Grondement (survol) en ours (Maj : Rugissement provocateur)

Spé : Combat farouche · groupe · touche T · dès le niveau 10 · par défaut  
Où : Barre principale, près de tes sorts (raccourci conseillé : T).

```
#showtooltip
/cast [noform:1] !Forme d’ours redoutable; [mod:shift] Rugissement provocateur; [@mouseover,harm,nodead][] Grondement
```
(variante 2, avant le sort principal)
```
#showtooltip
/cast [noform:1] !Forme d’ours; [mod:shift] Rugissement provocateur; [@mouseover,harm,nodead][] Grondement
```
Pourquoi : Reprend le monstre qui tape un allié survolé ; passe en ours d'abord.
**Changé :** variante Forme d'ours redoutable : au niveau 40 la Forme d'ours quitte le grimoire, la macro disparaissait.

### TAI Charge — Charge farouche (ours) ; sinon Sarments au survol

Spé : toutes · solo + groupe · touche Maj+1 · dès le niveau 8 · option  
Où : Barre secondaire, sous la barre principale (raccourci conseillé : Maj+1).

```
#showtooltip
/cast [form:1] Charge farouche; [@mouseover,harm,nodead][] Sarments
```
(variante 2, avant le sort principal)
```
#showtooltip
/cast [form:1] Sonner; [@mouseover,harm,nodead][] Sarments
```
(variante 3, avant le sort principal)
```
#showtooltip
/cast [@mouseover,harm,nodead][] Sarments
```
Pourquoi : En ours : Charge farouche (talent, interrompt) ou Sonner ; hors forme : Sarments au survol.
**Changé :** option (place).

### TAI Voyage — Aquatique dans l'eau, Voyage dehors, Félin dedans

Spé : toutes · solo + groupe · touche Maj+X · dès le niveau 16 · par défaut  
Où : Barre secondaire, sous la barre principale (raccourci conseillé : Maj+X).

```
#showtooltip
/cast [swimming] !Forme aquatique; [outdoors] !Forme de voyage; !Forme de félin
```
Pourquoi : Une touche de déplacement qui choisit la bonne forme selon le terrain.
**Changé :** `!` ajouté devant Forme de félin (recliquer en intérieur faisait quitter la forme).

### TAI Soin — Toucher guérisseur (Maj : Rétablissement) survol > cible > toi

Spé : toutes · solo + groupe · touche 5 · dès le niveau 1 · par défaut  
Où : Case 5 de la barre principale (touche 5).

```
#showtooltip
/cast [mod:shift,@mouseover,help,nodead][mod:shift,help,nodead][mod:shift,@player] Rétablissement; [mod:alt,@player][@mouseover,help,nodead][@target,help,nodead][@player] Toucher guérisseur
```
Pourquoi : Soin sans changer de cible (Alt = toi).

### TAI Recup — Récupération (Maj : Croissance sauvage, Ctrl : Prompte guérison)

Spé : toutes · solo + groupe · touche 6 · dès le niveau 4 · par défaut  
Où : Case 6 de la barre principale (touche 6).

```
#showtooltip
/cast [mod:shift,@mouseover,help,nodead][mod:shift,help,nodead][mod:shift,@player] Croissance sauvage; [mod:ctrl] Prompte guérison; [mod:alt,@player][@mouseover,help,nodead][@target,help,nodead][@player] Récupération
```
Pourquoi : Soin sur la durée au survol ; Croissance sauvage (talent, Forever) soigne tout le groupe.

### TAI NS Soin — Rapidité de la nature + Toucher guérisseur instantané

Spé : Restauration · solo + groupe · touche Maj+V · dès le niveau 30 · par défaut  
Où : Barre secondaire, sous la barre principale (raccourci conseillé : Maj+V).

```
#showtooltip Rapidité de la nature
/cast Rapidité de la nature
/cast [mod:alt,@player][@mouseover,help,nodead][@target,help,nodead][@player] Toucher guérisseur
```
Pourquoi : Rapidité de la nature est hors temps de recharge global : le Toucher guérisseur du même appui devient instantané.

### TAI Eclat — Éclat lunaire (Maj : Essaim d'insectes) survol

Spé : toutes · solo + groupe · touche 7 · dès le niveau 4 · par défaut  
Où : Case 7 de la barre principale (touche 7).

```
#showtooltip
/cast [mod:shift,@mouseover,harm,nodead][mod:shift] Essaim d'insectes; [@mouseover,harm,nodead][] Eclat lunaire
```
Pourquoi : DoT au survol, posable sur plusieurs monstres sans changer de cible.

### TAI Stellaire — Feu stellaire (Maj : Colère)

Spé : Équilibre · solo + groupe · touche 8 · dès le niveau 20 · par défaut  
Où : Case 8 de la barre principale (touche 8).

```
#showtooltip
/cast [mod:shift,@mouseover,harm,nodead][mod:shift] Colère; [@mouseover,harm,nodead][] Feu stellaire
```
Pourquoi : Gros nuke d'Équilibre ; Colère, plus rapide, sur Maj.

### TAI Selenien — Forme de sélénien (sans la quitter)

Spé : Équilibre · solo + groupe · touche - · dès le niveau 40 · par défaut  
Où : Barre de côté : un clic quand tu en as besoin.

```
#showtooltip
/cast !Forme de sélénien
```
Pourquoi : Le « ! » évite de sortir de la forme par erreur.

### TAI Lucioles — Lucioles sur survol ou cible

Spé : toutes · solo + groupe · touche - · dès le niveau 18 · option  
Où : Barre de côté : un clic quand tu en as besoin.

```
#showtooltip
/cast [@mouseover,harm,nodead][] Lucioles
```
Pourquoi : Réduit l'armure et empêche le camouflage.

### TAI MdF — Marque du fauve (Maj : Don, Ctrl : Épines) cible > toi

Spé : toutes · solo + groupe · touche - · dès le niveau 1 · par défaut  
Où : Barre de côté : un clic quand tu en as besoin.

```
#showtooltip
/cast [mod:shift] Don du fauve; [mod:ctrl,@mouseover,help,nodead][mod:ctrl,help,nodead][mod:ctrl,@player] Epines; [mod:alt,@player][@mouseover,help,nodead][@target,help,nodead][@player] Marque du fauve
```
Pourquoi : Buffs au survol ; Épines sur le tank.

### TAI Decurse — Abolir le poison (Maj : malédiction) survol > cible > toi

Spé : toutes · solo + groupe · touche - · dès le niveau 14 · par défaut  
Où : Barre de côté : un clic quand tu en as besoin.

```
#showtooltip
/cast [mod:shift,@mouseover,help,nodead][mod:shift,help,nodead][mod:shift,@player] Délivrance de la malédiction; [mod:alt,@player][@mouseover,help,nodead][@target,help,nodead][@player] Abolir le poison
```
(variante 2, avant le sort principal)
```
#showtooltip
/cast [mod:alt,@player][@mouseover,help,nodead][@target,help,nodead][@player] Guérison du poison
```
Pourquoi : Nettoie un allié survolé sans changer de cible.

### TAI Defense — Écorce (ours : Régénération frénétique)

Spé : toutes · solo + groupe · touche Maj+Q · dès le niveau 12 · par défaut  
Où : Barre secondaire, sous la barre principale (raccourci conseillé : Maj+Q).

```
#showtooltip
/cast [form:1] Régénération frénétique; Ecorce
```
(variante 2, avant le sort principal)
```
#showtooltip
/cast [form:1] Enrager
```
Pourquoi : Ours : Régénération frénétique ; sinon Écorce.

### TAI Innerv — Innervation sur survol (Maj : toi)

Spé : toutes · groupe · touche - · dès le niveau 40 · par défaut  
Où : Barre de côté : un clic quand tu en as besoin.

```
#showtooltip
/cast [mod:shift,@player][@mouseover,help,nodead][@target,help,nodead][@player] Innervation
```
Pourquoi : Mana au soigneur survolé quand il est à sec.

### TAI Rez — Renaissance en combat, Ressusciter hors combat (survol)

Spé : toutes · groupe · touche - · dès le niveau 24 · option  
Où : Barre de côté : un clic quand tu en as besoin.

```
#showtooltip
/cast [combat,@mouseover,help,dead][combat,help,dead] Renaissance; [@mouseover,help,dead][help,dead] Ressusciter
```
Pourquoi : Forever donne au druide Ressusciter (hors combat, niveau 24) : la même touche fait Renaissance en combat.

