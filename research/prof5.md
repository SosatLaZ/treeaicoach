# PROF 5.0 — « métiers sans erreur » : audit des données, ce qui est lu en jeu, ce qui reste estimé (13 nov 2026)

Demande : « meilleur assistant pour les métiers, beaucoup plus autonome et sans erreur, SURTOUT sans erreur ». Le joueur a
Cuisine, Secourisme, Travail du cuir (+ Dépeçage). Quatre plaintes réelles rejouées d'abord (tests/wow/prof5_scenario.lua),
puis corrigées : « achète à l'HV » alors qu'apprendre la recette suivante chez le maître faisait mieux ; impossible de sauter
d'une étape à l'autre ; « Apprendre X à 67 » à 92 de compétence ; quantités invraisemblables (« 28/271 Cuir moyen »).

## 1. Audit des données (tools/wow_audit_recipes.py, tables client 1.60.1.70862, cache hors ligne)

- Reconstruction depuis les tables (1.60.1.70862) = fichier livré : **oui**
- Version 1.60.1.70888 (10 nov) : `SkillLineAbility` identique ligne à ligne (0 ajout, 0 retrait, 0 changement).

| Métier | Recettes | Apprises avec le métier | Exigence de la table | Patron (exigence de l'objet) | Maître : exigence ESTIMÉE (= seuil jaune) |
|---|---|---|---|---|---|
| Secourisme | 31 | 1 | 0 | 15 | 15 |
| Forge | 473 | 5 | 10 | 347 | 111 |
| Travail du cuir | 552 | 6 | 0 | 436 | 110 |
| Alchimie | 187 | 3 | 1 | 133 | 50 |
| Herboristerie | 3 | 0 | 0 | 2 | 1 |
| Cuisine | 130 | 4 | 0 | 114 | 12 |
| Minage | 18 | 1 | 0 | 2 | 15 |
| Couture | 444 | 5 | 0 | 332 | 107 |
| Ingénierie | 264 | 3 | 0 | 162 | 99 |
| Enchantement | 251 | 5 | 0 | 164 | 82 |
| Pêche | 3 | 0 | 0 | 2 | 1 |
| Dépeçage | 3 | 0 | 0 | 2 | 1 |

Lecture du tableau, champ par champ :

| Champ | Source | État |
|---|---|---|
| Recettes d'un métier, objet créé, quantité | `SkillLineAbility` + `SpellEffect` 24 | exact (client) |
| Couleurs : jaune (`TrivialSkillLineRankLow`), gris (`…High`), vert = milieu | client | exact pour la table ; **corrigé en jeu** par la fenêtre du métier (`relativeDifficulty`, `maxTrivialLevel`) |
| Points par fabrication (`NumSkillUps`) | client | exact |
| Composants et quantités | `SpellReagents` | exact pour la table ; **relus en jeu** (`C_TradeSkillUI.GetRecipeSchematic`, 5.0) : la lecture gagne |
| Compétence pour APPRENDRE une recette du maître | **absente du client** (donnée serveur) | **ESTIMÉE = seuil jaune** (drapeau 2), « à confirmer chez le maître » ; **lue chez le maître** (compte entier) |
| Compétence d'un patron (objet-recette) | `ItemSparse.RequiredSkillRank` | exact |
| Niveau du perso et compétence pour passer un rang | **absents du client** | valeurs Classic (5/10/20/35, 0/50/125/200) = estimation ; **lus chez le maître** (5.0 : `trank`) |
| Plafonds de rang 75/150/225/300 | sorts de rang (effet 118, palier 1 à 4) | exact (client) |
| Qui vend quoi | **absent du client** | liste tenue à la main (44 objets Classic) ; **prix et lot relus chez chaque marchand visité** |
| Prix d'achat et lot d'un composant de marchand | `ItemSparse.BuyPrice`, `VendorStackCount` | exact (hors réputation) ; le prix lu chez le marchand passe avant |

Erreurs trouvées et corrigées dans la table :

- **2 exigences fausses** : `MinSkillLineRank` valait 75 pour « Pierre à aiguiser grossière » (grise à 55) et 125 pour « Pierre à
  aiguiser lourde » (grise à 115) — restes d'anciennes valeurs : la recette aurait été grise le jour où on l'apprend. Le
  générateur traite maintenant toute exigence de table AU-DESSUS du seuil jaune comme une estimation (drapeau 2).
- Les 11 autres exigences « de table » (Forge 10, Alchimie 1) sont sous leur seuil jaune : gardées, corrigées par le maître.
- Comparaison avec Classic Era : 1039 recettes aux mêmes seuils, 604 aux seuils changés par Forever ; 26 recettes où Era porte une exigence de maître que Forever n'a pas (Era n'est donc pas une source fiable pour Forever : non utilisée).

