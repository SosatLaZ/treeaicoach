# Les addons du joueur : comment ils sont codés, et ce que TreeAI doit en retenir (étude 4.1.x)

Demande : « je veux vraiment que t'analyses les addons que j'ai, sans plagier, mais que tu regardes » + « comment ils sont
codés » + « je veux pile-poil le même truc que Suivi Forever ». Joueur : WoW: Forever 1.60.1 frFR, chasseur Horde 30,
groupe de 4. Complète `replace.md` (qui remplace quoi) et `deps.md` (ce qui casse sans eux) sans les répéter.

**Méthode / règles.** Dépôts publics clonés en lecture seule dans le bloc-notes de session (rien dans notre dépôt) :
`cjber/shortest-path-forever`, `cjber/adventure-guide-forever`, `cjber/skillup-forever` (GPL-3.0-or-later), `Questie/Questie`
(GPL-3.0), `Ralgathor/4everSwingTimer` (GPL-3.0), `openmap-world/openmap` (MIT). Lus : README, docs/, AGENTS.md,
CHANGELOG, commentaires d'en-tête et structure des fonctions clés. **Aucune ligne de code, table de données, chaîne
traduite ni image n'est reprise** ; ce qui suit décrit des *idées* avec nos mots. Toute adoption = réécriture par nous,
avec nos données (mesurées en jeu ou tirées des tables du client par nos outils), jamais leurs fichiers `Data/`/`Nav/`.

## 0. La découverte principale : « Suivi Forever » = trois addons dans une colonne partagée

Ce que le joueur appelle « Suivi Forever » n'est pas un seul addon : c'est une **colonne privée commune** (titre
« Suivi Forever ») que trois addons du même auteur (cjber) remplissent chacun avec leur section :

| Bloc vu sur ses captures | Addon qui le dessine |
|---|---|
| « Histoire : <zone> · Chapitre 1 sur 4 », liste d'arrêts, « Arrêt 1 sur 10 : Rendre : X » | **Adventure Guide Forever** (AGF) choisit et ordonne les arrêts, puis les confie à SPF |
| « Trajet 3:27 · 2.5 km », « 1. Marcher jusqu'à … 0:21 », « 2. Ascenseur pour … » | **Shortest Path Forever** (SPF) calcule chaque étape (marche, ascenseur, vol, bateau, foyer) |
| « Composants de métier » (une ligne par métier) | **SkillUp Forever** (route de montée de métier, liste de courses) |
| « 1078 m · Arrêt 1 sur 4 : Fossoyeuse » au centre de l'écran | le **marqueur de navigation natif du jeu** (SuperTrackedFrame) : SPF y pose le point utilisateur à la fin de l'étape en cours + un titre |

Donc « pile-poil le même truc » = (a) un ordre d'arrêts expliqué (histoire / chapitre), (b) chaque arrêt décomposé en
étapes de transport chronométrées, (c) les courses de métier dans la même colonne, (d) le marqueur natif au centre.
TreeAI fait déjà (a) mieux sur l'ordre (Route.Solve exact, `route41.md`) et une partie de (b) ; il lui manque surtout la
**présentation** (étapes numérotées m:ss, ascenseurs, colonne accrochée au suivi du jeu) et le **marqueur natif**.

## 1. Shortest Path Forever (SPF) — moteur de trajet

**Organisation.** TOC Interface 16001, ~95 fichiers chargés dans l'ordre : `Locales/` (l'anglais sert de clé), `Core/`
(cycle de vie, réglages, « contexte de plan » = toutes les entrées vivantes d'un calcul, API publique, capture d'erreurs),
`Data/` (généré par outils Python depuis les tables du client via wago.tools + CMaNGOS + durées InFlight, **jamais édité à
la main**, rafraîchi chaque jour par CI), `Nav/` (12 Mo de cartes de marche compressées), `Routing/` (recherche transport,
décodage des cartes, ordonnancement), `Journey/` (état du trajet, étapes, coûts, guidage, retour au corps), `Transport/`
(horaires vivants, observation des bateaux, synchronisation entre joueurs, vols, téléports), `UI/`. Tests headless LuaJIT
avec API simulées + « forever_sim » (client simulé), bancs de performance qui échouent au-delà de 3 ms par image,
lints maison (taint, valeurs secrètes, art jamais étiré). Deux SavedVariables : compte + personnage.

