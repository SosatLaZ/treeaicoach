# BAGS (5.0) — l'assistant d'inventaire : ce qu'on a appris, ce qu'on a décidé

## L'incident (preuve avant correctif)
« La vente rapide a vendu mes meilleures potions de soins. » Rejoué dans `tests/wow/bags_scenario.lua` (§1) :
- `Potion de soins` (929) est un **composant** de la recette de Couture « Gants de vraie foi » (Data/Recipes) ; les
  potions de mana servent aux « Renforts d'armure … mystiques » (Travail du cuir).
- Courses en ville (`CR.VerdictOf`) répondait donc, pour un chasseur sans Couture : « Pas besoin : sert en Couture (pas
  ton métier) », `extra = tout` → `CR.SellStacks` les vendait d'un clic. 4.3.5 a seulement exclu la classe « Consommable ».
- La bonne règle n'est pas « exclure une classe » mais « **qui a besoin de cet objet ?** » : un seul avis par objet,
  construit par des *gardiens* ; un objet réclamé n'est jamais proposé ; sans avis « en trop » explicite, on garde.

## Idées prises aux autres addons (lus pour comprendre, rien de copié)
- **Scrap / Peddler / Dejunk** : la camelote = qualité grise + listes du joueur (inclure / exclure) ; filtres optionnels
  (équipement lié non portable, consommables trop bas) ; **exclusions qui gagnent toujours** (objets d'un set, non
  liés, remboursables) ; vente plafonnée à **12 objets** pour que tout reste rachetable (« safe mode ») ; vente par
  petits paquets qui attendent le jeu ; détruire = jamais sans un geste par objet (protégé) ; une **pièce** sur les
  boutons de sac. → repris : marques « garder / vendre / banque » (`/tai garde`, clic droit sur la carte), 12 objets non
  gris par clic, gris d'abord (ce qui a de la valeur reste dans le rachat), paquets de 6 relus un par un, pièce native.
- **Baganator / Bagnon** : classement par classe / sous-classe d'objet et par *usage* (consommable, quête, compo,
  équipement) ; API publiques pour brancher un avis : plugin « camelote » (`RegisterJunkPlugin`) et *widget de coin*
  (`RegisterCornerWidget`), `hooksecurefunc` sur la classe de bouton de Bagnon. → repris : notre avis nourrit leur
  pièce de camelote (Baganator) et un coin HV / banque ; chez Blizzard, une texture posée sur le bouton du jeu
  (`EnumerateValidItems` du sac combiné, `ContainerFrameNItemM` sinon), retouchée seulement si elle change.
- **Buffet / macros de consommables** : « le meilleur utilisable de ton niveau » par catégorie. → la catégorie est
  l'unité de décision : la meilleure possédée n'est jamais en trop ; une moins bonne ne l'est que si les meilleures
  couvrent déjà le stock.

## Le contrat (pour GEAR, PROF et tout module)
```lua
T.Bags.Verdict(itemID, bag, slot) → { action = "keep"|"sell"|"ah"|"bank"|"use"|"destroy?", why, keep, surplus, value,
                                      have, name, vendor, ah, reasons, keeper, claimed, sellN }
T.Bags.RegisterKeeper(nom, fn)    -- fn(itemID, infos, combien_en_sac, sac, case)
   n > 0, "pourquoi"[, "bank"|"use"]  → garde n (math.huge = tout)
   0, "pourquoi"                      → « je connais cet objet : EN TROP pour moi » (avis explicite : vente possible)
   nil                                → pas d'avis (sans avis « en trop » de quelqu'un, l'objet est gardé)
   4e retour vrai                     → « pas d'avis sur le reste » (réclame 6 sur 20 sans dire que 14 sont en trop)
   erreur                             → réclame tout (dans le doute, on ne vend pas)
T.Bags.Invalidate([itemID])       -- ton avis a changé (plan de métier, équipement) : avis refaits à la demande
T.Bags.Shopping()                 -- ce qu'il manque : { {cat, id, n, have, want, label, name, ah, npc, src, text} }
T.Bags.Lists() / SellList() / GreyItems() / Cheapest() / MissingText() / Mark(id, "keep"|"sell"|"bank"|nil)
T.Bags.Summary()                  -- pour un bouton / une barre : { ready, sellN, sellValue, missing, free, total, merchant, bank }
T.Bags.SellClick() / BankClick() / Sort() / RepairNow(true)   -- à appeler DANS un clic (T:UserAction) ; rien hors clic
```
(le bloc ci-dessus : API publique complète ; `Peek(id, bag, slot)` = l'avis déjà prêt, sans calcul, pour un rappel
appelé en rafale.)

