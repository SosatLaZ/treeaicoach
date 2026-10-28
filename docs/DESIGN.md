# TreeAI Coach : direction visuelle

Une seule direction pour toute l'application (fenêtre, rapports HTML, aperçus) :
**« régie esport »**. Un outil de joueur fait à la main, pas un modèle de site généré.
Fond graphite tirant vers le vert (la sève de l'arbre TreeAI), une seule couleur d'accent
(vert sève), des chiffres en police condensée, des bords nets, des séparateurs d'un pixel
au lieu de cartes empilées.

## Couleurs (jetons)

| Jeton        | Valeur    | Usage                                                        |
|--------------|-----------|--------------------------------------------------------------|
| `BG`         | `#0C0E0D` | fond de fenêtre / de page                                    |
| `SURFACE`    | `#121513` | barre latérale, bandeau d'état, dialogues                    |
| `RAISED`     | `#191D1B` | survol, champs, boutons secondaires                          |
| `SUNKEN`     | `#090B0A` | zones creuses (journal, barres de jauge)                     |
| `LINE`       | `#222725` | séparateurs 1 px                                             |
| `LINE_STRONG`| `#2F3532` | bord des contrôles, séparateur actif                         |
| `TEXT`       | `#E4E8E5` | texte principal                                              |
| `MUTED`      | `#A4ADA8` | texte secondaire, descriptions (8,4:1 sur `BG`)              |
| `DIM`        | `#7E8782` | légendes, valeurs absentes (5,2:1 sur `BG`, ≥ 4,5:1 partout) |
| `ACCENT`     | `#9BD84A` | **seul accent** : action principale, onglet actif, valeurs   |
| `ACCENT_DIM` | `#3E5A1E` | fond d'élément sélectionné                                   |
| `ON_ACCENT`  | `#0C0E0D` | texte sur l'accent                                           |
| `DANGER`     | `#E5484D` | sens uniquement : danger, ennemi, mort, défaite              |
| `WARNING`    | `#E8A23A` | sens uniquement : attention, à surveiller                    |
| `OK`         | = `ACCENT`| sens : sûr, victoire, bon chiffre (le vert TreeAI)           |
| `ALLY`       | `#4A90D9` | anneau des alliés (couleur d'équipe, pas un accent)          |

Interdits : dégradés violet → bleu / indigo / cyan, violet « vibecode », néon cyan ou violet,
bordures lumineuses, halos de couleur en fond (hors l'arbre du lanceur), verre dépoli / flou, bleu par défaut des
frameworks (`#3B82F6`, `#6366F1`, `#8B5CF6`), gris shadcn / slate `#0F172A` comme identité,
or / marine du client du jeu (`#C8AA6E`, `#0A1428`, `#010A13`).

## Lanceur (fenêtre principale) : « graphite » (v2.21)

Qt Widgets (docs/LAUNCHER.md). Référence : Réglages système d'Apple, Linear, Raycast. Calme, plat, une
seule couleur d'accent, typographie nette. (Le « trou noir » puis l'« arbre de vie saphir » ont été
refusés : « trop kitsch ».)

* **Fond** : une couleur plate (`ui_widgets.Backdrop`), aucune image, aucun dégradé, aucune animation.
* **Jetons** (`ui_widgets.GRAPHITE`, un seul thème) : fenêtre `#101113`, barre latérale `#151619`,
  cellules opaques `#1B1C20`, filets blancs à 6-7 %, texte `#ECEDEF`, secondaire `#A3A7AE`, tertiaire
  `#8A8E97`, **accent unique** `#7AA7FF` (interrupteurs, bouton principal, liens), sélection neutre
  `#2C2F36` ; sens : danger `#FF6B63`, attention `#EBBE4D`, sûr `#5FD08F`. Contraste ≥ 4,5:1 vérifié
  par `tests/test_design_rules.py`.
* **Police** : **Inter** (OFL, `assets/fonts`), repli Segoe UI Variable. Titre de page 26 px, titre de
  section 16 semi-gras (Inter Display, couleur du texte), corps 14, secondaire 13, notes 12 ; jamais en
  dessous de 12.
  Pas de monospace.
* **Grille de 8 pt** : colonne centrée de 760 px, marges 40, **40 entre sections**, 8-12 entre le titre et
  sa cellule, rayon 12 (cellules) / 8 (contrôles), lignes de 48 px au moins. Cellules **sans bord** (le fond
  `#1B1C20` suffit) ; seuls les filets entre lignes restent.
* **Navigation (v3.4, 6 pages au plus)** : Accueil (jouer) · Overlay (affichage en jeu) · Alertes et voix ·
  Discord · Analyse ; en bas : Réglages (Ctrl+1 … 6). Les anciennes pages « Avancé » et « À propos » vivent
  dans Réglages (Mise à jour, puis les blocs repliés « Avancé » et « Aide et mentions ») ; leurs anciens noms
  y mènent encore (`PAGE_ALIASES`, `SETTINGS_ANCHORS`).
* **Un seul bloc replié « Avancé » par page** (`ui_widgets.Fold`), en bas, fermé : les réglages rares
  (détails, sons, moteur de voix, stockage, détection, performance, support). Ses lignes ne sont construites
  qu'à la première ouverture ou pendant un temps mort après le démarrage (`Fold.defer`).
* **Page** = titre, une phrase courte, puis des listes groupées (légende, cellule, filet entre lignes).
  **Ligne** : titre + description courte à gauche, contrôle à droite.
* **Un réglage = un seul endroit** (`tests/test_launcher.py` vérifie chaque interrupteur : un champ de
  `Config`, appliqué tout de suite, sauvegardé). Un sous-réglage n'est affiché que si son interrupteur
  maître est allumé (`CoachApp.depends`).
* **Accueil** : la carte d'état (Démarrer / Arrêter) avec l'art du champion à droite, discret (assombri,
  fondu, opacité 55 %, aucun cadre décoratif) ; les cartes de sélection des champions et des joueurs
  seulement quand elles servent ; « Coach » (niveau, voix, mode sûr) ; essayer ; dernière partie ; système.
* **Lancement** (`ui_flair.BootSplash`, < 0,5 s) : le nom, un trait de progression fin et UNE ligne
  d'état (le premier contrôle réel qui échoue, sinon « Prêt »). `TREEAICOACH_NO_SPLASH=1` la désactive.
* **Interdits** : images de fond, lueurs, néon, verre, ornements (angles dorés, filets « scanline »),
  texte en capitales qui crie, monospace décoratif, emoji, animations décoratives, cartes dans des cartes.

## Textes

* Libellés de 1 à 2 mots sur les boutons (« Démarrer », « Rapport », « Dossier »).
* Les touches citées dans un texte sont les touches **réglées** (lues dans la configuration),
  jamais une touche par défaut écrite en dur.
* Pas de tiret cadratin (—) dans l'interface : « : », « · » ou un retour à la ligne.
* Pas d'emoji, pas d'icône « étincelle », pas de « magie », pas de « Bienvenue dans… ».
* Français simple, tutoiement, phrases courtes.

## Mouvement et performance

Seulement quand il a un sens : interrupteur qui glisse, flash de danger en jeu. Rien ne se
rafraîchit quand la fenêtre est réduite (2 s) ; l'Accueil se met à jour 4 fois par seconde
seulement pendant une partie et quand il est affiché. Démarrage : rien de lourd avant la première image
(ni numpy, PIL, cv2, onnxruntime, discord, edge_tts, torch : `tests/test_ui_layout34.py`), le moteur, la
voix et le détecteur démarrent 40 ms après, les autres pages 0,7 s après. Chargement des pages : voir
docs/LAUNCHER.md (rien de bloquant sur le thread de la fenêtre, une page n'est jamais reconstruite).

`tests/test_design_rules.py` vérifie automatiquement les interdits (tiret cadratin dans les
textes de l'interface, couleurs et polices bannies, emoji, dégradés et ombres dans la feuille de
style, contrastes).
