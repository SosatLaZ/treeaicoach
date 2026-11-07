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

## 2. Premiers pas en jeu

- Un petit panneau TreeAI s'affiche (déplaçable). `/tai` = l'aide de toutes les commandes.
- `/tai options` : tout se règle là (chaque module et chaque aide se coupe).
- `/tai cacher` / `/tai afficher` / `/tai verrou` (bloque le panneau) / `/tai reset` (le remet au centre).

## 3. Les macros intelligentes (`/tai macros`)

- `/tai macros installer` : crée les macros de ta classe (hors combat). `/tai macros liste` les montre ;
  clique une macro dans le panneau pour la prendre et pose-la sur ta barre.
- Elles s'améliorent seules quand tu apprends un sort ; nourriture / eau / potion / bandage / munitions se
  mettent à jour avec le meilleur objet de tes sacs ; « TAI Nourrir » nourrit ton familier avec la bonne
  nourriture. Une macro que tu modifies à la main n'est jamais écrasée.
- Une macro = une touche pressée = une action (règles Blizzard).

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

- Ouvre l'HV et clique **Scanner** : l'addon garde 14 jours de prix (par royaume et faction) et calcule la
  **valeur du marché**, la **commission** (5 % HV de faction, 15 % HV neutre), le **dépôt**, le **profit
  net**, le **retour sur investissement** et le **prix d'équilibre**.
- **Bonnes affaires** : objets sous le marché (profit après frais et risque), objets moins chers que leur
  prix chez le marchand (profit garanti), alerte marchés trop minces. Bouton **Acheter** = UNE annonce par
  clic, prix revérifié avant achat.
- **Vendre** : prix conseillé (sous-cotation raisonnable), dépôt, « mieux au marchand » si c'est plus
  rentable ; tes enchères sous-cotées sont signalées (bouton **Annuler**).
- **Suivi** : budget, achats/ventes lus dans les courriers de l'HV, profit réalisé par objet et par jour.
- `/tai hv neutre` / `/tai hv faction` si l'HV n'est pas reconnu. « Hey coach, quoi revendre ? » marche aussi.

## 8. Voix et IA (TreeAI)

- La voix **ne parle jamais toute seule** : dis **« Hey coach »** puis ta question (« on fait quoi ? »,
  « quoi revendre ? », « quel sort au niveau 28 ? ») — au micro du PC ou sur Discord.
  L'IA connaît ta classe, ton niveau, ta zone, tes sacs, tes quêtes et ton groupe.
- **Journal de combat** (pour le rapport « Dernier combat » et l'IA) : tape `/combatlog` en jeu (et une fois
  `/console advancedCombatLogging 1` pour avoir la vie). La page WoW de TreeAI indique s'il est actif.
- Les infos de l'addon arrivent dans TreeAI à chaque `/reload` ou déconnexion.

## 9. PvP

Les aides PvP sont **désactivées** par défaut (l'addon t'aide à quêter). `/tai pvp` ou la case
« Aides PvP » dans les options pour les activer.

## 10. Règles

Rien n'est automatisé : pas de bot, pas de touche simulée, pas de lecture de la mémoire du jeu. L'addon
n'utilise que l'API officielle de Blizzard, TreeAI ne lit que les fichiers que le jeu écrit (journal de
combat, sauvegardes de l'addon). Tout ce qui agit (acheter, partager, poser une macro) se fait par TON clic.

## Problème ?

`/tai statut` (état des modules), `/tai debug` (messages détaillés). Si l'addon n'apparaît pas : vérifie le
chemin `Interface\AddOns\TreeAI\TreeAI.toc`, relance WoW, coche « Charger les add-ons périmés ».
