# BRAIN — « Pilote auto » (4.0.2) : ce qu'il décide, et ce qu'on peut mettre par-dessus

Branche `brain` (worktree /home/user/brain), base m401 `74b5117` (4.0.1 : journal + zone / boussole fusionnés).
Demande du joueur (niveau 28, chasseur horde, souvent en duo / groupe de 4) : « fais une super intelligence qui fait plein
de trucs logiques, genre cette détection-là (intention du groupe), mettre en arrière-plan et arrêter les quêtes qui servent à
rien — vraiment que des trucs logiques — et ça pour tout, et vois ce que tu peux rajouter encore par-dessus ».

Règles tenues : rien de protégé (une action protégée = un clic du joueur, et le pilote n'en fait aucune), rien d'irréversible
sans confirmation (l'abandon reste à la page « Journal », 2 clics), rien dans le chat, interface calme (UNE ligne d'avis sur
la carte, le reste dans la page « Pilote auto »), seulement des faits du jeu ou des mesures, événementiel (≤ 40 kinstr/min,
0 au repos), chaque décision visible et annulable, le choix explicite du joueur gagne pour la session.

## 1. Fait dans ce commit

| # | Décision | Données (vraies) | Déclenché par | Annuler / le joueur gagne |
|---|---|---|---|---|
| 1 | Contexte : mort, fantôme, donjon, en vol, absent, en ville, en route, à farmer, en quête, calme + drapeaux (JcJ dangereux, groupe dispersé, niveau pris, sacs, munitions, réparation, repos, journal plein) — `T.Brain.Context()` | PlayerState, IsInInstance, carte (capitales), mouvement (≥ 20 s vers un arrêt à > 600 yd), combats / quêtes avancées (5 min), UnitIsPVP + C_PvP.GetZonePVPInfo, cartes des membres, sacs, durabilité, munitions (Chasseur), GetXPExhaustion, nombre de quêtes | ses événements (aucune minuterie) | — (lecture) |
| 2 | Quête en pause (suivi + trajet), avec la raison | faits d'Abandon (grise, échouée, bloquée, doublon, donjon fait, trop dure pour le groupe ACTUEL, autre continent / ≥ 15 min) ; grise du trajet jugée avec les mêmes faits ; détour ruineux = XP réelle / détour Route-NavTime vs XP/h mesurée ≥ 10 min | trajet refait, groupe, niveau, zone | « Reprendre » / « Annuler » → gardée toute la session ; suivie à la main → reprise |
| 3 | Reprise toute seule | groupe plus grand (« vous êtes 3 »), « ton groupe l'a aussi », niveau, trajet à < 150 yd, objectif avancé, finie | idem | — |
| 4 | Jamais en pause | finie, chrono en cours, prise il y a < 5 min, avancée il y a < 15 min, visée à la main (boussole), suivie à la main (watchManual) | — | — |
| 5 | Zone choisie quand le trajet n'a PLUS RIEN et que tu n'as pas choisi | ZonePlan.counts (QuestieDB, quêtes à ton niveau), même continent, ≥ 6 quêtes, trajet Route | trajet refait / zone | « Annuler » → auto ; zone changée à la main → le pilote ne choisit plus (session) |
| 6 | Mode du trajet « Groupe » en groupe, mode d'avant quand il se dissout | ZonePlan.STYLES (ZONEFIX), GetNumGroupMembers | groupe | « Annuler » ; mode changé à la main → jamais retouché |
| 7 | Courses : arrêt inséré au plus court chez un PNJ CONNU (urgent : le plus proche ≤ 900 yd ; sinon capitale du trajet ou ≤ 250 yd d'un arrêt), note sinon | durabilité, sacs, munitions, maître (prix lu par Guichets, sinon niveaux pairs) ; PNJ QuestieDB / déjà vus (Détection) | besoin qui apparaît / devient urgent, trajet refait | « Annuler » → pas reproposée de la session ; fait à l'arrivée ; retiré quand réglé |
| 8 | Pierre de foyer conseillée | foyer prêt, ≥ 2 quêtes finies à ≤ 400 yd de l'auberge (GetNextWaypoint), plus rien au trajet, ≥ 5 min gagnées (Route) | trajet refait | (un avis, aucune action) |
| 9 | « Suivre le groupe » (coupé par défaut) | `Group.Intent()` (contrat GROUPJOIN, conf ≥ 0,6) | `GROUP_INTENT` | « Annuler » (zone) |
| 10 | En vol : « à l'arrivée, <étape> » ; en ville : « Courses ici : … » (clic = boussole) | trajet (Route:Stops), besoins | contexte | — |
| 11 | Groupe de 4 : « <donjon> possible » (clic = « Prêt pour le donjon ? ») | quête de donjon reprise (Dungeon.QuestDungeon) | groupe | — |
| 12 | Couper le pilote rend tout | — | interrupteur | — |

## 2. Par-dessus : idées (valeur pour le joueur · faisabilité · statut)

| Idée | Valeur | Faisabilité | Statut |
|---|---|---|---|
| Quête chronométrée jamais mise en pause | haute (sécurité) | facile (Quests.Timers) | **fait** |
| « Ton groupe l'a aussi » : reprise dès qu'un membre a la quête | haute (duo / groupe) | facile (Tracker.GroupOn) | **fait** |
| Groupe de 4 → donjon de ses quêtes possible, fenêtre « Prêt ? » d'un clic | haute | facile (Dungeon.QuestDungeon / ToggleChecklist) | **fait** |
| Arrêt « course » jamais reproposé 10 min après l'avoir atteint (besoin pas encore réglé) | moyenne (anti-nag) | facile | **fait** |
| Biais « tue en chemin » quand tu as du repos (XP des monstres ×2) | moyenne | contrat ZonePlan `Z:SetBias({ kill })` | demandé à ZONEFIX (README) |
| Choix explicite du joueur reconnu même s'il vaut le défaut (`styleBy` / `zoneBy`) | moyenne | contrat ZonePlan (1 champ) | demandé à ZONEFIX |
| Journal plein et une nouvelle quête proposée (QUEST_DETAIL) : « retire d'abord « X » (en pause, grise) » → page Journal | haute | moyenne (événement QUEST_DETAIL, aucun automatisme) | à faire (propriétaire : Abandon, pour un seul avis « journal ») |
| Lier la pierre de foyer à l'auberge de la zone choisie (aubergiste connu, avis seulement) | moyenne | moyenne (Détection innkeeper, Route) | à faire |
| Point de vol non appris sur le chemin de la zone choisie → arrêt « point de vol » | moyenne-haute | moyenne (Route.KnownTaxi + Data/Travel) | à faire |
| Après 2 morts sur la même quête (Combat / Session) : en pause « trop dure » jusqu'au prochain niveau ou groupe | moyenne | moyenne (faits de mort déjà gardés par Combat) | à faire (Combat est bêta) |
| Munitions / nourriture pour la durée du trajet (rythme mesuré × durée Route) | moyenne | moyenne (Stats coupé en mode sûr : la règle attendrait la mesure) | à faire |
| Farmer vs quêtes : si tu farmes et que tes quêtes rapportent plus (XP/h mesurées des deux), le dire une fois | moyenne | moyenne (Stats) | à faire |
| Absent : rien d'annoncé pendant l'absence (les décisions attendent le retour) | basse | facile | **fait** (décisions de quêtes / zone suspendues en vol, mort, absent, donjon) |
| Regroupement : « Ami est aux Tarides » | — | — | non : propriétaire = module Groupe (une info, un propriétaire) |
| JcJ dangereux : alerte d'ennemis proches | — | — | non : propriétaire = PvPMark ; le pilote expose seulement le drapeau `pvp` |
| Accepter / rendre / choisir la récompense tout seul | — | — | non : déjà une option opt-in du module Quick (proposée une fois) ; le pilote ne la double pas |

## 3. Mesures

* `session_sim` HUNTER / Forever / mode sûr : Brain ≈ 8 kinstr/min (plafond 40), total addon ≈ 510 (plafond 640) ; repos
  (`--idle`, 10 min) : Brain ≈ 0 (plafond 1). Une seule tâche lourde par image (besoins, pauses, zone, courses, foyer,
  groupe), le reste à l'image suivante.
* Déjà rouge sur la base m401 (avant ce commit) : `test_session[HUNTER-forever-sur]` — pire image 70–93 kinstr > 58
  (Dungeon.Index / Quests / Export / Abandon après chaque /reload ; jamais le pilote) et « Highlight 28–30 > 28 ». Le p99
  (plafond 25) : 25 sans le pilote, 26–27 avec (A/B, même simulation, 5 passages). Mesuré image par image : le pilote ne
  coûte que 1–5 kinstr dans chacune des ≈ 160 images où il tourne (50 passages / h, 15 relectures du contexte) ; le +1
  vient de ses gestionnaires d'événements (sacs, inventaire, quêtes) posés dans des images déjà lourdes et du trajet
  « Groupe » qu'il choisit pour le groupe de 4 de la simulation (autre trajet, autre travail des autres modules). À refaire
  par l'intégrateur (PERF4) : la marge du p99 de la base est nulle.