Anomalies signalées (aucune ne peut produire un mauvais conseil : la recette n'est pas planifiée ou le nom est lu en jeu) :

- 225 × objet créé absent de la table des objets (nom lu en jeu)
- 127 × composant absent de la table des objets (nom et prix lus en jeu, sinon « à farmer »)
- 25 × recette déjà grise le jour où on l’apprend (jamais planifiée : chance 0)
- 3 × recette sans seuils (jaune 0 / gris 0 : jamais planifiée)

Rangs :

- Secourisme : plafonds 75, 150, 225, 300 (client) ; niveau et compétence pour passer le rang : absents du client → valeurs Classic, **à confirmer chez le maître**.
- Forge : plafonds 75, 150, 225, 300 (client) ; niveau et compétence pour passer le rang : absents du client → valeurs Classic, **à confirmer chez le maître**.
- Travail du cuir : plafonds 75, 150, 225, 300 (client) ; niveau et compétence pour passer le rang : absents du client → valeurs Classic, **à confirmer chez le maître**.
- (les 12 métiers ont les 4 mêmes plafonds ; aucun livre de rang pour Cuisine / Secourisme / Pêche dans les objets de Forever)

Composants de marchand : 44 objets, tous avec un prix ; vendus par lots : Eau de source (lot de 5), Lait glacé (lot de 5), Jus de melon (lot de 5), Epices douces (lot de 5), Fiole vide (lot de 5), Fiole plombée (lot de 5), Pomme rouge brillante (lot de 5), Fiole de cristal (lot de 5), Epices spéciales (lot de 5), Fiole imprégnée (lot de 5).

## 2. « Lu dans le jeu d'abord » : ce que TreeAI lit, et quand

| Fenêtre du jeu | Ce qui est lu (et gardé) | Effet sur le plan |
|---|---|---|
| Métier (`TRADE_SKILL_*`) | recettes connues, couleur réelle à ta compétence, gris exact, **composants réels** | seuils corrigés, quantités corrigées |
| Maître (`TRAINER_SHOW/UPDATE`) | par recette : exigence, prix, niveau requis, apprenable maintenant, déjà apprise ; **rang suivant** (compétence, niveau, prix) ; sa place | « dès N » exact, « Apprends X chez ton maître », rang au bon niveau |
| Marchand | ce qu'il vend, prix du lot, taille du lot ; sa place | prix et lots exacts |
| Banque / courrier | stocks (datés : « banque vue il y a 2 h ») | compos déjà possédées |
| HV (TreeAI / Auctionator) | prix moyen + confiance | sinon « à farmer », jamais un prix inventé |

**Bug réel trouvé (fidélité)** : la fenêtre du maître de Forever suit la famille Mainline — `GetTrainerServiceInfo` rend
`nom, type, icône, niveau requis`, pas `nom, sous-texte, type` (Era). L'ancien code lisait le type à la 3e place : sur le vrai
client « déjà apprise » n'était jamais reconnu, et une exigence illisible devenait « 0 » (= « apprenable tout de suite »).
5.0 : `PL.TrainerService` cherche le type aux deux places, ne garde un nombre que s'il est lu, et `0` n'est plus jamais une
exigence. Testé avec les deux signatures.

Ce qui reste estimé est dit : « à confirmer chez le maître » sur l'étape, « Inconnu : … » sur la carte du métier.
Règles nouvelles :
- maître jamais lu + recettes du maître à moins de 25 points → **« Va d'abord chez ton maître »** en première étape, et
  tout achat à l'HV est marqué « à confirmer : le maître d'abord » ;
- maître lu : une recette « du maître » qu'il ne propose pas dans sa plage n'est **pas planifiée** (elle vient d'ailleurs) ;
- une recette dont l'exigence est atteinte se prend **à la première visite** (« Apprends X chez ton maître »), même si le
  plan ne s'en sert que plus tard ; « (dès N) » = la vraie exigence, jamais le moment où le plan s'en sert.

## 3. Planificateur : ce qui a changé

- **Chances par couleur** (inchangé) : orange 100 %, jaune / vert (gris − compétence) / (gris − jaune), + Heures sup.
- **Fourchettes** : chaque étape jaune / verte porte « ≈ N (de A à B) » = espérance ± 1,28 σ (8 fois sur 10), σ² = Σ (1 − c) / c
  par fabrication ; une étape orange est un compte exact.
- **Vert foncé** : sous 25 % de chance une recette n'est prise que si rien d'autre ne monte (et c'est écrit : « ≈ 5 par point »).
- **Compo qui se récolte** : plus jamais fabriquée en masse depuis une autre (« 165× Cuir lourd, 807 Cuir moyen à farmer »).
- **Ton or** : sans cible choisie, le plan s'arrête où 80 % de ton or s'arrêtent (« Avec ton or : 92 → 122 (but 150) », le prix de
  la suite est dit). Le plan d'une cible plus basse est le début du plan complet (prouvé par test).
- **Garde-fous** (`Prof.Engine.Audit`, `PL.Check`) : aucune étape sous ta compétence / au-dessus du plafond, aucune recette
  grise ou pas encore apprenable, fabrications ≤ Σ 1 / chance + 1, compos = Σ fabrications × quantité, rien d'acheté tant
  qu'il reste du stock, coût = Σ prix. Vérifié sur tous les métiers × compétences 1..299 (Lua : 5 136 plans ; Python :
  16 146) ; **en jeu, un plan incohérent n'est jamais affiché** (noté dans le diagnostic).
- Prix : marchand (lu, sinon table) → HV TreeAI (confiance ≥ 30 %) → Auctionator → fabriqué → sinon « à farmer ».

## 4. Idées retenues de SkillUp Forever (lu : README, docs, structure — aucune ligne ni donnée reprise)

- Les **visites au maître** sont des étapes du trajet, pas une note : chez nous « Va d'abord chez ton maître », puis une visite
  par palier (rang + recettes du même moment).
- Les **échecs prévus** (fabrications sans point) : chez nous la fourchette par étape et la marge de 10 % au marchand.
- **Dernier compte lisible** quand un compte est secret (en combat) : repris pour les sacs (`bagCount`), sinon le plan croit
  qu'il faut tout racheter.
- **Achat / artisanat sans contamination** : chaque action passe par `T:ShopCall` (dans le clic, hors combat) et le « mode
  sûr » montre au lieu d'agir. Leur choix (ne plus acheter du tout) reste notre repli si `/tai bloque` montre un blocage.

## 5. Ce qui n'est PAS fait / à vérifier en jeu

1. La liste du maître montre-t-elle bien les recettes « indisponibles » avec leur compétence (filtre coché) ? Sinon
   seules les recettes apprenables sont lues (le plan reste juste, mais « dès N » manque).
2. `BuyTrainerService` au clic (carte « Apprendre X ») : aucun blocage ? (`/tai bloque achats` ; sinon mode sûr).
3. `GetRecipeSchematic` rend-il les compos des recettes Classic de Forever (cases « de base ») ?
4. Le niveau requis du rang (4e retour de `GetTrainerServiceInfo`) est-il rempli pour Compagnon / Expert ?
5. Stock des amis du groupe : non partagé aujourd'hui (seuls leurs métiers le sont) → pas compté.
6. Temps de farm : seulement les zones et niveaux des bêtes (pas de durée : aucune mesure encore).
