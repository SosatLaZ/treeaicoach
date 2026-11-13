# AH5 (5.1) — l'hôtel des ventes refait pour l'achat-revente en masse

Joueur : chasseur Horde ~40, WoW: Forever 1.60.1 frFR (API `C_AuctionHouse`), Auctionator installé. Ses mots :
« refais entièrement tout sur l'HV, je veux que ce soit parfait … vois ce qui peut être régulé par moi-même sans te
gourer, genre sur des items de merde … rends le truc bcp plus réactif pour que je puisse faire de l'achat-revente en
masse, objectif visé 50 po par jour voire plus ». Plaintes d'avant : conseils parfois faux, potions vendues, gels.

Livré en deux tranches. **Tranche 1 (§1 à §6)** : calculs exacts, affaires « sûr » et « probable », files Acheter /
Poster, panneau collé à l'HV, journal et objectif. **Tranche 2 (§7 à §12)** : « réguler » un petit marché, file
Annuler / reposter, marges d'artisanat, moments de vente, tableau de bord unique, parité Python.

Rien n'est copié d'un autre addon : ce qui suit décrit des idées connues de tous les joueurs d'HV (et la façon dont
des outils comme TradeSkillMaster ou Auctionator les rangent : valeur de marché tirée d'un historique, prix
minimum / normal / maximum d'une mise en vente, taux de vente, « sniper », passes « poster » puis « annuler »), avec
nos mots, nos formules et nos données.

## 1. Comment on gagne de l'or à l'HV (et où on en perd)