**Le graphe.** Nœuds : départ, arrivée, quais, maîtres de vol, portails, points d'arrivée des téléports. Arêtes :
- marche entre lieux fixes : coûts **pré-calculés hors jeu** sur le terrain (table « walks »), pondérés par les dangers ;
  marche depuis/vers ta position : recherche vivante sur la carte de marche ;
- vol : durée livrée, **remplacée par la durée que tu as mesurée** sur ce trajet ; maîtres de vol inconnus signalés ;
- bateau / zeppelin / ascenseur / tram : temps de trajet du modèle + **attente calculée depuis la phase observée**
  (« vu » une fois → l'horaire tient des heures ; partagé discrètement par messages d'addon guilde/groupe) ; jamais vu →
  attente = demi-période, affichée « environ » ;
- foyer / téléport de classe : incantation + 5 s de chargement, la recharge compte comme une attente ; parler au maître de
  vol = 3 s ; le foyer doit faire gagner ≥ 20 s ; une marche ≥ 10 min cède à un transport qui arrive à 10 % près.
La topologie est mise en cache (seules les durées de marche sont réécrites si ta vitesse change).

**L'algorithme.** Recherche par étiquettes (une étiquette par « état d'arrivée » : mode, lieux déjà visités) guidée par
une **borne inférieure admissible** obtenue par un Dijkstra inverse sans attentes ni modes (A* multi-critère). Elle donne
vite une solution « en poche », élague tout ce qui ne peut pas la battre, puis **prouve** l'optimum. Progressif : la
meilleure route connue s'affiche après 3 s, et on ne change ensuite que pour un gain ≥ min(10 %, 30 s) (hystérésis :
pas de route qui clignote). Recalcul quand on quitte le chemin ou ~1 fois/min, au changement de vitesse / de niveau.

