# AH5 (5.1) — l'hôtel des ventes refait pour l'achat-revente en masse

Joueur : chasseur Horde ~40, WoW: Forever 1.60.1 frFR (API `C_AuctionHouse`), Auctionator installé. Ses mots :
« refais entièrement tout sur l'HV, je veux que ce soit parfait … vois ce qui peut être régulé par moi-même sans te
gourer, genre sur des items de merde … rends le truc bcp plus réactif pour que je puisse faire de l'achat-revente en
masse, objectif visé 50 po par jour voire plus ». Plaintes d'avant : conseils parfois faux, potions vendues, gels.

Livré en deux tranches. **Tranche 1 (ce document, §1 à §6)** : calculs exacts, affaires « sûr » et « probable », files
Acheter / Poster, panneau collé à l'HV, journal et objectif. **Tranche 2 (§7, à faire)** : « réguler » un petit marché,
file Annuler / reposter, marges d'artisanat, tableau de bord, parité Python.

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

## 7. Tranche 2 (pas encore fait)

« Réguler » un petit marché bon marché (acheter tout le mur bas, reposter au prix normal : seulement avec peu de
vendeurs, des ventes prouvées, un mur plafonné, et le risque dit en une ligne) ; file « Annuler / reposter » ; marges
d'artisanat liées à ses métiers ; tableau de bord de la page HV et retrait des anciennes tuiles ; fonctions Python
miroir testées contre le Lua ; habitudes par heure / jour de la semaine.
