# TreeAI pour WoW Forever — démarrage rapide (5.x)

TreeAI conseille, tu joues : rien ne part sans ton clic (règles de Blizzard), rien en combat, rien dans le chat.

## Installer

- **Automatique** : TreeAI Coach, page **WoW Forever** → **Installer l'addon**, puis relance le jeu (un nouvel addon
  n'apparaît qu'après un redémarrage de WoW, pas après un /reload).
- **À la main** : supprime l'ancien dossier `…\AddOns\TreeAI`, dézippe `TreeAI-addon.zip` dans
  `…\World of Warcraft\_forever_\Interface\AddOns\` (tu dois avoir `…\AddOns\TreeAI\TreeAI.toc`), relance le jeu,
  coche **TreeAI Coach** dans la liste des AddOns.

## La bande d'actions rapides

La bande de boutons TreeAI (déplaçable), un clic par bouton :

| Bouton | Ce qu'il fait |
|---|---|
| Guidage quêtes | allume ou coupe TOUT le guidage des quêtes (trajet, boussole, repères) |
| Rendre | le trajet passe par les PNJ qui attendent tes quêtes finies, au plus court |
| Prendre | le trajet va chercher les meilleures quêtes près de toi |
| Suivant | l'étape est faite (ou tu la sautes) : la suivante |
| Zone | tout faire dans ta zone, et les zones conseillées pour ton niveau |
| Vite | une seule consigne « fais ça maintenant », pour le plus d'XP par heure |
| Sacs · Équipement · Métiers | les pages du même nom |
| Groupe | les quêtes à faire ensemble ; seul : rejoindre un groupe |
| TreeAI | la fenêtre TreeAI (toutes les pages, la recherche, les réglages) |

Déplacer la bande : `/tai edit` (glisser, molette = taille). La remettre en place : `/tai interface reset`.
La **Visite guidée** (`/tai visite`) montre où cliquer ; elle se lance seule la première fois.

## Le guidage des quêtes

Un seul interrupteur : le bouton « Guidage quêtes » de la bande, ou `/tai guide on` / `/tai guide off`
(`/tai guide` seul dit s'il est allumé, sans rien changer). Coupé, TreeAI ne dit plus où aller ; le reste (sacs,
équipement, métiers, familier) continue. Tu préfères la flèche de Suivi Forever ? `/tai guidage forever` : jamais
deux flèches à la fois. Le suivi de quêtes du jeu reste le tien : TreeAI ne le cache ni ne le remplace.

## Sacs, équipement, métiers

- **Sacs** (`/tai sacs`) : ce qui est à vendre, à garder, à mettre à l'HV ou à la banque. Ce qu'un métier ou une
  amélioration attend n'est pas proposé à la vente.
- **Équipement** (`/tai equipement`) : ce que tu peux mettre de mieux tout de suite, les prochaines pièces à viser.
  TreeAI lit ta spécialisation ; si elle est fausse, `/tai spe` la corrige.
- **Métiers** (`/tai metiers`) : le plan pour monter ton métier, étape par étape, et où acheter.

## Les macros

Fenêtre TreeAI → **Macros**, hors combat. Le bouton **« Tutoriel : quoi poser, sur quelle touche »** dit, pour chaque
macro : ce qu'elle fait, ce qui est automatique, où la poser sur ta barre, la touche conseillée. Pour un chasseur
neuf, 5 macros d'office : Tir (1), Familier (V), Pièges (E), Aspects (X), Feindre (Maj+E). Une macro que tu as
modifiée n'est jamais écrasée ; une macro qui n'a rien à faire (pas de monture, pas de potion dans le sac) n'est pas
créée. Une touche = une action, comme le veut Blizzard.

## Ce qui tourne d'office

Profil **Simple** (d'office) : quêtes, trajet, boussole, groupe, sacs, équipement, métiers, macros, talents, et pour
un chasseur l'aide au familier (mort, absent, mécontent, points de dressage) et aux munitions. Le reste (HV,
statistiques mesurées, confort automatique…) s'allume une fonction à la fois : fenêtre TreeAI → **Fonctions**, ou
`/tai profil complet` pour tout (`/tai profil simple` pour revenir). Accepter / rendre les quêtes tout seul n'est
jamais allumé sans toi : `/tai quetesauto on` (après avoir allumé « Confort »).

## Commandes utiles

`/tai aide` (la liste) · `/tai fenetre` · `/tai guide` · `/tai zone` · `/tai vite` · `/tai sacs` ·
`/tai equipement` · `/tai spe` · `/tai metiers` · `/tai macros` · `/tai groupe` · `/tai talents` · `/tai profil` ·
`/tai perf` · `/tai pics` · `/tai erreurs`. Une commande sans `on` / `off` dit l'état et ne change rien.

## Envoyer le rapport d'erreurs

1. En jeu : `/tai erreurs`.
2. Tape `/reload` : le jeu sauvegarde, TreeAI Coach écrit le fichier.
3. Envoie `%APPDATA%\TreeAICoach\TreeAI-erreurs.txt` (TreeAI Coach, page WoW Forever → **Erreurs WoW : ouvrir le
   fichier**), avec une phrase sur ce que tu faisais.

Le fichier contient les erreurs Lua, les actions bloquées par le jeu, les **erreurs avalées** (ce qui échoue sans
rien afficher : l'endroit, combien de fois, le premier message), les pics de lenteur et le **temps réel par module**
mesuré dans ton jeu pendant ta dernière session. Les chiffres de performance du changelog sont **mesurés en
simulation** ; ceux de ce fichier viennent de ta machine.

## FAQ

**Ça lague ?** `/tai perf` dit ce qui coûte le plus dans ton jeu (millisecondes réelles), `/tai pics` les moments
lents. Coupe ce dont tu ne te sers pas (fenêtre TreeAI → Fonctions). Si ça saccade encore, envoie le rapport.

**« TreeAI a été bloqué » ?** Clique « Ignorer » et envoie le rapport : chaque blocage y figure avec la fonction en
cause.

**L'addon n'apparaît pas ?** Vérifie le chemin `…\AddOns\TreeAI\TreeAI.toc`, relance le jeu et coche « Charger les
add-ons périmés » si besoin.
