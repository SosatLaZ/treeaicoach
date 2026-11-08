# Route 4.1 — audit « le chemin le plus court qui marche réellement » (agent ROUTEFIX)

Joueur (niv. 30, chasseur Horde, groupe de 4, frFR) : « par pitié il faut que le chemin le plus court marche
réellement » · « le trajet calcule pas le dénivelé ni les virages ».

Méthode : `tests/wow/routefix_scenario.lua` (lancé par `tests/wow/test_routefix.py`, mocks modern + Forever). Cas
réalistes sur les **vraies** tables de voyage livrées (`Data/Travel.lua` : maîtres de vol, vols, zeppelins), lieux de
quêtes fictifs autour d'Orgrimmar, La Croisée, Les Pitons-du-Tonnerre, Serres-Rocheuses (Retraite de Roche-Soleil),
Mille pointes (Poste de Librevent), Orneval (Bois-brisé), Camp Taurajo ; vitesse montée 11,2 yd/s. Pour chaque cas :
ordre de l'ancien moteur 4.1.0 (glouton + 2-opt/Or-opt de ZonePlan, avec sa façon de compter le foyer), ordre du
nouveau, et **optimum par force brute** sur le même modèle de coût (≤ 9 arrêts). 40 instances aléatoires en plus
(4–9 arrêts, chaînes prendre → objectif → rendre, foyer, journal presque plein).

## Résultats

| Cas | Optimum | 4.1.1 | 4.1.0 |
|---|---|---|---|
| A — rendre 8 quêtes dans 5 zones, foyer prêt (auberge Orgrimmar) | 840 s | 840 s | 1 210 s (**+44 %**) |
| B — même chose, foyer en recharge | 1 134 s | 1 134 s | 1 134 s |
| C — même chose, vols inconnus (seulement La Croisée) | 1 554 s | 1 554 s | 1 554 s |
| D — Mille pointes, 3 chaînes + remise aux Pitons, foyer prêt | 599 s | 599 s | 599 s |
| E — 3 remises en Durotar / Tarides + 1 à Tirisfal (zeppelin) | 756 s | 756 s | 756 s |
| F — 1 remise à 150 yd + 4 à Orgrimmar (auberge), depuis La Croisée | 72 s | 72 s | 230 s (**+221 %**) |
| 40 instances aléatoires (4–9 arrêts) | — | pire rapport **1,0000** | — |
| Mesa (1 300 yd de rampe, 500 yd à vol d'oiseau), temps réel 186 s | — | 183 s (−2 %) | 82 s (**−56 %**) |

Bon sens vérifié : saut de 300 yd = à pied ; un vol seulement entre deux maîtres de vol **connus** (Orgrimmar inconnu →
aucun vol) ; zeppelin avec attente (période / 2 + traversée) ; foyer au plus une fois ; chaînes dans l'ordre ; l'autre
continent visité une seule fois, à un bout du trajet.

## Cas faux · cause · correction

1. **Foyer compté à chaque trajet** (« Rendre mes quêtes », cas A, +44 %). Cause : ZonePlan pliait
   `min(trajet, foyer + auberge→arrêt)` dans **chaque** case de la matrice ; l'ordre supposait 3 foyers, le trajet
   affiché n'en utilisait qu'un (le 1er). Correction : coût exact « foyer une seule fois, au meilleur trajet »
   (`Route.OrderCost`), état « foyer utilisé » dans la programmation dynamique (`Route.Solve`), même règle à l'affichage
   (`Route:StopsRaw` → `hearthChoice`).
2. **Foyer retiré dès qu'un arrêt est à moins de 5 min** (cas F, +221 %). Cause : règle QA-QUEST F1
   (`ZonePlan.noHearth`) qui interdisait le foyer pour tout le trajet ; le joueur volait La Croisée → Orgrimmar au lieu de
   rendre ici puis lancer sa pierre. Correction : le foyer est permis à n'importe quel trajet, une fois ; le coût exact
   décide (rendre ici, PUIS foyer). La règle n'a plus d'effet sur l'ordre ni sur l'affichage.
3. **Ordre seulement local** (glouton + 2-opt) : optimal dans les cas B–E ici, mais sans garantie. Correction :
   Held-Karp exact jusqu'à 10 arrêts (précédences + journal + foyer), au-delà insertion la moins chère + Or-opt (1–3) +
   2-opt sur le coût exact, deux départs (ordre donné, insertion). Force brute : pire rapport 1,0000 sur 40 cas.
4. **Stabilité** : hystérésis 10 % **et 60 s** → un gain de 45 s sur un trajet de 6 min (12 %) était refusé.
   Correction : > 10 % et > 30 s (`Route.Choose`).
5. **Vitesse** : trajet calculé à pied (7 yd/s) même monté / avec une monture au niveau 30 ; défaut monté 14 yd/s
   (+100 %) alors qu'une monture 30–40 fait +60 %. Correction : `NavTime.TravelSpeed` (monté si en selle, ou niveau ≥ 30
   avec une monture connue — vue une fois ou dans le journal des montures), défaut 11,2 yd/s, 3 s pour monter en selle par
   vrai trajet ; la boussole utilise la même vitesse.
