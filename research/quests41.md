# QUESTS 4.1 — le cerveau des quêtes : audit (ce qui était faux · cause · correction)

Joueur (niveau 29, chasseur horde, groupe de 2-4, frFR, WoW: Forever, Questie) : « je veux le suivi de quêtes le plus fort
du monde, me dire quelle quête prendre, pas de bugs… je ne sais même pas où sélectionner « rendre mes quêtes au plus vite » ».
Puis : « réduis la dépendance de TreeAI aux autres addons ».

Méthode : un journal réaliste rejoué sur le client Forever fidèle (`tests/wow/quests41_scenario.lua`) : Mille pointes (Poste
de Librevent), Les Tarides, Serres-Rocheuses, Orneval, Pitons-du-Tonnerre ; 6 quêtes finies dans 5 zones, deux remises chez
le même PNJ, « La revanche de Gann » (Nitroglycérine, Pulpe de bois, Nitrate de sodium : trois camps du même site, ≈ 150 yd),
une quête de donjon (Kraal de Tranchebauge, PNJ listé DANS l'instance par Questie), deux grises, une élite, des quêtes à
prendre (grise, élite, donjon, autre faction, autre classe, faite, prérequis manquant, trop haute, lointaine, chaîne). Les
sorties « avant » viennent du même scénario lancé sur la base 674446f. Variante sans Questie (points du jeu seulement).

## Ce qui était faux (sorties réelles de la base) et pourquoi

| # | Ce que le joueur voyait (base 674446f) | Cause | Correction (4.1) |
|---|---|---|---|
| 1 | « La revanche de Gann » en **3 arrêts** : « 4. Ramasse « Nitrate de sodium » [10 min] · 5. Ramasse « Nitroglycérine » [19 min] · 6. Ramasse « Pulpe de bois » [28 min] » (capture 405 : « dans ~13 min », « ~25 min », même 520 m) | `Export` place chaque objet sur le plus gros amas de SON monstre (cellules de 6 %) ; `N.Nodes` ne fusionnait que des objectifs à ≤ 120 yd du 1er point d'un amas : trois camps à 145–155 yd = trois arrêts, chacun avec son temps entier | `N.Nodes` : les objectifs d'une même quête à ≤ 400 yd (sites de rechange `alt` compris) = UN arrêt ; temps sur place en partie commun (×0,6 pour les suivants, aussi dans `_pred` / `svc` que lit Route). Après : « 7. Ramasse « Nitroglycérine » + Ramasse « Pulpe de bois » + Ramasse « Nitrate de sodium » » |
| 2 | Titre de carte / thème « **Kraal de Tranchebauge** » au-dessus d'un trajet aux Tarides / Mille pointes (capture 405 n° 2) | Questie range des PNJ dans l'instance ; `firstPos` (Export) prenait cet amas ; `Z:Themes` en faisait le camp de la quête et `ThemeParts` la zone du titre (`ZoneOf` d'une carte d'instance dont le parent est le continent = l'instance elle-même) | Export : amas d'instance écartés pour une quête qui n'est pas de donjon (`X.InstanceMap`, ZoneDB de Questie) ; `Z:Themes` : jamais un camp dans une instance ; `ThemeParts` : zone = zone (dehors) de l'arrêt de la quête dans le trajet ; `Z.OutdoorZone` / `Z.IsInstance` ; nom de zone du trajet = zones de ses arrêts (`Z.StepsZoneLabel`) |
| 3 | La remise de Gann **disparaissait** du trajet (placée au fond du Kraal → « hors zone ») | même cause que 2 | remise sans lieu (pas l'instance) → en fin de trajet ; dès que le joueur parle au PNJ (ou que la quête est finie : point du jeu), à sa vraie place |
| 4 | Deux remises au même PNJ (Mangeclous, Camp Taurajo) = 2 arrêts ; une remise PRÊTE au même PNJ que des remises pas prêtes attendait la fin (31 min) | les prises / remises étaient un nœud chacune ; fusionner prête + pas prête liait les quêtes | prises et remises PRÊTES au même PNJ (≤ 25 yd) = un nœud (journal compté par nœud : `N.Delta`) ; `Route.Groups` numérote d'un seul numéro les arrêts consécutifs au même PNJ |
| 5 | Une quête **grise** du journal (« Vieux contrat », niv 21 à 29) travaillée dans le trajet | `Z:Accept` n'avait pas de règle « grise » (seul le pilote auto, coupé par défaut, la mettait en pause) | `Z:Accept` → « grise » si pas finie (finie : la remise reste) ; dit : « Hors trajet : …, 2 grises » |
| 6 | **Aucun** endroit pour « rendre mes quêtes au plus vite » ; les remises des autres zones restaient dans « Autres zones » | pas de mode : le trajet = la zone de travail | mode « Rendre mes quêtes » : remises des quêtes finies, toutes zones, plus court chemin (glouton + 2-opt), vols / bateaux / foyer — le foyer à n'importe quelle étape une fois (`Zp.hearthAny`). Après : « 1. Rendre « Les crocs de Galak » · 2. Vol pour Camp Taurajo · 3. Rendre « Le journal de Khazgorm » + Rendre « Les excavateurs de Bael'dun » · 4. Vol pour Les Pitons-du-Tonnerre · 5. Rendre « La flamme sacrée » · 6. Vol pour Retraite de Roche-Soleil · 7. Rendre « Impasse aux Serres-Rocheuses » · 8. Pierre de foyer vers Orgrimmar · 9. Vol pour Poste de Bois-brisé · 10. Rendre « Les eaux du lac Mystral » [22 min] » |
| 7 | « Vol pour **Thunder Bluff** » (anglais) | `Data/Travel.lua` nomme ce maître de vol en anglais | `Route.PlaceName` : nom du jeu (carte des vols, lu par `KnownTaxi`), sinon nom de la carte du client pour les capitales écrites en anglais → « Vol pour Les Pitons-du-Tonnerre » |
| 8 | Quêtes à prendre : liste « Facile à côté » triée par niveau, sans temps ni raison ; objectifs comptés « 1 » | `Quests.QuestieScan` (tri par niveau) ; QuestieDB ne donne pas le nombre demandé d'une quête pas prise → `n = 1` (temps 6 à 8 fois trop court dans le trajet aussi) | `QuestBrain.PickupList` : XP réelle (QuestieDB / jeu) + monstres × part du groupe, temps = trajet jusqu'au donneur (PathLearn → Route → NavTime) + objectifs (modèle de coût, groupe) + retour, suite de chaîne (+½ XP, « ouvre la suite « … » »), « ton groupe l'a », niveau +2 ; classement par XP / min à 2 chiffres significatifs (stable) ; nombre par défaut 8 monstres / 6 objets (`E.DEFAULT_N`) |
| 9 | Quête de **donjon** à prendre jamais proposée, même à 4 qui veulent des donjons | l'export ne prenait que les quêtes dont la zone QuestieDB est une zone de travail (zone = l'instance) | export : quêtes de donjon de niveau proche dont le DONNEUR est dans ces zones ; proposées seulement à 4 et si « donjons » est coché |
| 10 | Quêtes d'un ami à prendre sans filtre race / classe | boucle `squadQ` d'Export sans `raceOK` / `classOK` | filtrées |
| 11 | Mode « Groupe » : les quêtes communes n'arrivaient pas d'abord (Pillards avant Arikara) ; sur un client classique, jamais vues | bonus du glouton défait par le 2-opt (chemin seul) ; `C_QuestLog.IsUnitOnQuest` seul | « Avec le groupe » : quêtes communes d'un seul tenant (`N.TimersFirst` sur l'ensemble commun), ce qui est à portée de main devant ; repli `IsUnitOnQuest(index, unité)` |
| 12 | Suivi Forever gardait l'ancien ordre après un trajet refait (« Arikara » chez SPF, « Gann » chez TreeAI) | `Route:OnPlanStep` n'était appelé que si le N° d'étape changeait ; après un recalcul `cursor` repart de nil | après chaque recalcul `Route:OnPlanStep`, qui ne renvoie que si la liste a changé (signature) |
| 13 | Trajet « Rendre / Prendre » vidé par le masque de zone | `Z:MaskSteps` (fait pour les plans de l'appli) masquait aussi les arrêts hors zone du trajet en jeu | toutes zones pour ces deux modes en jeu |
| 14 | Sans Questie : rien à prendre, objectifs sans lieu (1 placé + 2 « sans lieu »), remises en fin | Questie seule source des donneurs ; un seul objectif recevait le point du jeu | PNJ appris en jouant (GOSSIP_SHOW / QUEST_GREETING / QUEST_DETAIL / QUEST_PROGRESS / QUEST_COMPLETE : position + nom du jeu), points de quête du jeu (`GetNextWaypoint`, `GetQuestsOnMap`) pour tous les objectifs ; « Quêtes à prendre » dit ce qu'il manque : « Sans Questie, TreeAI ne connaît que les quêtes des PNJ à qui tu as parlé… » |

## Les modes (une seule source : `T.QuestBrain`, le trajet suit)

* « Rendre mes quêtes » (`turnin`), « Quêtes à prendre » (`pickup`), « Tout faire ici » (`zone`, défaut), « Avec le groupe »
  (`group`). Un clic : ligne « Que faire : … ▾ » de la vue Trajet (menu avec les nombres), `/tai rendre`, `/tai prendre`,
  `/tai cerveau zone|groupe`, et la bande de boutons (agent STRIP) via `Modes()` / `SetMode(id)`. Le sous-titre de la carte
  et de la page (`Z:StyleName()`) dit le mode. Plus rien à rendre / à prendre → retour à « Tout faire ici », dit une fois.
* « Suivant » (bande de boutons, agent STRIP) : `T.QuestBrain.Skip()` passe l'arrêt 1 — ses quêtes sortent du trajet pour
  la session, tout de suite (masque) puis au recalcul, et c'est dit (« Hors trajet : 1 passée (Suivant) ») ; `Prev()` /
  `Unskip(q)` les remettent.
* UN libellé par arrêt, le même dans l'onglet, la carte (une ligne par arrêt, plus trois lignes « dans ~13 min ») et la
  boussole : « Ramasse « Nitroglycérine » 0/6, « Pulpe de bois » 0/6, « Nitrate de sodium » 0/6 » (`Route.StopLabel`) ;
  plusieurs quêtes au même endroit : « Rendre « A » + Rendre « B » ».
* Contrat Planner → Route inchangé : `ZonePlan:BuildRoute` fait les étapes du mode, `Planner.steps` les porte, Route les lit.
  Aucun crochet supplémentaire dans Route (seulement le foyer à toute étape en « Rendre », la signature SPF, les noms FR).

## Pas fait ici (propriétaires ailleurs) — pour l'intégrateur

* **Distance / temps identiques partout** (capture 405 n° 1 : 708 / 711 / 699 / 520 m) : la boussole calcule en direct,
  la carte lit `Route:Stops` mémorisé (≤ 5 s, ≤ 5 min immobile), le Suivi TreeAI sa propre distance. Il faut `T.Next()`
  (ONEGUIDE) : une position, un arrondi (Compass), lu par tous. L'arrêt 1, lui, est désormais le même objet partout.
* **« Arrêt 1 sur 10 : Accomplir 2 objectifs · Arikara »** = l'itinéraire de Shortest Path Forever lui-même : avec
  « Guidage : TreeAI » (défaut, `T:GuideMode()`), TreeAI ne lui envoie RIEN, SPF calcule le sien. Deux planificateurs à
  l'écran. Choix produit : envoyer notre ordre à SPF par défaut quand il est installé (`guide = "auto"`), ou dire à SPF de se
  taire — à trancher (le renvoi après recalcul est corrigé, n° 12).
* Bindings `TREEAI_QB_*` à déclarer (Binds.lua / Bindings.xml, agent STRIP) ; `Modes()[i].key` les lit déjà.

## Vérifié (scénario, 3 saveurs + sans Questie)

A zone : un arrêt par quête et par lieu, jamais d'instance (titre, zone, arrêts), élite à 4 oui / seul non (dit), grises
dehors (dites), autres zones dehors, ordre stable (recalcul, quelques pas), aucun mot anglais, SPF à jour · B API + un mode
actif + `QUEST_MODE_CHANGED` · C « Rendre » : 6 remises, PNJ en français, vols + foyer, même PNJ = 1 arrêt, stable, le menu
dit « Rendre mes quêtes (6) » · D « Prendre » : filtres (grise, donjon non voulu, autre faction, faite, prérequis, trop
haute, autre classe), élite à 4, chaîne dite, lointaine après la proche, donjon à 4 et voulu, rien en solo d'élite /
donjon, classement stable · E groupe : quêtes communes d'abord · F solo : élite dehors, dite · G rien dans le chat ·
N sans Questie : zone, remises, vols / foyer, rien d'inventé + phrase claire, PNJ rencontrés proposés avec leur nom du jeu,
grise écartée, remise apprise, trajet « Prendre » nommé, groupe.
