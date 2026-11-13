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
T.Bags.Lists() / SellList() / GreyItems() / Cheapest() / MissingText() / Sort() / Mark(id, "keep"|"sell"|"bank"|nil)
```
Un gardien dont le nom contient « gear » / « quip » remplace le pont Équipement (Gear.CanUse / UpgradePct) ; « prof » /
« métier » / « cityrun » remplace le pont Métiers (CityRun.Verdict, consulté seulement chez un PNJ / en ville / en
courses : là où PROF calcule déjà ses plans). **Un gardien doit être rapide** (appelé pour chaque objet des sacs, 8 par
image) : pas de plan recalculé dedans.

## Perf (mesurable)
- Aucun travail sur BAG_UPDATE : le relevé (`Snapshot`) et les avis sont refaits à la demande, clé `API.BagGen()`.
- Un avis par objet, gardé tant que son compte (ou celui de sa catégorie de consommable) ne change pas ; durée de vie
  45 s (les gardiens externes changent d'avis sans prévenir).
- Butin sacs fermés, de la place : 0 avis calculé (scénario §8). En combat : rien (chaque tir = un BAG_UPDATE).
- Pastilles : seules les cases changées sont retouchées (1 bouton pour 1 objet ramassé, scénario §7).
- Gros recalcul (ouverture des sacs, marchand) : 8 objets par image (`T:Spread`).

## Pas fait / limites connues
- Taille des sacs en inventaire (un sac plus petit que les tiens n'est pas « en trop » : gardé).
- Poisons de voleur, élixirs, parchemins : pas de catégorie → gardés (« je ne sais pas s'il te sert »).
- Marquer « garder » depuis le sac lui-même (clic modifié sur le bouton du jeu) : non — carte du marchand, page Sacs,
  `/tai garde [objet]`.
- Retrait de banque : une pile par clic (dépôt : jusqu'à 8 piles par clic).
