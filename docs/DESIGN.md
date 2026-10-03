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
bordures lumineuses, halos de couleur en fond, verre dépoli / flou, bleu par défaut des
frameworks (`#3B82F6`, `#6366F1`, `#8B5CF6`), gris shadcn / slate `#0F172A` comme identité,
or / marine du client du jeu (`#C8AA6E`, `#0A1428`, `#010A13`).

## Lanceur (fenêtre principale)

Qt Widgets (docs/LAUNCHER.md). Esprit « Réglages système » d'Apple : calme, lisible, rangé.

* **Thèmes** clair et sombre (suivent Windows), jetons dans `ui_widgets.LIGHT` / `ui_widgets.DARK` :
  fond groupé, barre latérale, cellule de groupe, séparateur, texte / secondaire / tertiaire, **un
  seul accent** (vert sève : `#9BD84A` en sombre, `#2A721B` en clair pour garder 4,5:1 sur blanc),
  rouge et orange seulement pour le sens. Contraste ≥ 4,5:1 vérifié par `tests/test_design_rules.py`.
* **Police** : Segoe UI Variable (Text / Display) sous Windows, repli Segoe UI, puis Inter / Noto /
  DejaVu hors Windows (repli seulement, jamais l'identité). Titre de page 26 px semi-gras, corps
  14, secondaire 13, notes 12 ; jamais en dessous de 12.
* **Barre latérale** : 6 pages, icône au trait + nom, page courante en fond accent. Accueil ·
  Overlay · Alertes et voix · Analyse · Réglages · À propos (Ctrl+1 … 6). En bas : le point d'état
  et le bouton « Nouvelle version » quand elle existe.
* **Page** = grand titre, une phrase, puis des **listes groupées en retrait** : un petit titre de
  section, une cellule arrondie (10 px) dont les lignes sont séparées par un trait d'un pixel
  décalé de 16 px, une note sous la cellule si besoin. Colonne centrée de 760 px au plus.
* **Ligne** : titre (14) + description (13, secondaire) à gauche, contrôle à droite ; hauteur 48 px
  minimum. Une ligne qui ne s'applique pas est masquée (et son séparateur avec).
* **Contrôles** : interrupteur 42 × 24 (bouton qui glisse en 120 ms), contrôle segmenté, menu avec
  double chevron, curseur avec sa valeur écrite à droite, boutons arrondis 7 px ; un seul bouton
  accent par zone (l'action principale).
* **Accueil** commence par **une** ligne d'état (`ui_kit.status_line` : titre, un message utile,
  un bouton de correction si besoin) et le bouton Démarrer / Arrêter.
* Chaque champ de `Config` a un contrôle, ou figure dans `ui_common.HIDDEN_SETTINGS` avec sa raison
  (`tests/test_launcher.py` le vérifie) ; un réglage n'existe qu'à un seul endroit.
* **Interdits** en plus de la liste ci-dessus : dégradés, ombres portées, halos, verre dépoli,
  animations décoratives, icônes colorées multiples, cartes dans des cartes.

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
