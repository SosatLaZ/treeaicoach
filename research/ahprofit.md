# AHPROFIT (4.1) — l'HV revu pour le bénéfice

Joueur : chasseur niv. 30, groupe de 4, WoW: Forever frFR, serveur à faible volume. Plaintes : captures d'HV
« normal ? », « c'est débile », veut « un mode achat revente automatique fast », puis « revois tout pour l'HV pour
faire vla le bénef ».
Règle du jeu : acheter / poster / annuler = actions protégées, **un clic matériel = une action**, jamais de boucle,
jamais en combat ; scanner via l'API de l'HV est permis quand l'HV est ouvert (en respectant les limiteurs).
Pas de `research/ah2.md` dans le dépôt : les seuils de liquidité sont décrits dans `Auction.lua` (bloc `LIQ`) et ici.

## 1. Audit (mock Forever : prix en cuivre, dépôt, commission 5 %, piles, serveur calme)

| Zone | Ce qui n'allait pas (inutile / faux / confus) | Correction 4.1 |
|---|---|---|
| Chat | Chaque scan, achat, mise en vente, annulation et fin de « rafale » écrivait dans le chat (« Scan complet lu : … », « Acheté : … », « Mis en vente : … »). | `say()` écrit dans la ligne d'état du panneau HV (`M.msg`, `M.msgLog`, `A.Said()` pour les tests) ; seules les commandes tapées (`/tai hv …`) répondent dans le chat. Le scénario vérifie : zéro ligne de l'HV dans le chat. |
| Seuils | Défauts 20 pa de profit minimal et −20 % sous le marché : au niveau 30 sur un serveur calme, presque rien ne passait jamais (« Aucun achat-revente sûr » en permanence), alors qu'un achat-revente à +4 pa sur 20 pa misés est excellent. Les reventes au marchand passaient dès +1 pc. | Une seule règle partout : **bénéfice net ≥ max(1 pa, 15 % de la mise)** après commission et dépôt × P(invendu) (`F.minNet` / `min_net`), plus la preuve de ventes. Pré-filtre −15 %. Migration une fois des anciens 20 pa / 20 %. Réglable (pa minimum, %). |
| Volume | « 0 vendu » mesuré sur 20 min de scans comptait comme une preuve qu'un objet ne se vend pas (proba de vente 5 %). | Volume inconnu tant que les scans ne couvrent pas ≥ 12 h (`statsFromPoints`, `daily_volume`). |
| Quantité | Le contrôle « pile ≤ ce qui se vend en 3 j » était fait annonce par annonce : le plan pouvait acheter 3 piles du même objet et dépasser la demande. | `F.allocate` plafonne les unités par objet à la demande observée (`maxQty`). La page groupe par objet : « ×40, achète ≤ 62 pc/u ». |
| Vendre (sacs) | « Mieux à l'HV » comparait le net SI vendu au prix du marchand : un objet qui ne se vend jamais (laine du mock, 0 vente en 4 j) était conseillé à l'HV, dépôt perdu à chaque expiration. | Espérance honnête `ev = p × net + (1 − p) × (marchand − dépôt)` ; « vends à l'HV » seulement si `ev` bat le marchand. |
| Mise en vente | Toujours la pile entière, quel que soit le rythme des ventes (20 postés quand il s'en vend 3 / jour → expire, dépôt perdu). Sous-cotation figée à 1 pc. | Quantité = ventes pendant UNE mise en vente − déjà en vente (`F.postQty`), taille de pile habituelle des concurrents ; sous-cotation réglable (0 = 1 pc, sinon N %). |
| Combat | La touche « action HV suivante » pouvait agir HV ouvert en combat. | Refus en combat (Buy / Post / Cancel / touche) ; le mock signale toute action d'HV en combat. |
| Fenêtre HV | 640×500, 4 onglets, **6 boutons d'action dont 3 faisaient la même chose** (« Suivant », « Rafale : action suivante », plus les 3 spécifiques), jargon (« rafale », « stop-loss », « Conf. % »), bilan brut de 20 lignes. | Un panneau compact collé à l'HV : Scanner · Prix de mes objets ; **Acheter (n)** · **Poster (n)** · **Annuler les dépassés (n)** avec, à côté, ce que fera le prochain clic (objet, prix, prix max) ; gros bouton **Action suivante [touche]** + « Lier à une touche » ; ligne **Session** (achats, mises en vente, bénéfice prévu) ; ligne d'état ; liste À acheter / À poster / Mes ventes (colonne Risque faible/moyen/élevé au lieu de « Conf. »). |
| Page HV de la fenêtre | Les lignes de l'onglet du panneau + « Ouvrir la fenêtre HV » : aucun chiffre, rien d'utile HV fermé. | 3 chiffres (bénéfice possible maintenant · en vente à l'HV · gagné cette semaine), 4 gros boutons (Scanner · Acheter-revendre · Vendre mes sacs · Mes métiers), la liste choisie, « Mes ventes en cours ». États vides qui disent quoi faire (« Ouvre l'HV et clique Scanner… »). |
| Marchand → HV | Absent (seulement HV → marchand : annonce sous le prix de revente au marchand). | `F.vendorToAH` : composants vendus par les marchands (données du client) et objets vus chez les marchands (prix, quantité limitée, recettes) qui se revendent à l'HV avec preuve de ventes ; quantité ≤ demande. |
| Métiers | Aucun lien HV ↔ métiers. | `M.CraftProfits` : recettes connues (sinon de ton rang) de tes métiers ; coût des composants (`ProfLevel.PriceOf` : marchand ou HV) contre prix de vente mesuré ; bouton « Mon plan de métier ». |
| Infobulle | « marchand 30 pa · HV ~1 po 20, se vend ~6/jour → Mettre à l'HV ». | « HV ≈ 1 po 20 pa (vendu ~6/j) · marchand 30 pa → vends à l'HV » (l'HV d'abord, seulement si le marché est fiable). |
| Annonces dans « Maintenant » | Suggestions HV (affaires, ventes dépassées) même loin de l'HV. | Seulement HV ouvert. |
| Libellés app | « clique Scan complet » partout alors que le bouton s'appelle Scanner. | « Scanner » (app + addon). |

Ce qui était bon et gardé : modèle de liquidité (ventes mesurées entre scans, vendeurs, volatilité, Auctionator
d'accord), revérification du prix avant chaque achat (recherche, puis achat seulement si ≤ prix prévu ; marchandise :
annulée si le prix monte), annulation seulement si le dépôt perdu vaut le coup, lecture du scan complet d'un autre
addon, traitement par tranches ≤ 4 ms, limiteur Blizzard respecté.

## 2. Moteur de bénéfice (chiffres honnêtes)

* Valeur de marché = médiane des médianes journalières des 5 annonces les moins chères (nos scans, 14 j).
* Volume = unités disparues entre deux scans complets (moins les remises en vente moins chères) × 0,6 (part
  vendue), ramené à la journée ; inconnu sous 12 h de scans.
* Revente = marché × (1 − ½ volatilité) ; net = revente − commission 5 % (15 % neutre) − dépôt × P(invendu).
* Achat-revente gardé seulement si : liquidité ok (≥ 2–3 jours de scans, ≥ 4 scans, ≥ 12 h mesurées, ≥ 1 vente /
  jour, prix stable ±30 %, ≥ 3 vendeurs), net ≥ max(1 pa, 15 %), revente en ≤ 3 j ; quantité ≤ ventes de 3 j −
  stock ; ≤ 20 % de ton or par objet ; réserve (entraînement, réparations, monture) jamais touchée.
* Mêmes formules dans `treeaicoach/wow/auction.py` (`min_net`, `undercut`, `suggest_post`/`ev`, `post_quantity`,
  `vendor_to_ah`, `craft_profit`) ; le scénario Lua vérifie les mêmes nombres.

## 3. À l'HV : un clic = une action

`Acheter` revérifie le prix (recherche) puis achète l'annonce suivante du plan seulement si elle coûte ≤ le prix
prévu ; sinon elle est marquée « disparu » et rien n'est acheté. `Poster` met en vente la pile suivante au prix
calculé (re-calculé juste avant ; ignorée si le prix a chuté). `Annuler les dépassés` annule une vente dépassée
rentable. `Action suivante` (bouton ou touche « TreeAI : action HV suivante ») fait la prochaine de ces actions :
chaque appui est un événement matériel, au plus un appel protégé, jamais de boucle ni de minuterie qui agit.

## 4. Vérifications en jeu

1. HV fermé : fenêtre TreeAI → HV : 3 chiffres, 4 boutons, « Ouvre l'HV et clique Scanner ».
2. À l'HV : le panneau TreeAI est collé à droite de l'HV, rien dans le chat en scannant / achetant / postant.
3. Scanner 2–3 fois sur deux jours, puis « Acheter-revendre » : chaque ligne dit achat max, revente, net, risque,
   ventes / jour, délai.
4. « Acheter (n) » : un clic = un achat (2 clics pour une marchandise : vérifier puis confirmer) ; si un autre
   joueur a acheté l'annonce entre-temps, rien n'est acheté.
5. Lier « Action suivante » à une touche, appuyer plusieurs fois : une action par appui ; en combat, rien.
6. Infobulle d'un objet scanné : « HV ≈ … (vendu ~N/j) · marchand … → vends à l'HV / au marchand ».