6. **Relief / virages** : un seul facteur de détour, celui de la zone **actuelle**, appliqué à tous les trajets (même
   dans une autre zone) ; aucune notion de falaise (mesa : 82 s prévus, 186 s réels). Corrections : détour de la zone
   **de chaque trajet** ; relief a priori par zone de montagne (Serres-Rocheuses 1,45, Mille pointes 1,35, Pitons 1,5,
   Orgrimmar 1,3…, remplacé dès 2 trajets mesurés) ; **chemins appris** (`Modules/PathLearn.lua`) : cases de 20 yd,
   arêtes avec la longueur réellement parcourue, échantillonnées pendant le mouvement (échantillonneur de NavTime, aucun
   OnUpdate nouveau, jamais en vol / instance / mort), positions du groupe à 1/3 de poids ; A* sur ces arêtes quand la
   ligne droite traverse des cases jamais parcourues. Mesa : 183 s prévus pour 186 s réels.
7. **Boussole en ligne droite vers le haut de la falaise**. Correction : `PathLearn.Waypoint` (point le plus loin du
   chemin visible en ligne droite ; seulement si le virage > 35°) — la flèche vise ce point, étiquette « Par le chemin »,
   la distance et le temps restent ceux de l'arrêt ; la traînée de la minimap suit le chemin appris.
8. **Deux listes d'arrêts** : `Stops(1)` (boussole, « Suivre », voyage rapide) et `Stops(64)` (onglet, fenêtre, carte)
   étaient calculées séparément ; le foyer jugé sur le 1er trajet d'un côté et sur tout le parcours de l'autre pouvait
   donner deux arrêts 1. Correction : `Stops(n)` = début de `Stops(64)` (même objet). La boussole calcule son temps avec
   la formule du trajet (`Route.WalkSecMap`).
9. **Temps sur place faux** : sans `_pred`, 60 % de l'écart d'« eta » (qui compte le trajet) passait pour du travail
   (3 min « sur place » pour une remise après 5 min de vol). Correction : `s.svc` (15 s pour prendre / rendre).
10. **Vols appris ignorés hors vols directs** : une durée chronométrée A → B (relais compris) n'était lue que si A → B
    était un vol direct de la table. Correction : toute durée apprise entre deux maîtres connus compte.
11. **Suivi Forever, choix « Lui donner l'ordre de TreeAI »** : l'ordre n'était renvoyé que si « Suivre » avait été
    cliqué dans la session ; après un /reload, SPF gardait son trajet. Correction : `Route.WantFollow()` (choix gardé) →
    renvoi après chaque trajet refait, après la purge de démarrage (trajets empilés retirés puis UN envoi) et à la reprise
    après pause ; rien n'est renvoyé si les arrêts n'ont pas changé.

## Branchement (ZonePlan appartient à ZONEPICK)

`Route.InstallEngine(ZonePlan.Engine)` (appelé à l'activation de Route) remplace `Matrix / Improve / Cost / Choose` du
moteur de ZonePlan par le solveur de Route, même contrat, sans toucher au fichier. Changement propre souhaité dans
`ZonePlan:BuildRoute` : appeler directement `T.Route.Solve(nodes, ord, D, { cap, logCount, yield })` sur une matrice
sans foyer + `D.hs`, supprimer `noHearth` / le `legN` « foyer à chaque trajet », puis retirer le pont.
