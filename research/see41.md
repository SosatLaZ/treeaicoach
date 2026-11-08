# 4.1 — ce que le joueur voit (INTEG)

Situation de SCREEN405 rejouée (`tests/wow/see41_scenario.lua`, lancé par `tests/wow/test_see41.py`, forever + modern) :
WoW: Forever frFR, chasseur Horde niveau 29, groupe de 4 (cadres de groupe du jeu en haut à gauche), aux Tarides près du
Carrefour, quêtes finies dans 5 zones (Les Tarides ×2, Mille pointes, Serres-Rocheuses, Orneval, Pitons-du-Tonnerre),
« La revanche de Gann » (3 objets au même endroit), géométrie du vrai client (1920×1080), mode sûr, réglages par défaut,
QuestieDB factice (noms français). Vidage : `SEE41=1 lua5.1 tests/wow/harness.lua -q --no-storm --geometry ready=1
--profile sur --flavor forever --class HUNTER --level 29 --allow … --scenario tests/wow/see41_scenario.lua wow_addon/TreeAI`.

## Vidage (forever)

```
ÉCRAN TreeAICompassFrame 848,896 200x112
ÉCRAN TreeAIMinimapButton 1749,926 31x31 (minimap)
ÉCRAN TreeAIStrip 144,889 621x34
BANDE Rendre (6) · À prendre (5) · Suivant · Zone (4) * · Groupe (3) · Journal · TreeAI
BOUTON Rendre : Rendre mes quêtes · touche aucune
BOUTON À prendre : Quêtes à prendre · touche aucune
BOUTON Suivant : Étape suivante · touche aucune
BOUTON Zone : Tout faire ici · touche aucune · choisi
BOUTON Groupe : Avec le groupe · touche aucune
BOUTON Journal : Nettoyer mon journal · touche aucune
BOUTON TreeAI : Fenêtre TreeAI · touche aucune
BOUSSOLE « Rendre « Le journal de Khazgorm » (+2) » · « 149 m  ·  27 s »
QUÊTES 01 tile   Rendre mes quêtes
QUÊTES 02 tile   Quêtes à prendre
QUÊTES 03 tile   Tout faire ici
QUÊTES 04 tile   Avec le groupe
QUÊTES 05 header Prochaine étape
QUÊTES 06 step   Rendre « Le journal de Khazgorm » + Rendre « Les excavateurs de Bael'dun » + Prendre « Les raiders kolkars »
QUÊTES 07 hero   Trajet ≈ 7 min · 870 m
QUÊTES 08 row    Que faire : Tout faire ici ▾
QUÊTES 09 row    Mode : Le plus rapide
QUÊTES 10 row    Zone : Les Tarides ▾
QUÊTES 11 row    Autres zones : 7 quêtes (Mille pointes, Les Pitons-du-Tonnerre…)
QUÊTES 12 header Arrêts
QUÊTES 13 step   Rendre « Le journal de Khazgorm » + Rendre « Les excavateurs de Bael'dun » + Prendre « Les raiders kolkars »
QUÊTES 14 step   Ramasse « Poignard de Tranchecrin »
QUÊTES 15 step   Tue « Raider kolkar »
QUÊTES 16 step   Rendre « Le poignard de Tranchecrin »
QUÊTES étape 1 : « Rendre « Le journal de Khazgorm » + Rendre « Les excavateurs de Bael'dun » + Prendre « Les raiders kolkars » » · « 149 m · 27 s »
QUÊTES étape 2 : « Rendre « Le journal de Khazgorm » + Rendre « Les excavateurs de Bael'dun » + Prendre « Les raiders kolkars » » · « 149 m · 27 s »
QUÊTES étape 3 : « Ramasse « Poignard de Tranchecrin » » · « objectif 0/1 · « Le poignard de Tranchecrin » · dans ≈ 2 min »
QUÊTES étape 4 : « Tue « Raider kolkar » » · « objectif · dans ≈ 4 min »
QUÊTES étape 5 : « Rendre « Le poignard de Tranchecrin » » · « dans ≈ 7 min »
QUÊTES étape 6 : « Rendre « Les raiders kolkars » » · « dans ≈ 7 min »
GROUPE 01 button Partager mes quêtes (13)
GROUPE 02 button Mode « Avec le groupe »
GROUPE 03 header Partager
GROUPE 04 text   « Le chef Galak », « La revanche de Gann », « Arikara », « Les crocs de Galak » et 9 autres
GROUPE 05 button Partager (13)
GROUPE 06 header Équipement du groupe
GROUPE 07 row    Toi · Chasseur 29 · faible : main droite (vide), distance (vide) et torse (vide) (+8)
GROUPE 08 row    Compagnon1 · Guerrier 29 · niv d'objet ≈ 24 · faible : main gauche (vide), distance (vide) et main droite (gris, niv 5)
GROUPE 09 row    Compagnon2 · Prêtre 30 · niv d'objet ≈ 26 · faible : distance (vide)
GROUPE 10 row    Compagnon3 · Mage 28 · niv d'objet ≈ 26 · faible : distance (vide)
GROUPE 11 header Échanges conseillés
GROUPE 12 text   Aucun objet échangeable vu (tes sacs, et ceux de tes amis TreeAI).
GROUPE 13 text   Le suivi du groupe est coupé (fonction bêta) : allume-le pour voir la vie, la zone et les quêtes de tes amis.
GROUPE 14 button Allumer « Groupe »
```

