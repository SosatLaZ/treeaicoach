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

## Lanceur (fenêtre principale) : « arbre de vie, saphir » (v2.17)

Qt Widgets (docs/LAUNCHER.md). Esprit Apple : calme, aéré, typographie nette, profondeur discrète.
Tout en saphir : un dégradé bleu nuit, derrière le contenu **un arbre de vie au trait** (tronc,
branches qui remplissent un cercle, racines en miroir, un anneau fin : le lien avec le logo TreeAI),
à faible contraste. Des panneaux de verre posés dessus, un seul accent saphir. Pas de lueur, pas de
néon. (Le « trou noir » de la première version a été refusé : « ça fait trop IA ».)

* **Fond** (`ui_backdrop.py`) : dessiné une fois hors ligne (`python tools/make_backdrop.py` ->
  `assets/ui/backdrop.png`, 1600 × 1000, ~225 Ko), décodé sur un thread de fond, mis à la taille
  de la fenêtre en mode « cover » (le cercle reste rond), une seule image en cache par taille que
  `paintEvent` recopie. **Aucune animation, aucun minuteur : 0 % de processeur au repos.**
* **Jetons** (`ui_widgets.SAPPHIRE`, un seul thème) : fond `#071430`, texte `#F5F8FF`, secondaire
  `#BFCBE3`, tertiaire `#9DAFD0`, accent `#74A8FA` (texte `#04102A` dessus), sélection `#1F4F9C`,
  sens : danger `#FF7D75`, attention `#F3D065`, sûr `#72D9A6`. Arbre `#1F5FBF`, graines `#4F8EF7`.
* **Verre** : cellules `rgba(18,38,74,0,80)` avec un filet `rgba(150,190,255,0,10)`, barre
  latérale `rgba(5,14,32,0,72)`. Pas de flou, pas d'ombre, pas de dégradé dans la feuille de style.
  `tests/test_design_rules.py` compose le verre sur le pixel le plus clair du fond : chaque couleur
  de texte y garde ≥ 4,5:1.
* **Police** : **Inter** (licence OFL, `assets/fonts`, sous-ensemble latin, 4 fichiers de ~60 Ko :
  Regular, Medium, SemiBold, Display SemiBold), chargée par `QFontDatabase` ; repli Segoe UI
  Variable. Titre de page 26 px semi-gras (Inter Display), corps 14, secondaire 13, notes 12 ;
  jamais en dessous de 12.
* **Navigation** : une page = un but, rangées par fréquence d'usage. En haut, ce qui sert à chaque
  partie : Accueil · Overlay · Alertes et voix · Discord · Analyse. En bas, sous « Configuration »,
  ce qu'on règle une fois : Réglages (démarrage, après la partie, fenêtre, IA, touches) · Avancé
  (minimap, analyse, performance, maintenance) · À propos (Ctrl+1 … 8). Page courante : fond
  `#1F4F9C`, icône blanche ; les autres icônes en secondaire. Logo (tuile saphir, arbre blanc) à
  côté du nom.
* **Page** = grand titre, une phrase, puis des **listes groupées** : petit titre de section, une
  cellule de verre arrondie (10 px), lignes séparées par un filet décalé de 16 px, une note sous la
  cellule si besoin. Colonne centrée de 760 px au plus.
* **Ligne** : titre (14) + description (13, secondaire) à gauche, contrôle à droite ; 48 px minimum.
* **Contrôles** : interrupteur 42 × 24 (saphir allumé), contrôle segmenté (segment choisi en
  `#1F4F9C`), menu à double chevron, curseur avec sa valeur, boutons arrondis 7 px ; un seul bouton
  accent par zone.
* **Accueil** commence par **une** ligne d'état (`ui_kit.status_line`) et le bouton Démarrer / Arrêter.
* Chaque champ de `Config` a un contrôle, ou figure dans `ui_common.HIDDEN_SETTINGS` avec sa raison
  (`tests/test_launcher.py`) ; un réglage n'existe qu'à un seul endroit.
* **Interdits** en plus de la liste ci-dessus : néon, bordures lumineuses, halos de couleur autour
  des contrôles, verre dépoli (flou), animations décoratives (le fond est fixe), icônes colorées
  multiples, cartes dans des cartes. L'arbre est **le seul** élément décoratif, et il reste derrière.

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
seulement pendant une partie et quand il est affiché. Chargement des pages : voir
docs/LAUNCHER.md (rien de bloquant sur le thread de la fenêtre, une page n'est jamais reconstruite).

`tests/test_design_rules.py` vérifie automatiquement les interdits (tiret cadratin dans les
textes de l'interface, couleurs et polices bannies, emoji, dégradés et ombres dans la feuille de
style, contrastes).
