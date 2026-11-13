# ROUTE6 (5.1) — « le trajet dit vrai » : notes de travail

Base : 5.0.0 (4f70463). Audit indépendant AUDIT5, sections B1–B12, C1, D. Tout est mesuré **en simulation** (mock
« forever », profil par défaut « sur » et « tout ») ; rien n'a été vu dans le vrai client 1.60.1.

## Ce qui a été appris

### Re-tri depuis la position (B1)
- Le plan n'était refait que sur une signature (quêtes, niveau, zone en mode auto seulement) : marcher 2,6 km ne
  changeait rien. Maintenant : re-tri **des mêmes arrêts** (travail découpé ≤ 2 ms / tranche, `Planner:Reorder` →
  `ZonePlan:Reorder`) quand le joueur est à ≥ 165 yd du point de calcul (`P.MOVE_YD`), au plus toutes les 6 s, jamais en
  vol / mort / combat ; et 2 s après PLAYER_ALIVE / PLAYER_UNGHOST / PLAYER_CONTROL_GAINED (relevé, atterri). Un recalcul
  complet seulement si la signature change (zone dans tous les modes, révision de l'export).
- Immobile : 0 re-tri, 0 recalcul (2 min mesurées). Pas de 30 m : rien.

### Coût d'une image en marchant (règle des 4 ms)
- Mesure `tests/wow/route6_world.lua` (25 images / s, 50 kinstr = 1 ms comme `session_sim.lua`). La couche « forever » du
  mock (fonctions définies après `WowMock.FOREVER_LAYER_LINE`) est du code C dans le jeu : elle avance l'horloge mais
  n'est pas comptée comme travail TreeAI — **même règle que la simulation de session** (avant, la mesure du scénario la
  comptait : ~50 à 90 kinstr de « wowmock » par image).
- La vérification « donneur atteint » (`QB.VisitTick` : relevé du journal + quêtes à prendre ≈ 1 ms) tombait dans la même
  image que le minuteur du Planner, le rafraîchissement de la boussole et le re-tri. Elle passe maintenant par la file
  « un travail par image » (`T:Spread`).
- Pire image en marchant 1,6 km (D1) : « sur » 154 kinstr (≈ 3,1 ms), « tout » 181 kinstr (≈ 3,6 ms) ; 6 re-tris.

### Shortest Path Forever (B3)
- Point d'entrée vérifié dans notre code : `ShortestPathForever.API.Estimate(carte, x, y, carte, x, y)` (0..1 →
  secondes, ou nil + « combat » / « invalid » / « unreachable »). 5.0.0 ne l'appelait que si le joueur avait choisi
  « Guidage : Suivi Forever » (`Compass.SPF`, `Compass.Travel`) et seulement pour **afficher** 3 trajets.
- 5.1 : `NavTime` le lit **sans opt-in**, en lecture seule, hors combat, sous pcall, au plus **un appel par image**
  (file `T:Spread`), réponses gardées 15 min par paire de cases de 100 yd. Chaque réponse d'un trajet à pied étalonne le
  facteur de détour de sa zone (source « spf » après 3 réponses) : l'ORDRE des arrêts en profite sans demander les n²
  trajets. Forme d'appel « . » d'abord, « : » une fois si la première ne répond pas (non vérifié en jeu).
- Ordre des sources : estimation SPF → chemins appris (PathLearn) → facteur de zone (« relief », puis « défaut »).
  `/tai erreurs` dit lesquelles ont servi (`NavTime.CostLines`, section « == Coût des trajets »).
- Ascenseurs / tram : liste vide, laissée vide (aucune position inventée).

### Chemins appris dans le profil par défaut
- `PathLearn` n'a ni cadre ni fonction protégée ; il range une position par seconde seulement quand le joueur bouge
  (échantillon de NavTime). Les requêtes suivent ce qui est appris au plus toutes les 30 s (`PL.QUERY_S`) : sinon chaque
  case apprise vidait les mémos et le trajet refaisait tous ses chemins. Allumé une fois pour qui est resté en « mode
  sûr » ; un « coupé » choisi par le joueur n'est jamais défait. Coût mesuré : simulation de session « sur » (60 min,
  chasseur Forever) = 856 kinstr/min pour tout l'addon (plafond 890, inchangé), PathLearn sous le seuil d'affichage par
  module ; en marchant 1,6 km (scénario route6_walk) la pire image reste ≤ 154 kinstr (« sur »).

### Morts (B3 / D2)
- Seulement les morts du joueur (PLAYER_DEAD, hors instance) : une case de 100 yd où il est mort 2 fois à un niveau encore
  proche (≤ 2 niveaux de moins) coûte 120 s de plus dans l'ORDRE (jamais dans le temps affiché) ; ligne « Zone
  dangereuse : tu es mort 2 fois près de … ». Trois niveaux plus tard, plus rien.

### Questie (B8)
- Un seul traducteur zone Questie → carte (`Export.AreaToMap`) : méthode de l'ancien addon, table privée de la
  bibliothèque QuestieDB, table de l'addon. Les vendeurs d'objets de quête sont trouvés avec la bibliothèque seule.
  Une zone inconnue n'est plus mémorisée comme « absente » (Questie charge tard).

## À vérifier dans le vrai jeu
- Forme d'appel et réponses réelles de `ShortestPathForever.API.Estimate` ; coût réel de l'appel.
- Tables Questie réellement présentes sur Forever (bibliothèque seule ? ancien addon ?).
- `TAXIMAP_OPENED` : le drapeau « non découvert » des points de vol (`isUndiscovered`).
- Barres de nom / cible / fenêtre de PNJ comme preuve qu'un donneur est là (question « Ce PNJ n'est pas là ? »).
- Règle des 4 ms : `/tai pics` après une soirée réelle.
