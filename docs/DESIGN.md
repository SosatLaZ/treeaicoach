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
| `MUTED`      | `#8B948F` | texte secondaire                                             |
| `DIM`        | `#59615C` | légendes, valeurs absentes                                   |
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

## Typographie

Trois tailles seulement (avant le facteur `ui_scale`, 0,88 par défaut) :

| Rôle       | Police                                   | Taille | Graisse  |
|------------|------------------------------------------|--------|----------|
| Affichage  | Bahnschrift SemiBold (chiffres, titres)  | 17     | semi-gras|
| Corps      | Segoe UI                                 | 11     | normal / gras |
| Légende    | Segoe UI, MAJUSCULES espacées            | 9      | gras     |

Les chiffres importants (chrono, K/D/A, CS/min) utilisent la police d'affichage en taille 22.
Repli hors Windows : DejaVu Sans / Liberation Sans. Interdites comme identité :
Inter, Poppins, Space Grotesk, Geist.

## Espacement, formes

* Grille de 4 px : 4 / 8 / 12 / 16 / 24.
* Rayon : **4 px** (contrôles, portraits carrés), 6 px maximum (dialogues). Pas de pilule ronde.
* Pas d'ombre portée douce. Pas de carte dans une carte : une section = titre en légende +
  séparateur 1 px + lignes.
* Tableaux alignés (colonnes fixes, chiffres alignés à droite) plutôt que des grilles de
  cartes identiques.

## Textes

* Libellés de 1 à 2 mots sur les boutons (« Démarrer », « Rapport », « Dossier »).
* Pas de tiret cadratin (—) dans l'interface : « : », « · » ou un retour à la ligne.
* Pas d'emoji, pas d'icône « étincelle », pas de « magie », pas de « Bienvenue dans… ».
* Français simple, tutoiement, phrases courtes.

## Mouvement

Seulement quand il a un sens : point « en direct » qui pulse pendant l'analyse, jauge de
menace, flash de danger. Pas d'animation décorative.

`tests/test_design_rules.py` vérifie automatiquement les interdits (tiret cadratin dans les
textes de l'interface, couleurs et polices bannies, emoji).