| Mécanique | Ce que c'est | Ce que le client permet de savoir |
|---|---|---|
| Commission | À la vente, le jeu garde 5 % du prix (15 % dans un HV neutre). | Exacte après coup : la facture du courrier (`GetInboxInvoiceInfo` : prix, dépôt, commission). Avant : 5 % / 15 % (`floor`). Le taux vu dans les factures est noté (`diag.ah.cutPm`). |
| Dépôt | Payé en postant, rendu si ça se vend, **perdu** si l'enchère expire ou est annulée. Proportionnel au prix marchand × quantité × durée. | `C_AuctionHouse.CalculateCommodityDeposit(itemID, durée, quantité)` et `CalculateItemDeposit(emplacement, durée, quantité)` existent sur Forever (référence du client 1.60.1) : on les lit au moment de poster, et on retient la part (‰ du prix marchand) pour estimer les achats. Sans elles : un **plafond** (5 % du prix marchand par tranche de 2 h, la règle la plus chère connue), jamais un chiffre optimiste. |
| Sous-cote | Le moins cher part en premier : poster 1 pc sous le moins cher. Si tout le monde le fait, le prix glisse vers le plancher du plus pressé. | Les annonces relues en direct (`SendSearchQuery`, non protégée). |
| Plancher marchand | Une annonce dont le rachat est **sous le prix de vente au marchand** : l'acheter et la revendre au marchand rapporte la différence, sans commission ni dépôt ni attente. C'est le seul gain *prouvable*. | Prix marchand : `GetItemInfo` (11e retour). Prix de l'annonce : la recherche. |
| Achat-revente (« flip ») | Acheter les annonces nettement sous le prix habituel, reposter au prix habituel. Risques : le prix « habituel » est faux (peu de preuves), ça ne se vend pas (dépôts perdus à chaque expiration), quelqu'un sous-cote. | Nos scans sur plusieurs jours : médiane par jour, unités disparues entre deux scans (≈ ventes), nombre de vendeurs ; Auctionator en lecture seule pour recouper. |
| Marchandises / objets | `C_AuctionHouse` sépare les *marchandises* (empilables : on achète N unités, le jeu sert les moins chères d'abord, deux étapes : demander le prix puis confirmer) des *objets* (une enchère = un identifiant, rachat par `PlaceBid`). | `GetItemCommodityStatus`. Statut inconnu → pas d'affaire. |
| Taille des piles | Sur ce client une marchandise n'a plus de « pile » à l'HV (prix à l'unité) : l'effet « petites piles plus chères » de l'ancien HV disparaît ; reste : ne pas poster plus que ce qui se vend pendant une mise en vente. | Ventes / jour mesurées. |
| Heures et jours | Plus d'acheteurs le soir et le week-end, plus de vendeurs (prix bas) le dimanche soir / en fin de soirée. | Pas encore mesuré par TreeAI (il faudrait des scans horodatés sur plusieurs semaines) : **non utilisé**, rien n'est supposé. |

## 2. Les mesures, et comment chacune est calculée (toutes en cuivre, entières : `Modules/AHMath.lua`)

| Mesure | Formule | Données |
|---|---|---|
| Commission | `floor(prix × 5 / 100)` (15 en HV neutre) | prix de revente prévu |
| Dépôt | valeur du jeu ; sinon `floor(marchand × n × part apprise / 1000)` ; sinon plafond `floor(marchand × n × 5 × heures / 200)` | client, sinon `GetItemInfo` |
| Chance de vendre pendant une mise en vente | `p = clamp(ventes/jour × heures/24 ÷ (devant moi + n), 5 %, 95 %)` en ‰ ; ventes non mesurées → inconnu | unités disparues entre scans × 0,6, sur ≥ 12 h de mesure |
| Dépôts perdus attendus | `ceil(dépôt × (1000 − p) / p)` (remises en vente jusqu'à la vente) | — |
| Délai de vente | `(devant moi + n) ÷ ventes/jour` jours | — |
| Net « marchand » | `marchand × n − achat` | exact |
| Net « HV » | `revente × n − commission − dépôts perdus attendus − achat` | — |
| Prix de revente prévu | `min(prix habituel prudent, 1 pc sous l'annonce suivante)` ; prix habituel prudent = médiane des médianes journalières × (1 − ½ volatilité) | historique ≤ 14 j |
| Gain minimal voulu | `max(1 pa par clic, 15 % de la mise)` (réglables) | — |
| Plancher de mise en vente | plus petit prix unitaire tel que net ≥ coût + gain minimal (vérifié par recherche exacte) ; coût = prix d'achat (premier entré, premier sorti) ou, pour du butin, ce que le marchand en donnerait | journal |
| Capital | `min(part % × (or + immobilisé) − immobilisé, or − réserve)` ; ≤ 20 % de l'or dans un même objet | `GetMoney`, journal |
| Preuves (« probable ») | ≥ 3 jours d'historique, ≥ 4 scans, ≥ 12 h mesurées, ≥ 1 vente / jour, ≥ 3 vendeurs, volatilité ≤ 30 %, prix lus il y a ≤ 12 h, modèle de liquidité d'accord | scans |
| Gain réalisé | ventes (prix − commission − coût PEPS) + reventes marchand − dépôts des expirées / annulées, par jour | clics confirmés, courrier, marchand |
| Or par heure | gain du jour ÷ temps passé HV ouvert (≥ 5 min) | horloge |

## 3. Trois classes, et ce qu'on en fait

* **sûr** : revente au marchand. Prix d'achat et prix marchand lus dans le jeu → gain exact, aucun frais.
* **probable** : achat-revente avec toutes les preuves du tableau. Le texte dit le gain *probable*, les frais, le délai,
  le nombre de jours de preuves et les ventes par jour.
* **spéculatif** : preuves minces, prix vieux, ventes non mesurées. **Absent des files** (jamais « sûr », jamais
  proposé à l'achat).

Garde-fous vérifiés par des tests de propriétés sur des marchés tirés au hasard (`tests/wow/ah5_scenario.lua`) :
aucune affaire « sûr » ne perd d'argent ; net exact au cuivre ; jamais mes propres enchères (annonces à mon nom ou qui
ressemblent aux miennes écartées ; pour une marchandise on s'arrête *avant* ma propre enchère) ; jamais plus que la
part d'or choisie ; un objet que `T.Bags.Verdict` dit de garder n'est jamais dans la file « Poster ».

## 4. Le travail en masse (aucune automatisation)

* Un clic ou un appui de touche = **au plus une** action protégée (`PlaceBid`, `StartCommoditiesPurchase`,
  `ConfirmCommoditiesPurchase`, `PostCommodity`, `PostItem`), hors combat, HV ouvert, limiteur du jeu libre
  (`IsThrottledMessageSystemReady` / `AUCTION_HOUSE_THROTTLED_SYSTEM_READY`).
* Entre deux appuis, TreeAI relit **en direct** l'annonce en tête de file (recherche, non protégée). Si elle a changé,
  l'affaire est recalculée avec les vraies annonces, ou retirée (« l'annonce a changé »).
* Marchandise : 1er appui = le jeu donne son prix pour N unités ; s'il est ≤ au prix prévu, 2e appui = acheter ; sinon
  annulé. Objet : un appui (l'enchère relue, son identifiant, son prix exact).
* Une action n'est comptée que si le jeu la confirme (événement d'achat / de création d'enchère, ou l'or / les sacs qui
  bougent exactement comme prévu). Pas de réponse en 6 s → « non confirmé », rien n'est écrit au journal.
* « Poster » : prix recalculé à l'instant du clic ; sous le plancher → pas posté (« attends »). Les prix lus avant mon
  dernier achat du même objet ne valent plus : relecture ciblée d'abord.
* Calculs étalés (25 objets ou 5 piles par image), rien HV fermé.

## 5. Preuve avant correctif : le gel

`session_sim.lua --crowd --phases hv` (ouvrir l'HV, scan de ~2 500 annonces, 30 achats, 30 mises en vente, fermer) sur
le code 5.0 : **une image de 21 ms** à la fin de chaque relecture ciblée (l'ancien calcul repassait tout le marché d'un
coup). Avec le bureau : pire image 1,4 ms, 0 travail une fois l'HV fermé.

## 6. Ce qui doit être vérifié dans le vrai client (sonde « Hôtel des ventes » de TreeAI-erreurs.txt)

1. `CalculateCommodityDeposit(itemID, 1|2|3, n)` répond un nombre HV ouvert (sinon : dépôt « estimé » affiché).
2. `PlaceBid(auctionID, rachat)` achète tout de suite ; réponse `AUCTION_HOUSE_PURCHASE_COMPLETED` (sinon l'or qui baisse).
3. `StartCommoditiesPurchase` → `COMMODITY_PRICE_UPDATED(prix unité, total)` ; `ConfirmCommoditiesPurchase` →
   `COMMODITY_PURCHASE_SUCCEEDED` / `_FAILED`. Le jeu ne me vend jamais mes propres unités (on n'en dépend pas).
4. `PostItem(emplacement, durée, n, nil, rachat)` : le rachat est **par objet**. `PostCommodity(emplacement, durée, n, prix unité)`.
   Réponse `AUCTION_HOUSE_AUCTION_CREATED`, ou `AUCTION_HOUSE_POST_WARNING` (confirmation demandée → on ne force pas).
5. Les achats arrivent **par courrier** ; sujets de courrier et facture (`GetInboxInvoiceInfo`) comme sur Classic.
6. `GetItemInfo` : le 11e retour est le prix de vente au marchand de TOUT exemplaire de cet objet.
7. Une touche liée par Bindings.xml compte comme un clic matériel pour ces fonctions.

Les compteurs `diag.ah` du fichier d'erreurs (prix reçus, achats confirmés par événement / par l'or seulement, sans
réponse, créations d'enchères, avertissements, taux de commission vu, part de dépôt apprise) disent ce qui s'est passé.

## 7. « Réguler » un petit marché bon marché (classe « regul »)

L'idée (ce que le joueur appelle « réguler ») : sur un objet bon marché que peu de gens vendent, un vendeur pressé
poste un « mur » d'annonces très sous le prix habituel. Racheter **tout** le mur et reposter au prix habituel rend le
marché à son niveau, et la différence est le gain.

Quand ça marche : peu de vendeurs (personne ne reposte derrière toi), des ventes réelles au prix habituel (mesurées sur
plusieurs jours, pas supposées), un mur peu profond par rapport à ce qui se vend (il faut l'écouler avant que d'autres
vendeurs arrivent), un mur qui ne coûte pas cher (si tout tourne mal, la perte est petite).
Quand ça rate : l'offre est élastique (dès que le prix remonte, d'autres reviennent poster — beaucoup de vendeurs
dans l'historique), le prix habituel est faux (peu de jours de scans), le mur est plus profond que ce qui se lit (au-delà
des 12 annonces lues), ou on n'en rachète qu'une partie (le reste sous-cote ta revente).

Seuils **explicites** (`AHMath.RULES`, réglables : plafond du mur et interrupteur dans les options) :

| Seuil | Valeur | Pourquoi |
|---|---|---|
| vendeurs dans le mur | ≤ 3 (noms lus dans les annonces ; inconnus → rien) | peu de monde pour reposter derrière toi |
| vendeurs vus sur le marché (historique) | ≤ 3 | l'offre n'est pas élastique |
| prix habituel | ≤ 1 po l'unité | « items de merde » : petite mise, petite perte possible |
| le mur | toutes les annonces sous 75 % du prix habituel, **en entier** | une annonce laissée sous-cote ta revente |
| coût du mur | ≤ 3 po (option « Plafond d'un mur ») et ≤ la part d'or choisie | perte bornée |
| ventes | ≥ 2 / jour mesurées (≥ 3 jours, ≥ 4 scans, ≥ 12 h mesurées) | le prix habituel est un prix de VENTE |
| délai | (mes unités déjà en vente + le mur) ÷ ventes/jour ≤ 4 j | écouler avant que d'autres reviennent |
| marge | net ≥ max(1 pa, 30 % de la mise) | le risque se paie (15 % pour un achat-revente prouvé) |
| ma propre vente | aucune sous le prix de revente | sinon je me sous-cote moi-même |

Revente = min(prix habituel prudent, 1 pc sous la première annonce au-dessus du mur). Net = Σ par annonce
(revente × n − commission − dépôts perdus attendus − prix payé), même chance de vente pour tout le lot. La file prend le
mur **entier ou rien** (`AHMath.allocate` : un groupe). Une ligne de risque est toujours affichée : « Risque : un autre
vendeur peut reposter sous ton prix ; au pire, revendu au marchand : perte X » (pire cas = prix marchand × n − mise).
Le « prix en chute » du jour est accepté ici (c'est le mur lui-même qui tire la médiane du jour) ; rien d'autre ne
l'est.

## 8. Annuler / reposter une vente dépassée

Annuler perd le dépôt ; ne pas annuler laisse la vente derrière un concurrent moins cher. Pour une vente (prix p,
n unités, dépôt d), concurrents moins chers a unités, nouveau prix p' (1 pc sous le moins cher, jamais sous le
plancher), nouveau dépôt d', frais d'annulation c (lus par `GetCancelCost` s'ils existent, comptés en plus — prudent) :

* garder = ⌊ P(a, n) × (net(p × n) + d) / 1000 ⌋ (le dépôt revient si ça se vend) ;
* annuler = ⌊ P(a', n) × (net(p' × n) + d') / 1000 ⌋ − d' − c ;
* annuler seulement si annuler − garder ≥ max(1 pa, 5 % du net reposté).

P = chance de vendre pendant une mise en vente (‰, §2). Pas d'annulation sans ventes prouvées, ni si le moins cher est
anormalement bas (< 70 % du prix habituel : on attend qu'il parte), ni sur des prix lus il y a plus de 10 min (relecture
ciblée d'abord). Une annulation = un clic, une seule `CancelAuction`, limiteur du jeu respecté ; comptée au journal quand
le jeu la confirme (`AUCTION_CANCELED`, sinon la vente absente de la liste) ; le courrier « annulée » qui suit n'est pas
recompté. L'objet revient par courrier ; la file « Poster » le remet au bon prix.

## 9. Marges d'artisanat (tes métiers)

Pour chaque recette que tu connais (lue dans ta fenêtre de métier, sinon celles de ton rang) : composants au prix lu
(marchand : prix exact ; HV : coût réel des unités les moins chères à l'instant, `AHMath.buyCost` ; sinon prix habituel
de tes scans, signalé), revente au prix habituel prudent de l'objet fabriqué, commission et dépôts perdus déduits, marge
≥ 15 %. Seulement si l'objet fabriqué a des ventes prouvées (classe « probable »). Affiché dans « Mes métiers » ; pas de
file d'achat de composants (encore à faire, voir §12).

## 10. Moments de vente (jour × heure)

Entre deux scans complets espacés de ≤ 6 h, les unités disparues sont rangées dans un créneau (jour de la semaine ×
tranche de 4 h, au milieu de l'intervalle). Rien n'est dit tant qu'il n'y a pas ≥ 8 créneaux d'au moins 6 h mesurées
sur ≥ 4 jours différents ; un créneau n'est annoncé « meilleur » que s'il vend ≥ 1,2 × la moyenne. Il faut donc
plusieurs semaines de scans réguliers : en attendant, la page le dit (« pas encore assez de scans espacés »).

## 11. Ce que le joueur voit, et l'appli

* Page « HV » = un tableau de bord : objectif du jour, capital libre / immobilisé, tuiles **Acheter (n)**, **Poster
  (n)**, **Annuler (n)**, puis En vente, Mes métiers, Ce qu'il te manque ; la liste choisie ; le résultat par objet ;
  la ligne « Moments de vente ». Les anciennes tuiles (« Vendre tes sacs à l'HV », « Acheter-revendre », « gagné
  cette semaine », « Scanner ») sont retirées.
* Panneau collé à l'HV : Acheter · Poster · Annuler, « Action suivante » (achat, puis annulation, puis mise en vente),
  la touche.
* L'appli (`treeaicoach/wow/ahmath.py`) refait les mêmes calculs, comparés au Lua sur des milliers de cas générés
  (`tests/wow/test_ahmath_parity.py`) ; `auction.py` lit le résumé du bureau (objectif, files, capital).

## 12. Encore à vérifier ou à faire

À vérifier dans le vrai client (compteurs `diag.ah` du fichier d'erreurs) :
* les recherches donnent-elles les noms des vendeurs des autres (`owners`) ? (`sellerNamesKnown` / `sellerNamesMissing`)
  — sans eux, aucune régulation n'est proposée ;
* `CancelAuction(auctionID)` → `AUCTION_CANCELED` (`auctionCanceled`) ; `GetCancelCost` (`cancelCost`) : un coût en plus
  du dépôt, ou le dépôt lui-même ? (compté en plus : prudent) ; `CanCancelAuction` ;
* `QueryOwnedAuctions` relit la liste de mes ventes (→ `OWNED_AUCTIONS_UPDATED`) et passe par le limiteur ;
* le courrier « Vente aux enchères annulée : X (n) » porte la quantité.

Pas fait : file d'achat des composants d'une recette rentable ; relances automatiques de scans aux bons créneaux
(le joueur reste maître de ses scans) ; marges des métiers de récolte (vendre brut ou transformé).