(rect = gauche,bas largeur×hauteur en pixels d'écran ; « * » = mode choisi ; aucune touche n'est posée d'office — le
bouton « Touches » de la bande pose Alt-1… sur les touches libres en un clic.)

## Contrôle contre LAYOUT41

| LAYOUT41 | Vu | |
|---|---|---|
| À l'écran : 4 choses seulement (bande, boussole, alertes temporaires, minimap) | bande `TreeAIStrip`, boussole `TreeAICompassFrame`, pastille minimap ; pas de carte, Horizon, Suivi, Dégâts, radar, barre « c-1 » | ✔ |
| Bande en haut à gauche, jamais sur les cadres de groupe / joueur / cible | 144,889 (à droite du cadre joueur, au-dessus des cadres de groupe) ; vérifié contre PlayerFrame, TargetFrame, PartyMemberFrame1-3, suivi, minimap | ✔ |
| Bande : Rendre (n) · À prendre (n) · Suivant · Zone · Groupe · Journal · TreeAI | `Rendre (6) · À prendre (5) · Suivant · Zone (4) * · Groupe (3) · Journal · TreeAI` ; nombres = T.QuestBrain (6 quêtes finies) | ✔ |
| Boussole = LE pointeur : flèche + « 708 m · 2 min » + une ligne | « Rendre « Le journal de Khazgorm » (+2) » · « 149 m · 27 s » | ✔ |
| Quêtes : 4 modes en gros boutons, puis « Prochaine étape » (même texte que la boussole), puis le trajet | tuiles ×4, « Prochaine étape », « Trajet ≈ 7 min · 870 m », « Arrêts » | ✔ |
| Une quête / un PNJ à un endroit = UN arrêt | arrêt 1 = les 2 remises + la prise chez le même PNJ, une seule ligne | ✔ (corrigé) |
| Boussole, « Prochaine étape » et arrêt 1 : même texte, même distance | même texte (la boussole le raccourcit à 40 lettres : « (+2) »), même « 149 m · 27 s » partout | ✔ (corrigé) |
| Groupe : … + « Équipement du groupe » | section présente (membres, niv d'objet ≈, « faible : … »), « Échanges conseillés » | ✔ |
| Rien dans le chat | 0 ligne | ✔ |

## Contradictions trouvées et corrigées

1. **Arrêt 1 écrit de 3 façons.** Boussole « Rendre « Le journal de Khazgorm » + Ren… · 149 m · 27 s », « Prochaine
   étape » = le même texte coupé, liste « Arrêts » : « Rendre « Le journal de Khazgorm » · 149 m au sud-ouest · ≈ 1 min »
   puis une 2e ligne « Rendre « Les excavateurs… » · dans ≈ 1 min » pour le même PNJ. Maintenant : la liste « Arrêts »
   regroupe un arrêt en une ligne (titre de Route.StopLabel, comme la carte et la boussole), l'arrêt que vise la boussole
   reprend la distance écrite sur la boussole (`W.CompassWhere`), « Prochaine étape » montre le texte entier de la cible
   de la boussole, et la boussole raccourcit un arrêt à plusieurs quêtes en « Rendre « A » (+2) » (`Compass.ShortLabel`).
2. **« Que faire » / « Mode » en double** sur la page Quêtes (lignes de ZonePlan rendues deux fois : en tête puis via
   les lignes du trajet) : une seule fois.
3. **Alertes d'équipement en double** (carte du groupe « arme grise · rien en torse » + « Équipement du groupe ») : avec
   GroupGear allumé, la carte du groupe ne parle plus d'équipement. « ≈ +N % » reste (infobulle : « Estimation »).

## Reste à regarder en jeu

- La page Groupe en mode sûr dit « Le suivi du groupe est coupé (fonction bêta) » : le module Groupe (membres, où ils
  sont, rejoindre) n'est pas dans le mode sûr alors que le joueur joue toujours à 4 — à décider.
- La durée « 27 s » de la boussole (vitesse de course) et les « dans ≈ 2 min » de la liste (modèle de trajet NavTime)
  viennent de deux calculs ; l'arrêt 1 affiche partout celle de la boussole.