Un gardien dont le nom contient « gear » / « quip » remplace le pont Équipement (Gear.CanUse / UpgradePct ; en groupe,
ce pont ne dit jamais « en trop » d'une pièce échangeable : seul GEAR sait si un ami en a besoin). Un nom contenant
« prof » / « métier » / « cityrun » remplace le pont Métiers (une marchandise qui sert à une recette d'un de TES métiers
est gardée ; le « en trop » précis — plan, quantités — est l'affaire du gardien de PROF). **Un gardien doit être
rapide** (appelé pour chaque objet des sacs, 6 par image) : pas de plan recalculé dedans, répondre depuis un cache et
appeler `T.Bags.Invalidate()` quand le plan change.

Sans avis de personne : gardé, pas de pastille, pas de ligne d'infobulle (« inconnu → rien »). Exception : une
marchandise (classe 7) que personne ne réclame et dont le prix d'HV est CONNU et nettement meilleur que le marchand est
proposée « HV » (jamais « marchand » : la vente d'un clic exige un avis « en trop » explicite).

À faire côté autres agents (hors de mes fichiers) :
- PROF : la carte « PNJ » de ProfLevel (`C.SellStacks` / « Vendre (n) » / « Réparer ») double la carte du marchand de
  Sacs ; elle devrait disparaître au profit de `T.Bags` (ou appeler `T.Bags.SellClick`). `CR.SellStacks` / `ExtraList`
  doivent passer par `T.Bags.Verdict` (c'est le chemin de l'incident). Inscrire un gardien « prof ».
- GEAR : inscrire un gardien « gear » (améliorations pour moi / le groupe ; `0, "moins bien que …"` pour le reste).
  ItemInfo : le mot de la fin de la ligne « Valeur » se tait déjà quand Sacs a une ligne (petit ajout gardé, voir rapport).
- LOOK : la page « Sacs » est rangée sous « HV » (onglets « Sacs | Hôtel des ventes » en tête des deux pages, la
  navigation garde ses 11 catégories). Un bouton « Sacs » / « Vendre » / « Ranger » de la barre peut lire
  `T.Bags.Summary()` et appeler `T.Bags.OpenPage()`, `SellClick()`, `Sort()`.
- STABLE : `T:OnBags(owner, fn)` est utilisé s'il existe (sinon notre propre regroupement de BAG_UPDATE_DELAYED).
  Le mock ne sait pas déposer à la banque (UseContainerItem sac → banque) : simulé dans le scénario.

## Perf (mesurable)
- Aucun travail sur BAG_UPDATE : le relevé (`Snapshot`) et les avis sont refaits à la demande, clé `API.BagGen()`.
- Un avis par objet, gardé tant que son compte (ou celui de sa catégorie de consommable) ne change pas ; durée de vie
  45 s (les gardiens externes changent d'avis sans prévenir).
- Butin sacs fermés, de la place : 0 avis calculé (scénario §8). En combat : rien (chaque tir = un BAG_UPDATE).
- Pastilles : seules les cases changées sont retouchées (1 bouton pour 1 objet ramassé, scénario §7).
- Gros recalcul (ouverture des sacs, marchand) : 6 objets par image (`T:Spread`), la suite dans sa propre image.
- Journal de quêtes (chaque monstre tué) : seuls les avis dont la réponse « quête » change sont refaits.
- Mesuré (mock, 57 objets, coût de Bags.lua seul) : relevé des sacs ≈ 16 kinstr (0,3 ms), un avis à froid ≈ 1,6 kinstr,
  pire image à l'ouverture des sacs ≈ 25 kinstr (0,5 ms), butin sacs fermés ≈ 0,5 kinstr, au repos 0.
- Simulation 60 min « sûr » (chasseur, Forever) : Bags < 1 kinstr/min ; pire image 147 kinstr (base : 149).

## Pas fait / limites connues
- Taille des sacs en inventaire (un sac plus petit que les tiens n'est pas « en trop » : gardé).
- Poisons de voleur, élixirs, parchemins : pas de catégorie → gardés (« je ne sais pas s'il te sert »).
- Marquer « garder » depuis le sac lui-même (clic modifié sur le bouton du jeu) : non — carte du marchand, page Sacs,
  `/tai garde [objet]`.
- Retrait de banque : une pile par clic (dépôt : jusqu'à 8 piles par clic).

---

# BAGS 5.1 — « sans te gourer sur ce dont j'ai besoin, faut tout prédire »

Règle : **« vends » n'est dit que si tout besoin futur est écarté par des données**. Dans le doute : gardé, en disant ce
qui n'est pas su. Un doute n'est jamais dans le clic unique.

## Les prédicteurs (un par façon d'avoir besoin d'un objet) — `Modules/Bags.lua` + `Modules/BagsPredict.lua`
| Prédicteur | Source (jamais inventée) | Ne peut pas savoir |
|---|---|---|
| tes choix | marques « toujours garder / vendre / banque », rachats appris (`char.marks`, `char.learned`) | — : ton choix gagne |
| quêtes du journal | journal du jeu + Surbrillance : nombre demandé exact, objets fournis, objet qui lance une quête | — |
| quêtes à venir (veto) | base de Questie **lue en jeu** : objet → quêtes ; prenable par TOI (pas rendue, race / faction, classe, métier, pas exclue), de ton niveau à +8 → garde ; au-delà → banque ; répétables ; objets qui lancent une quête | le **nombre** demandé (absent de la base → tout est gardé) ; rien sans Questie |
| consommables | Data/Consumables : meilleur palier utilisable, stock cible par classe ; palier supérieur : ≤ +5 niveaux gardé, au-delà banque | élixirs / parchemins : gardés sans stock cible |
| munitions | arme à distance équipée (jeu) | une arme rangée à la banque |
| familier | régime / niveau du familier (jeu, sinon dernier régime vu) + Data/PetFood | un familier jamais sorti |
| composants de classe | liste fixe | — |
| outils, clés, sacs, monture, mascotte, pierre de foyer, coffres verrouillés | classe d'objet du jeu + listes | — |
| équipement | gardien Gear (stats lues, spé), sets, « porté récemment », portable d'ici 10 niveaux, ami du groupe (GroupGear → « Donne ») | stats pas encore chargées (alors gardé) |
| recettes | Data/Recipes (objet-recette → métier, compétence), infobulle « déjà connue » | recette hors des tables |
| métiers | Data/Recipes (toutes les recettes de tous les métiers) ; le **compte** vient du plan de PROF | ce que tu fabriqueras hors du plan |
| ami du groupe (veto) | métiers partagés par les amis qui ont TreeAI | un ami sans TreeAI |

Quantités : besoin = somme des prédicteurs (raison du plus grand), comparé à sacs + banque (dernier relevé fait banque
ouverte, par personnage) quand le besoin est un besoin total (quête, plan de métier). Les autres personnages ne sont
pas comptés (inconnus).

## Questie : pourquoi pas « relire toute la base au premier marchand »
Il faut « objet → quêtes qui le demandent ». La base de Questie est rangée par quête : la retourner = lire les
objectifs de **chaque** quête (2 lectures × ~5 000 quêtes ≈ 0,2 s de travail). Découpé, c'est invisible en jeu, mais
ça ne tient pas dans le budget de la simulation « foule » (phase butin : 113 kinstr / s, mesuré 101). Donc :
1. Questie porte déjà ce lien par objet (`relatedQuests`, `startQuest`) : **une lecture par objet dont la vente est
   envisagée** — mais on ne s'y fie qu'une fois **vérifié sur de vraies quêtes** (celles du journal, puis 16 quêtes
   prises dans la base : il faut retrouver ≥ 3 objectifs, sans un seul manque) ;
2. sinon (« non vérifié ») : tout ce qui n'est pas gris est **« à confirmer »** et la carte du marchand offre
   « Vérifier mes quêtes à venir (une fois) » : TON clic lance la relecture complète (20 quêtes par image, jamais en
   combat), gardée pour les sessions suivantes (`db.qidx`, tant que la base de Questie est la même) ;
3. sans Questie : « à confirmer », en le disant (« quêtes à venir inconnues (Questie absent) »).

## Filets
- `L.sell` = ce qu'UN clic peut vendre ; `L.confirm` = à part (un doute, ou une pile ≥ option « bigValue », 1 po par
  défaut) : une ligne par objet sur la carte, **son** clic. Un doute = avis « garde » pour le reste de l'addon
  (`v.confirm` porte la vente possible) → aucun autre module ne peut le vendre par erreur.
- Rappel tant que le marchand est ouvert : ce qui a été vendu + « Annuler (Rachat) ». Un rachat (l'objet revient,
  de l'or est dépensé, l'onglet de rachat s'est vidé) → « toujours garder » pour de bon.
- « toujours vendre » gagne toujours ; si un besoin le réclamait : dit, et clic à part.

## Preuve
`tests/wow/bags_audit_scenario.lua` (par classe) et `tests/wow/bags_replay_scenario.lua` : voir leurs en-têtes ; les
deux savent échouer (prédicteurs cassés exprès → violations / regrets).

## Perf
Avis gardés **par cause** (`v.deps` : level, prof, quest, equip, pet, roster, price, bank) ; durée de vie de 45 s
seulement pour les avis où un gardien d'un autre module a répondu. Un seul passage `Warm` à la fois (les demandes
simultanées s'y ajoutent). Index des recettes : 12 morceaux (un par ligne de métier), une fois par session.
