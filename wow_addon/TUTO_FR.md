# TreeAI pour WoW Forever — tuto rapide

## 1. Installer

**Automatique (conseillé)** : mets TreeAI Coach à jour (3.8 ou plus) et lance-le. Il trouve ton dossier WoW
Forever et installe / met à jour l'addon tout seul. Ensuite **relance WoW** (un nouvel addon n'apparaît
qu'après un redémarrage du jeu).
- Page **WoW Forever** de TreeAI (Ctrl+7) : état, dossier WoW (bouton *Parcourir* si non trouvé), bouton
  *Installer / mettre à jour l'addon*.

**À la main** : dézippe `TreeAI-addon.zip` dans
`…\World of Warcraft\_forever_\Interface\AddOns\` → tu dois avoir `…\AddOns\TreeAI\TreeAI.toc`.
Relance WoW. Dans l'écran des personnages → *AddOns* → coche **TreeAI Coach** (et « Charger les add-ons
périmés » si besoin).

Tes potes : chacun installe l'addon (même zip) pour la **synchro de groupe**.

## 2. Premiers pas en jeu (tout se fait à la souris)

- **Assistant au 1er lancement** (3 étapes) : ce que fait TreeAI → combien de conseils (**Mode calme /
  Normal / Tout**) + où mettre le panneau (**Auto** = TreeAI scanne ton écran et prend la meilleure place
  libre) → boutons rapides Macros / Touches / Boussole / Aide. Le revoir : options → *Assistant de démarrage*.
- **Le panneau** : onglets Maintenant · Quêtes · Prévisions · Groupe · HV · Infos. Clic sur une ligne =
  l'action, clic droit = masquer (pour 2 h, même après /reload ; revient seulement si ça change vraiment), molette = défiler. Couleurs : gris = info, or = conseil, rouge = urgent.
- **Barre d'outils du panneau** (à droite du titre) : **Tout** (icône livre) (toutes les actions de tous les modules,
  rangées par rubrique, un clic = lancer), **mode édition** (icône clé), boussole, macros, groupe, options, cadenas,
  mini (une ligne), fermer.
- **Mode édition** (clé) : chaque cadre TreeAI (panneau, encadré d'alerte, boussole, suivi, barres…) se
  colore avec son nom → glisse-le (aimanté : grille 10 px, bords, autres cadres), molette = taille, clic droit
  = remettre à sa place auto ; *Verrouiller tout*, *Tout remettre auto*, *Fini*. Gardé par personnage.
- **Placement auto** : au 1er lancement et quand tu changes de résolution / d'échelle d'interface, TreeAI
  place ses cadres dans les zones libres (encadré près de la minimap, boussole en haut au centre sans gêner
  la cible, panneau du côté le plus vide, suivi sous ton suivi de quêtes). Ce que tu as déplacé à la main
  reste où tu l'as mis. Il ne touche JAMAIS aux cadres de Blizzard ni des autres addons.
- **Calme** : une même info vue par plusieurs modules (munitions, sacs pleins, réparation…) = **une seule
  ligne**. L'encadré rouge : au plus **1 toutes les 20 s**, jamais deux fois la même chose, et **rien en
  combat** sauf danger de mort (contrôle rompu, Feindre la mort résisté…) — le reste attend la fin du combat.
- **Options** (Échap → Options → AddOns → TreeAI Coach) : préréglage, profil (**communs au compte** ou
  **propres à ce perso**), mode édition, placement auto, puis une page par module : chaque module se coupe /
  se rallume **sans /reload**.
- Les commandes restent là pour ceux qui aiment : `/tai aide` (la même page « Tout »), `/tai mode calme`,
  `/tai edit`, `/tai placer`, `/tai profil perso`, `/tai bienvenue`.

## 3. Les macros expertes (`/tai macros`)

- `/tai macros` ouvre le panneau : à gauche la liste (vert = installée, orange = modifiée par toi,
  blanc = dispo, gris = « niv. X » pas encore), à droite l'**aperçu exact** de la macro générée pour TON
  personnage et le **« Pourquoi »** (à quoi sert chaque ligne, quels modificateurs).
- Écrites pour ta **spécialisation** (détectée par tes talents, ou bouton « Spé ») et ton **contexte**
  (solo / groupe, auto ou bouton) : tank, soigneur, DPS, montée en niveau. Seuls les sorts que tu connais
  sont utilisés (sorts des classes refaites de Forever compris) ; la macro grandit quand tu en apprends.
- Rien n'est créé sans ton clic (« Installer cette macro », « Tout installer (ma spé) » ou
  `/tai macros installer`), jamais en combat. Une macro modifiée à la main, ou une macro à toi du même
  nom, n'est jamais écrasée (Maj+clic ou `/tai macros forcer` pour le faire exprès).
- Réflexes inclus : survol puis cible (`[@mouseover,harm,nodead][]`), soins survol > cible > toi (Alt =
  toi), modificateurs Maj/Ctrl/Alt pour les variantes, postures / formes / camouflage gérés, contrôle du
  familier ou du démon, /stopcasting pour les interruptions, bijoux « Utiliser » et racial seulement si
  tu les as. Consommables (repas, pierre de soins puis potion, gemme de mana, bandage, munitions,
  poisons) : le meilleur objet de tes sacs.
- Une macro = une touche pressée = une seule action sur le temps de recharge global (règles Blizzard).
  `/tai macros spe|solo|groupe|auto|liste|retirer`.

## 4. Quêtes (`/tai quetes`)

- Ligne **« Étape : … »** dans le panneau : quoi faire maintenant.
- Quêtes **à rendre** par zone, **« finis ici avant de partir »**, progression « 2/10 », quêtes grises à
  abandonner (un clic ouvre la quête, tu abandonnes toi-même), quêtes élites **« à faire à 4 »**.
- **Bouton d'objet de quête** (Maj + glisser pour le déplacer).
- Avec **Questie** : quêtes dispo autour de toi pas encore prises, celles qui s'ouvrent au prochain niveau,
  la suite d'une chaîne, quel monstre lâche ton objet.

## 5. À plusieurs (`/tai groupe`)

Si tout le groupe a l'addon : section **Groupe** (niveau, zone, vie/mana, sacs de chacun) + les 3 meilleurs
conseils : quêtes communes, « partage-la » (bouton *Partager*), « attendez-le avant de rendre », pause
boisson, qui doit looter l'objet de quête, passage marchand, pierre de foyer, donjon prêt…
- `/tai partir 20` : prévient le groupe que tu pars dans 20 min. `/tai pret on|off`.

## 6. Chasseur & avantages (`/tai boost`)

Familier (bonheur, nourriture, PV, points d'apprentissage), munitions, aspects (guépard en voyage), barre du
tir automatique, Marque du chasseur, pièges ; rares/élites à proximité, **meilleure récompense de quête
surlignée**, infobulles **« Amélioration +X % »** / prix marchand vs HV, menace en groupe, XP (monstres /
quêtes avant le niveau, XP/h), conseiller d'apprivoisement (« loup niv 25 → Morsure rang 4 »).

## 7. Hôtel des ventes : achat-revente (`/tai hv`)

Une fenêtre TreeAI s'accroche à droite de l'HV dès que tu l'ouvres (`/tai hv fenetre` sinon).

- **Scan complet** : lit TOUT l'HV d'un coup (Blizzard : 1 fois / 15 min pour tout le compte ; le bouton
  affiche le compte à rebours). La lecture se fait par petites tranches : le jeu ne fige pas. Si
  **Auctionator** vient de faire son scan complet, TreeAI le relit aussi, sans rien redemander.
- **Scan ciblé** : ne recherche que les objets utiles (liste d'achats, tes sacs, tes ventes), au rythme
  maximum autorisé par Blizzard. Parfait entre deux scans complets.
- Ce que tu parcours toi-même dans l'HV est lu au passage. Les prix d'Auctionator servent de secours.
- **Affaires** : objet, prix, valeur du marché, **profit net** (commission ET dépôt déjà retirés), risque
  (+ dépôt perdu à chaque remise en vente si ça ne part pas), temps de vente estimé. Reventes au marchand =
  profit garanti.
- **Vendre** : tes piles qui valent mieux à l'HV qu'au marchand : prix conseillé, quantité, durée, dépôt
  exact, net attendu, prix marchand. Bouton **Poster** = une mise en vente.
- **Mes ventes** : tes enchères sous-cotées, avec le conseil « annuler et reposter » seulement si le gain
  dépasse le dépôt perdu.
- **Bilan** : or total, réserve (ce que tu veux garder + entraînement/réparations selon ton niveau +
  monture), budget de revente, argent immobilisé (en vente + acheté pas encore revendu), plan d'achat
  et profit réalisé (lu dans les courriers de l'HV).
- **Gestion de l'or** : TreeAI répartit ton budget sur les meilleures affaires (profit attendu ÷ risque,
  35 % max du budget sur un même objet) → une liste d'achats prête.
- Gros bouton **Suivant** : chaque clic fait UNE action (acheter une annonce, poster une pile ou annuler une
  enchère) puis passe à la suivante. Rien ne se fait tout seul : pas de boucle, pas de minuterie. Si le prix
  a bougé, l'étape est sautée. Sur Forever, un achat = « vérifier » puis « acheter » (et « confirmer » pour
  les marchandises en pile).
- Réglages : `/tai hv reserve 50` (garder 50 po), `/tai hv monture 90`, `/tai hv budget 20` (plafond/jour),
  `/tai hv neutre` / `faction` si l'HV n'est pas reconnu, `/tai hv bilan`, `/tai hv vendre`.
- **Ça ne marche pas ?** Tape `/tai hv debug` à l'HV et envoie les lignes : API utilisée, limiteur, temps
  avant le prochain scan complet, taille/vitesse du dernier scan, Auctionator, erreurs.

## 8. Tous les modules en bref

Quêtes (étape, à rendre, grises → module Abandon qui explique pourquoi), Suivi (trié par XP/min), Boussole,
Prévisions (munitions / nourriture / sacs / réparation / formation avant que ça manque), Groupe + Missions
(qui a besoin de quoi, prêt ?, rendez-vous), Chasseur + Chasseur pro (familier, munitions, aspects, pièges,
tir auto), Classes (buffs oubliés), Combat (pull, patrouilles, fuyards, interruptions, récap de mort),
Inventaire (sacs, réparation, gris), Montée (formation, XP reposée, talents), Avantages (rares, récompense,
amélioration), Macros + Touches, Confort (nouveautés, nouveaux sorts, /tai perf), Pro, Économie, HV.
Chaque module a sa page d'options et ses actions dans « Tout ».

## 9. Voix et IA (TreeAI)

- La voix **ne parle jamais toute seule** : dis **« Hey coach »** puis ta question (« on fait quoi ? »,
  « quoi revendre ? », « quel sort au niveau 28 ? ») — au micro du PC ou sur Discord.
  L'IA connaît ta classe, ton niveau, ta zone, tes sacs, tes quêtes et ton groupe.
- **Journal de combat** (pour le rapport « Dernier combat » et l'IA) : tape `/combatlog` en jeu (et une fois
  `/console advancedCombatLogging 1` pour avoir la vie). La page WoW de TreeAI indique s'il est actif.
- Les infos de l'addon arrivent dans TreeAI à chaque `/reload` ou déconnexion.

## 10. PvP

Les aides PvP sont **désactivées** par défaut (l'addon t'aide à quêter). `/tai pvp` ou la case
« Aides PvP » dans les options pour les activer.

## 11. Règles

Rien n'est automatisé : pas de bot, pas de touche simulée, pas de lecture de la mémoire du jeu. L'addon
n'utilise que l'API officielle de Blizzard, TreeAI ne lit que les fichiers que le jeu écrit (journal de
combat, sauvegardes de l'addon). Tout ce qui agit (acheter, partager, poser une macro) se fait par TON clic.

## Problème ?

`/tai statut` (état des modules), `/tai debug` (messages détaillés). Si l'addon n'apparaît pas : vérifie le
chemin `Interface\AddOns\TreeAI\TreeAI.toc`, relance WoW, coche « Charger les add-ons périmés ».
