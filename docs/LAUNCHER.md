# Lanceur (fenêtre principale) : technique et mesures

Retours du joueur sur l'ancien lanceur CustomTkinter : pages qui se chargent bizarrement, lentement,
morceau par morceau ; textes trop petits ; « ça fait trop IA » ; page Réglages laide. Mesures de
l'ancien lanceur (fournies avec la demande) : changement de page 5-32 ms sous Linux, 30-200 ms sous
Wine, toutes les pages prêtes 4 à 6 s après le lancement sous Wine. La cause de fond : chaque
`CTk*` est un canevas Tk de plusieurs éléments, sans anticrénelage, et les contournements
(pages « parquées » hors écran, canevas maison, construction par tranches) étaient eux-mêmes une
source de bugs.

## Décision : Qt Widgets (PySide6 Essentials 6.7), une feuille de style unique

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| (a) Tk réécrit, une scène canevas par page | aucune dépendance en plus | Tk ne lisse pas les formes (coins arrondis crénelés : l'« effet Apple » est impossible sans images PIL), DPI par écran à la main, champs de texte et menus à recoder ; c'est exactement ce qu'on a essayé avec `setting_row_class` / `Dropdown` / `Segmented` | rejeté |
| (b) **PySide6 / Qt Widgets + QSS** | rendu anticrénelé, DPI par écran natif (Qt 6), mises en page qui ne « clignotent » pas, `QStackedWidget` (changer de page = changer le widget visible), contrôles natifs (champs, menus, focus clavier), hook PyInstaller officiel | +11 Mo environ sur l'exe | **retenu** |
| (c) WebView2 (pywebview) | HTML/CSS libre | runtime Edge WebView2 absent de Wine (non testable ici : ni `--ui-smoke` ni captures dans le CI), deux processus Chromium (≈ 150 Mo de RAM de plus pendant la partie), pont JS/Python pour chaque réglage | rejeté |

Cohabitation avec la partie : l'overlay (`overlay.py`) possède **son propre thread** et ses
fenêtres Win32 « layered » brutes, pompées par `PeekMessageW` ; il ne connaît pas la boîte à outils de
la fenêtre principale. Qt tourne sur le thread principal, l'overlay et les touches globales
(`hotkeys.py`) sur les leurs : aucun objet Qt n'est touché hors du thread principal (les résultats
passent par `ui_common._Dispatcher`, une file vidée toutes les 30 ms par un `QTimer`). Le chemin en
jeu (moteur, overlay, voix) n'a pas changé.

## Mesures

Prototype (`QStackedWidget`, 6 pages de 30 lignes, même QSS), avant d'écrire le lanceur :

| | Linux (xvfb) | Wine |
|---|---|---|
| import PySide6 | 125 ms | 520-560 ms (à chaud) |
| premier affichage | 290 ms | 980-1080 ms |
| construction d'une page de 30 lignes | 100-110 ms | 160-180 ms |
| changement de page déjà construite | 9-11 ms (max 13) | 11-14 ms (max 23) |

Lanceur final (`python tools/launcher_bench.py`, sans moteur ; « changement » = afficher la page et
la peindre entièrement) :

| | Linux (xvfb) | Wine |
|---|---|---|
| fenêtre + Accueil construits | 145-175 ms | 280-455 ms |
| pages (Overlay, Alertes, Analyse, Réglages, À propos) | 50 / 45-62 / 10 / 111-117 / 31-41 ms | 65-97 / 84-88 / 12-21 / 122-137 / 54-86 ms |
| changement de page (peinture comprise) | moyenne 14-16 ms, max 30 ms | moyenne 22-36 ms, max 38-82 ms |
| toutes les pages prêtes (`--ui-smoke`, avec le vrai moteur qui démarre en parallèle) | 0,86 s après le lancement | 1,1 s (contre 4-6 s avant) |
| mémoire du processus (bench, sans moteur) | 177 Mo (max RSS) | non mesurée |
| `--ui-smoke` | code 0 | code 0 |

Taille de l'exe (estimation, l'exe n'a pas été construit ici) : Qt Core + Gui + Widgets, `qwindows`,
shiboken = 40,7 Mo bruts, **15,8 Mo compressés** ; retirés : Tcl/Tk + tkinter + customtkinter
= 13,3 Mo bruts, 4,5 Mo compressés. Soit environ **+11 Mo** (82 Mo -> ~93 Mo). Le `.spec` retire
`opengl32sw.dll` (20 Mo), les traductions Qt et les greffons inutilisés.

### Le bug qui coûtait 4,8 s sous Wine

Premier essai sous Wine : Accueil construit en 4,7 s, Alertes en 8,4 s. Le profil montrait
`QBoxLayout.addWidget` à 66 ms par appel. Cause : `setVisible(True)` sur un widget **sans parent**
(note de bas de section, sous-titre de page) crée une vraie fenêtre Windows de premier niveau, que
l'ajout dans la mise en page détruit aussitôt. Règle : on ne montre ni ne cache jamais un widget
avant de l'avoir placé dans son parent (seul `hide()` est permis avant). Après correction : 0,4 s.
Les modules lents à importer (`tts_neural` : edge-tts + aiohttp, `ai_advisor`) sont lus par un
thread de fond qui remplit ensuite la liste.

## Règles de robustesse (vérifiées par `tests/test_launcher.py`)

* **Rien de bloquant sur le thread principal** : démarrage du moteur, voix, détecteur, overlay,
  LCU, mise à jour (vérification, téléchargement, installation), liste des parties et rapports,
  aperçu de l'overlay, sélection des champions, test de la clé IA, démarrage auto de Windows :
  tout passe par `CoachApp.run_job(job, done, failed)`.
* **Rien après la fermeture** : `run_job`, `later` et `post` ne rappellent plus rien une fois
  `close()` commencé ; `show_page` ne construit plus de page ; une boîte modale ouverte est fermée
  d'abord ; les minuteries sont arrêtées avant d'arrêter le moteur.
* **Pages** : l'Accueil est construit avant l'affichage ; les autres le sont une par tour de boucle
  juste après le premier affichage (`_prebuild`, 15 ms entre deux) ; un clic avant construit la
  page demandée tout de suite. Une page n'est jamais détruite ni reconstruite. Les listes qui
  changent (parties, lignes « Système ») ne sont redessinées que si leur signature change.
* **Fenêtre** : taille et position mémorisées (`ui_geometry`, format `LxH+X+Y`), remises sur
  l'écran si l'écran a changé, taille minimale 960 × 600 (utilisable en 1280 × 720). DPI par
  écran : Qt 6 (arrondi « PassThrough ») ; `ui_scaling` force un facteur au prochain lancement.
* **Thème** : clair ou sombre selon Windows (`styleHints().colorScheme()`, repli registre).

## Outils

* `python tools/launcher_bench.py [--profile]` : temps de création, de chaque page, des changements.
* `python tools/launcher_shots.py [--out DIR]` : captures de chaque page, clair et sombre, en
  1280 × 720 et 1920 × 1080 (moteur factice « en attente d'une partie », trois parties fictives).