**La marche.** HPA* : grappes = tuiles de terrain, grille de 8 m à l'intérieur, cellules à plusieurs étages (tunnels,
Fossoyeuse), puis lissage « string-pulling » pour le dessin. Données en base64+zlib décodées **à la demande par grappe**
avec `C_EncodingUtil` (natif), plafonds mémoire, libération à la fin. Toutes les recherches sont des **coroutines qui se
partagent 2 ms par image**, en pause pendant le combat. Dangers : zone ≥ 4 niveaux au-dessus ×4, villes ennemies ×6,
eau évitée (sauf Marche sur l'eau) ; lignes grises sous l'étape : « Évite Bois de la Pénombre (niveau 18-30) ».

**Étapes et temps.** Un seul vocabulaire partagé tracker/API : « Marcher jusqu'à <quai / ascenseur / maître de vol> »,
« Ascenseur pour X », « Bateau pour X », « Utilise Pierre de foyer ». Chaque étape a ses secondes ; **l'en-tête additionne
les secondes affichées** (les totaux tombent juste à l'œil, et ne défilent pas quand on est immobile). Un vol compte à
rebours d'après la progression le long du tracé. **Ascenseur** : la hauteur du joueur est inconnue (z = 0), donc l'étape
est finie quand on quitte la cage après y être resté au moins la durée du trajet ; partir avant = « reviens au palier ».
Près d'un quai : section « prochains départs ». Un clic sur l'étape : « Terminer cette étape ».

**Colonne partagée (« TrackerHost »).** Les sections vivent dans une colonne **à nous**, accrochée **sous le contenu du
suivi natif** (son cadre de fond), donc elle suit la hauteur des quêtes sans aucun recalcul ; le suivi de Blizzard n'est
jamais déplacé, caché ni enregistré auprès de son gestionnaire (taint) ; même placement en combat et hors combat ; si
Questie ou EQOT couvrent le suivi, la colonne se met dessous / à côté ; détachable (glisser le titre) ; un seul fond
(texture d'infobulle du jeu, opacité réglable). Chaque addon de la famille réserve un rang. Rendu seulement si une entrée
scalaire change ; les comptes à rebours réutilisent les lignes (pas de tremblement quand un texte change de largeur).

**Guidage.** Le point utilisateur natif est posé **à la fin de l'étape en cours** (prochain quai, ascenseur, maître de
vol), ou virage par virage en option ; le marqueur 3D natif affiche alors distance (mètres) + titre ; SPF superpose
son icône dans un cadre enfant du marqueur et rend le point précédent à la fin. Bande-boussole en haut (2 prochains
virages, prochain arrêt, destination). Flèche qui pivote en douceur et se repose à l'arrêt.

**Carte.** Ses cadres posés sur le canevas, **aucun `AddDataProvider` ni `AcquirePin`** : sur Forever l'interface manette
parcourt les épingles de la carte depuis du code sécurisé et le fournisseur d'un addon la contamine (#158). Les épingles
de quête du jeu pour un arrêt « rendre » sont masquées une à une le temps de l'arrêt (pas de doublon « ? » + numéro).

**Secrets / erreurs.** Toute valeur venant d'un autre addon ou du client passe par `canaccessvalue` ; journal d'erreurs
qui chaîne le gestionnaire existant, signature sans chiffres, conservé entre sessions ; rien en combat, aucun OnUpdate
au repos (seulement carte ouverte).

**API utile à TreeAI** (déjà en partie branchée par Route/Compass) : `NavigateRoute(owner, arrêts)` avec par arrêt
`kind` (badge « ! » / « ? » / maître de vol sur l'épingle), `hold` + `radius` (zone d'objectif), `questIDs` (arrêt
fusionné), **`handins`** (masque le « ? » du jeu), `CurrentStop`, `Ended`, `Subscribe` ; `EstimateDetail` = secondes +
**étapes `{mode, to, seconds, wait}`** sans changer le guidage ; table globale `ForeverMapMarkers` pour connaître ses
épingles ; il répond aux appels TomTom quand TomTom est absent.

## 2. Adventure Guide Forever (AGF) — « Histoire », chapitres, ordre des arrêts

**Organisation.** `Planning/` (Model, Steps, Routing, Laps, Journeys, Order, Refresh…), `Integrations/` (QuestieDB,
RestedXP, SkillUp, Tweaks, OpenMap, AtlasLoot), `UI/` (même TrackerHost que SPF, copié à l'identique dans chaque addon
de la famille), `docs/design.md` = **contrat de conception numéroté** cité par le code et les tests.

**Catalogue.** Construit depuis QuestieDB (contrat par plage de versions) dans une coroutine à **2 ms par image**, puis
**sauvé dans les SavedVariables avec une clé de révision** (hash des producteurs) : aux connexions suivantes il est
adopté d'un bloc, sans recalcul. Corrections de liens de chaînes manquants faites dans leur propre code, documentées.

**Histoires / chapitres.** La chaîne d'une quête se lit par les liens « suivante » : on remonte à la tête puis on avance.
**« Chapitre n sur N » seulement si la chaîne est prouvée** (aucune bifurcation, pas de boucle, fin connue) ; sinon
« Chapitre n » seul. Fin d'histoire prouvée → petite alerte native « Histoire terminée », une fois par personnage.
La carte « Histoire : zone » garde sa zone tant qu'il y reste du travail (pas d'aller-retour A, B, A).

**Ordre des arrêts (par défaut).** « La meilleure action maintenant », pas l'optimum global : d'abord le travail « pour
maintenant » (rendus prêts, quêtes ≤ niveau + 2, entraîneur), puis le reste ; dans chaque palier, le plus proche. Rendus
et prises à deux pas passent devant ; un objet de quête qui expire passe en tête (« avant que X expire ») ; villes
regroupées en **une visite avec liste à cocher** des PNJ ; la mer traversée une fois ; en groupe, les quêtes partagées
attirent la route. Camps d'objectif = cellules de 100 m classées par apparitions, point au médoïde, camps de mobs ≥ 3
niveaux au-dessus ou élites relégués, menu « Endroit suivant ». Coût des quêtes au-dessus du niveau : 1 + 0,5 × écart².
**Stabilité** : ordre engagé conservé entre recalculs ; si seuls des compteurs bougent (même « forme » du journal), on
repeint les chiffres **sans replanifier**.

**Tracker.** Titre = le voyage choisi ; action courante + **2 suivantes grisées** ; un arrêt partagé = un titre commun
et la progression de chaque quête dessous ; boutons d'objet sécurisés et /cible pour l'étape courante.

## 3. SkillUp Forever — « Composants de métier »

Route de montée (recettes, visites d'entraîneur, coûts, échecs prévus). Une section du Suivi Forever par métier suivi :
prochaine étape + **composants manquants**, comptés sacs + banque (connue après une ouverture) + autres persos
(Syndicator) ; un compte secret est remplacé par **le dernier compte lisible** (une valeur secrète contamine tout ce
qu'elle touche). Chez le marchand : un onglet « Composants » natif liste quoi acheter et le coût total.
**Leçon de taint (leur #327/#313)** : appeler `BuyMerchantItem` ou la fonction d'artisanat depuis du code d'addon a
contaminé le chemin du marchand / de l'artisanat (achats refusés, manette bloquée jusqu'au /reload). Ils ont **retiré
l'achat** (le joueur achète dans la fenêtre du jeu) et lancent l'artisanat par un **bouton sécurisé à attribut sort**.
TreeAI appelle encore `BuyMerchantItem` (ProfLevel, CityRun, QuickFight) et `C_TradeSkillUI.CraftRecipe` (ProfLevel) au clic.

## 4. Questie — base, icônes, suivi

**Organisation.** Modules déclarés par un chargeur maison (créer / importer un module par nom, chronométrage du
chargement) ; **la base est désormais l'addon séparé QuestieDB** (contrat 4) ; dossiers `Database/Corrections`
(listes noires, correctifs), `Modules/Map`, `FramePool`, `Tracker` (+ `LinePool`), `Tooltips`, `Libs` (ThreadLib =
coroutines, CombatQueue = file d'actions rejouées à la fin du combat). Liste de travail Forever publique (beaucoup de
« validation en jeu en attente »).

**Icônes.** HereBeDragons-Pins (sa propre copie) pour carte et minimap ; **réserve de cadres** (on reprend un cadre
libéré au lieu d'en créer) ; **file de dessin** vidée par un minuteur, au plus 24 icônes par passage (pas de pic à
l'ouverture) ; minimap : une coroutine sans fin, relancée toutes les 0,1 s, calcule les distances **au carré** (pas de
racine) et ne met à jour les icônes du bord qu'une fois par seconde (ou plus si on va vite) ; infobulle groupée : toutes
les quêtes des icônes proches de celle survolée ; taille = échelle réglée × zoom de la carte.

**Suivi.** Lignes en réserve, regroupement par zone repliable, **6 tris** (proximité, niveau, zone, zone + proximité,
terminé, et inverses), bouton d'objet, « focus » d'une quête, fondu quand la souris s'éloigne, minuteurs de quête.
Plus : icônes sur les barres de nom, lignes de quête dans les infobulles de PNJ / objets.

## 5. Barre de swing : barres natives + 4everSwingTimer

« Main droite / Main gauche / À distance » = très probablement les **barres de swing natives** de Forever
(CVar `showSwingTimer`) ou 4everSwingTimer en style natif. Ce que leurs mesures publiques établissent (rapport détaillé
dans leur dépôt) : l'évènement **`PLAYER_SWING(durée, type)`** (0 main droite, 1 main gauche, 2 distance) est fiable et
**jamais secret**, même en donjon ; `UnitAttackSpeed` / `UnitRangedDamage` sont **secrets pendant tout le combat** ;
la plage de `C_SwingTimer` est morte sur ces builds ; le journal de combat est refusé. 4everSwingTimer : une bibliothèque
reconstruit le cycle, l'addon ne fait que dessiner ; position par disposition du **mode Édition** ; visibilité « toujours /
en combat / pendant un swing / cachée » ; effets lisibles (rouge = tir coupé, ambre = déplacement qui repousse le Tir auto).
**TreeAI** (Hunter.lua, barre de tir auto) relit `UnitRangedDamage` à chaque Tir auto → **secret en combat → repli 2,0 s** :
la barre est fausse pour toute arme dont la vitesse n'est pas 2,0. Et elle double la barre native.

## 6. OpenMap (MIT) et Leatrix Plus

**OpenMap** : un seul module « worldmap » ; toutes les données (révélations, lieux, vols, grottes, tuiles de minimap)
**générées par des outils** depuis les tables du jeu, un dossier par client ; accès aux valeurs secrètes par un seul
`Safe` ; carte cousue des zones voisines, niveaux de zone, entrées de donjon, services via Questie. SPF prolonge son
trajet sur les zones voisines quand OpenMap est là ; TreeAI (PathDraw / QuestPins) ne dessine que sur la carte affichée.
**Leatrix Plus** : couvert par `replace.md` (QoL « Confort ») ; rien de neuf côté technique utile ici.

## 7. Techniques : eux / TreeAI aujourd'hui / adopter l'idée ?

| Technique | Chez eux | TreeAI aujourd'hui | Adopter ? |
|---|---|---|---|
| Données générées par outils, jamais à la main | SPF, OpenMap, CI quotidienne | `tools/wow_build_travel.py` → `Data/Travel.lua` | déjà ; **ajouter ascenseurs / tram** (nos outils, tables du client) |
| Durées mesurées remplacent les livrées | vols chronométrés | NavTime (vitesse / détour par zone), PathLearn | déjà ; étendre aux vols et bateaux réels |
| Attente bateau par phase observée | sightings + partage | demi-période fixe | oui (observation locale d'abord, sans partage réseau) |
| Recherche A* à étiquettes + preuve | SPF (1 destination) | Route.Solve exact multi-arrêts (≤ 9) | notre ordre est meilleur ; garder |
| Hystérésis de recalcul min(10 %, 30 s) | SPF | recalcul à chaque changement | **oui** (stabilité de l'ordre) |
| Repeindre les compteurs sans replanifier | AGF (« forme » du journal) | ZonePlan replanifie | **oui** |
| Coroutines, budget 2 ms partagé, pause en combat | SPF, AGF, Questie | Export/Planner/Gear/Auction (coroutines) | déjà ; un ordonnanceur commun serait mieux |
| Catalogue mis en cache avec clé de révision | AGF | export recalculé par zone | **oui** (connexion plus légère) |
| Colonne accrochée sous le suivi natif | TrackerHost | Watch = fenêtre flottante, coupée par défaut | **oui, priorité** |
| Marqueur natif à la fin de l'étape en cours | SPF Guide | point natif seulement en repli de la boussole | **oui** |
| Pas de `AddDataProvider` sur la carte | SPF (#158) | QuestPins et PathDraw en ajoutent un | **oui** (lecture seule du canevas) |
| Pas d'achat / artisanat depuis le code | SkillUp (#327) | BuyMerchantItem, CraftRecipe au clic | **vérifier en jeu, puis bouton sécurisé / achat manuel** |
| `PLAYER_SWING` au lieu des vitesses | 4everSwingTimer | UnitRangedDamage (secret) | **oui, bug** |
| Dernière valeur lisible quand c'est secret | SkillUp, OpenMap | valeurs secrètes → nil | oui (comptes de sacs, vitesse) |
| Réserve de cadres + file de dessin plafonnée | Questie | QuestPins : cadres réutilisés par index, plafonds 40/10, tout posé d'un coup | file plafonnée seulement si le profil dépasse 2 ms |
| « Prouvé ou rien » (chapitre N sur M, coûts « environ ») | AGF, SPF | « ≈ » partout | oui : « environ » seulement si non mesuré |
| Contrat de conception numéroté cité par les tests | AGF `design.md` | research/*.md par agent | idée utile pour l'équipe |

## 8. Quinze changements pour TreeAI, par priorité

1. **Barre de tir auto juste en combat** — lire la durée dans `PLAYER_SWING` (type 2) au lieu de `UnitRangedDamage`
   (secret en combat → 2,0 s faux) ; proposer de couper la nôtre si les barres natives sont actives. *Hunter.lua.*
2. **Étapes de transport numérotées et chronométrées** sous chaque arrêt : « 1. Marcher jusqu'à <lieu> 0:21 »,
   « 2. Vol pour … », « 3. Zeppelin pour … (part dans 1:40) » ; en-tête « Trajet 3:27 · 2,5 km · Arrêt 1 sur 10 : Rendre :
   X » = **somme des temps affichés**, format m:ss, compte à rebours seulement en marchant. *Route.lua (Stops), Watch.lua.*
3. **Ascenseurs (Pitons-du-Tonnerre, Poste de Librevent, Grand ascenseur, Fossoyeuse) et tram** comme arêtes de
   transport avec attente moyenne, données produites par **nos** outils depuis les tables du client ou mesurées en jeu ;
   fin d'étape « sorti de la cage après la durée du trajet ». *tools/wow_build_travel.py, Data/Travel.lua, Route.lua, NavTime.lua.*
4. **Colonne accrochée sous le suivi du jeu** (ancrée au bas de son contenu, jamais déplacé ni caché), même placement en
   combat, détachable ; c'est l'emplacement que le joueur connaît. *Watch.lua, UI.lua.*
5. **Marqueur natif au centre** : le point utilisateur posé à la fin de l'étape en cours, titre « Arrêt 1 sur 4 : X »,
   rendu à la fin ; la boussole TreeAI devient optionnelle. *Compass.lua, Planner.lua.*
6. **Avec SPF installé, déléguer les étapes** : `NavigateRoute` avec `kind`, `hold/radius`, `questIDs` et **`handins`**
   (le « ? » du jeu s'efface), suivi par `Subscribe`/`Ended` au lieu de sondes ; sans SPF, notre rendu (point 2). *Route.lua.*
7. **« Histoire : zone · Chapitre n sur N »** en tête du trajet, N affiché seulement pour une chaîne prouvée (QuestieDB
   lue en jeu, liens suivante / prérequis), alerte « Histoire terminée » une fois par perso. *QuestBrain.lua, Route.lua, Watch.lua.*
8. **Section « Composants de métier »** dans la même colonne : une ligne par métier en cours (« Couture : 6 Étoffe de lin,
   2 Fil grossier (marchand) »), stock sacs + banque + courrier déjà connu de ProfLevel. *ProfLevel.lua, Watch.lua.*
9. **Achat / artisanat sans taint** : tester en jeu (`/tai bloque`, achat manuel juste après) ; si contamination,
   artisanat par bouton sécurisé à attribut sort et achat laissé au joueur (liste + coût). *ProfLevel.lua, CityRun.lua, QuickFight.lua.*
10. **Carte sans `AddDataProvider`** : suivre zoom / glissement / carte affichée par lecture du canevas pendant qu'elle est
    ouverte (comme PathDraw le fait déjà en repli). *QuestPins.lua, PathDraw.lua.*
11. **Hystérésis de trajet** : ne changer l'ordre ou le chemin affiché que pour un gain ≥ min(10 %, 30 s) ; compteurs
    d'objectifs repeints sans replanifier. *Route.lua, ZonePlan.lua.*
12. **Attente des bateaux / zeppelins par phase observée** (heure de départ vue au quai, gardée par perso), « part dans
    1:40 » au lieu d'une demi-période, « environ » tant que rien n'est vu ; section « Prochains départs » près d'un quai.
    *Route.lua, Data/Travel.lua, Watch.lua.*
13. **Vitesses de marche et durées de vol mesurées par trajet** remplaçant les tables (on a déjà NavTime / PathLearn :
    les brancher au coût de vol et au foyer « gagne ≥ 20 s »). *NavTime.lua, Route.lua, PathLearn.lua.*
14. **Catalogue QuestieDB mis en cache** dans TreeAIDB avec une clé (version QuestieDB + version TreeAI), adopté d'un bloc
    à la connexion ; reconstruit en coroutine 2 ms seulement si la clé change. *Export.lua, Finder.lua.*
15. **Lignes d'avertissement de trajet** sous une marche : « Traverse Mille pointes (niveau 25-35) », « Évite la Croisée
    ennemie » ; et Questie-like : tri du suivi par proximité / niveau / zone, infobulle groupée des icônes voisines.
    *Route.lua, Watch.lua, QuestPins.lua.*

## 9. À vérifier en jeu

1. Les barres « Main droite / Main gauche / À distance » sont-elles les natives (`/console showSwingTimer`) ?
2. `PLAYER_SWING` arrive bien pour le Tir auto du chasseur (type 2) avec une durée lisible en combat.
3. Achat via `BuyMerchantItem` au clic : un achat manuel juste après passe-t-il ? (`/tai bloque`).
4. Le marqueur natif accepte un titre et s'affiche en mètres en frFR (« 1078 m »).
5. Ancrage sous le contenu du suivi natif : aucune erreur en entrant / sortant de combat ni en mode Édition.
