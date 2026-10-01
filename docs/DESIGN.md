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

## Typographie

La lisibilité passe avant la compacité (retour joueur : « trop petit »). Tailles en pixels à
100 % ; le facteur `ui_scale` (1,0 par défaut, réglable dans Réglages > Interface) et l'échelle
d'affichage de Windows (facteur DPI par écran appliqué par CustomTkinter) s'y ajoutent.

| Rôle                     | Police                                   | Taille | Graisse        |
|--------------------------|------------------------------------------|--------|----------------|
| Titre de page            | Bahnschrift SemiBold                     | 26     | semi-gras      |
| Titre de section         | Bahnschrift SemiBold                     | 17     | semi-gras      |
| Corps (libellés, menus)  | Segoe UI                                 | 14     | normal / gras  |
| Secondaire (descriptions)| Segoe UI, couleur `MUTED`                | 13     | normal         |
| Légende                  | Segoe UI, MAJUSCULES                     | 12     | gras           |

Jamais en dessous de 12 px. Les chiffres importants (chrono, statistiques) utilisent la police
d'affichage en 28, les valeurs de réglage en 16. Contraste minimal 4,5:1 pour tout texte.
Repli hors Windows : DejaVu Sans / Liberation Sans. Interdites comme identité :
Inter, Poppins, Space Grotesk, Geist.

## Navigation : ce qui est où

Pensé pour le joueur qui ouvre l'application 30 s avant une partie.

* **4 pages** dans la barre latérale : **En jeu** · **Analyses** · **Réglages** · **Aide**
  (Ctrl+1 … 4). L'application s'ouvre toujours sur « En jeu ».
* **À un clic, sur toutes les pages** (barre latérale) : Voix, Overlay, Mode sûr, **Ton niveau**
  (4 boutons, toujours visibles même à 980 × 640), l'état de l'analyse (clic : retour sur « En jeu »)
  et, quand elle existe, la **nouvelle version** (bouton vert, ouvre Réglages > Mises à jour).
* **En jeu** : Démarrer / Arrêter, tester la voix, tester l'overlay, mode démo, diagnostic complet
  (avec sa touche en jeu, Ctrl+F8 par défaut), rapport et replay de la dernière partie.
* **Réglages** = une seule page, des onglets nommés d'après ce qu'ils changent :
  Général (démarrage, après la partie, fenêtre) · **Affichage** (ce que tu vois en jeu, avec
  l'aperçu) · **Voix** (ce que tu entends) · Détection · IA · Mises à jour · **Avancé** (touches
  en jeu, performance, maintenance). Un réglage n'existe qu'à un seul endroit.
* Chaque champ de `Config` a un contrôle dans Réglages, ou figure dans
  `ui_common.HIDDEN_SETTINGS` avec sa raison (barre latérale, appris, écrit par une autre
  action). `tests/test_ui_settings.py` le vérifie : aucun réglage mort ni inaccessible.
* Une seule échelle d'aide : le **niveau** (Débutant … Expert). Pas de deuxième jeu de
  « préréglages » qui le contredit.

## État d'abord

* La page « En jeu » commence par **une** ligne d'état : un titre (« En attente d'une partie »,
  « En jeu : Garen top », « Capture noire »…), **un** message utile (jamais la répétition du
  titre) et, en cas de problème, **un** bouton de correction à côté (Calibrer, Aide,
  Diagnostic). Texte : `ui_kit.status_line`.
* Avant la partie, la page ne montre que ce qui sert avant la partie : carte de la sélection des
  champions, dernière partie (rapport, replay, progrès), objectif, point à travailler ou les
  3 vérifications du premier lancement, et le panneau « Système ». Ennemis, alliés, conseil du
  moment et radar n'apparaissent qu'en partie (pas de cases « ? » vides).

## Mise en page

* Une colonne de contenu centrée, 860 px au plus (1300 px pour « En jeu »), marges de 32 px :
  jamais un libellé collé à gauche et son contrôle collé au bord droit d'un grand écran.
* Une section = titre + phrase d'explication + **une** carte (`SURFACE`, bord 1 px, rayon 6)
  dont les lignes sont séparées par un trait de 1 px. Pas de carte dans une carte.
* Ligne de réglage : libellé (14) + description (13) à gauche, contrôle juste à droite ;
  hauteur minimale identique pour toutes les lignes ; si la fenêtre est étroite, un contrôle
  large passe sous le texte. Une action propre à une section (« Tester la voix »,
  « Déplacer ») va à droite de son titre, pas dans l'en-tête de la page.
* Une section qui ne s'applique pas (ex. « Radar » hors du mode radar) est masquée, pas grisée.
* Contrôles : boutons 34 px (30 dans les barres d'outils et les tableaux), menus, champs et
  sélecteurs 34 px.
* Interrupteurs : pilule 46 × 26 (38 × 22 dans la barre latérale). Éteint : piste sombre
  cerclée, bouton gris à gauche ; allumé : piste verte pleine, bouton sombre à droite.

## Espacement, formes

* Grille de 4 px : 4 / 8 / 12 / 16 / 24. Jetons (`ui_common.py`) : `CTL_GAP` 8 entre deux
  contrôles, `ROW_PAD_Y` 12 dans une ligne de réglage, `ROW_CTL_GAP` 24 entre le texte et son
  contrôle, `TAB_GAP` 24 entre deux onglets, `SECTION_GAP` 28 entre deux sections, `CARD_PAD` 20,
  `PAGE_PAD` 32. Hauteurs : `BTN_H` 34, `BTN_H_SMALL` 30 (barres d'outils, tableaux, barre
  latérale), `CTL_H` 34, `LINK_H` 22 (liens de correction du panneau « Système »), `ICON_BTN` 30.
* Rayon : **4 px** (contrôles, portraits carrés), 6 px maximum (cartes, dialogues). Pas de pilule
  ronde, sauf les interrupteurs et le bouton des curseurs (leur forme est celle qu'on reconnaît).
* Pas d'ombre portée douce. Pas de carte dans une carte.
* Tableaux alignés (colonnes fixes, chiffres alignés à droite) plutôt que des grilles de
  cartes identiques.

## Textes

* Libellés de 1 à 2 mots sur les boutons (« Démarrer », « Rapport », « Dossier »).
* Les touches citées dans un texte sont les touches **réglées** (lues dans la configuration),
  jamais une touche par défaut écrite en dur.
* Pas de tiret cadratin (—) dans l'interface : « : », « · » ou un retour à la ligne.
* Pas d'emoji, pas d'icône « étincelle », pas de « magie », pas de « Bienvenue dans… ».
* Français simple, tutoiement, phrases courtes.

## Mouvement et performance

Seulement quand il a un sens : point « en direct » qui pulse pendant l'analyse, jauge de
menace, flash de danger. Pas d'animation décorative. Rien ne bouge quand la page « En jeu »
n'est pas affichée ou que la fenêtre est réduite (rafraîchissement 1 s, 2 s réduite) ; les
pages sont construites à la première visite ou pendant un temps mort.

`tests/test_design_rules.py` vérifie automatiquement les interdits (tiret cadratin dans les
textes de l'interface, couleurs et polices bannies, emoji).
