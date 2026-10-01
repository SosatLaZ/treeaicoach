<p align="center">
  <img src="packaging/icon.png" width="140" height="140" alt="Logo de TreeAI Coach">
</p>

<h1 align="center">TreeAI Coach</h1>

<p align="center">
  <b>Ton coach vocal anti-gank pour League of Legends : il surveille la minimap et te prévient à voix haute quand le jungler ennemi arrive.</b>
</p>

<p align="center">
  <a href="https://github.com/SosatLaZ/treeaicoach/raw/claude-team/brave-mendel-j8fqkf/release/TreeAICoach.exe"><img src="https://img.shields.io/badge/%E2%AC%87%EF%B8%8F%20T%C3%89L%C3%89CHARGER-TreeAICoach.exe%20v1.8.0-C8AA6E?style=for-the-badge&labelColor=0A1428" alt="Télécharger TreeAICoach.exe" height="56"></a>
</p>

<p align="center">
  <b>👉 <a href="https://github.com/SosatLaZ/treeaicoach/raw/claude-team/brave-mendel-j8fqkf/release/TreeAICoach.exe">Clique ici pour télécharger TreeAICoach.exe</a></b> (Windows 10/11, ~75 Mo) — puis double-clique dessus.<br>
  <sub>Dossier <a href="release/">release/</a> · empreinte SHA-256 dans <a href="release/SHA256.txt">SHA256.txt</a> · si Windows affiche « PC protégé » : <i>Informations complémentaires → Exécuter quand même</i>.</sub>
</p>

<p align="center">
  <a href="https://github.com/SosatLaZ/treeaicoach/actions/workflows/build-windows.yml"><img src="https://github.com/SosatLaZ/treeaicoach/actions/workflows/build-windows.yml/badge.svg" alt="Construction de TreeAICoach.exe"></a>
  <img src="https://img.shields.io/badge/Windows-10%20%7C%2011-0A1428?logo=windows" alt="Windows 10 | 11">
  <a href="LICENSE"><img src="https://img.shields.io/badge/licence-MIT-C8AA6E" alt="Licence MIT"></a>
</p>

---

## 📥 Télécharger

1. Va sur la page des **[Releases](https://github.com/SosatLaZ/treeaicoach/releases)** et télécharge **`TreeAICoach.exe`** :
   * **version stable** : [dernière release](https://github.com/SosatLaZ/treeaicoach/releases/latest) ;
   * **dernière version de développement** (mise à jour à chaque modification) :
     [TreeAICoach.exe « latest »](https://github.com/SosatLaZ/treeaicoach/releases/download/latest/TreeAICoach.exe).
2. C'est **un seul fichier**, sans installation : mets-le où tu veux (Bureau, Documents…).
   Python n'est pas nécessaire.
3. Au premier lancement, Windows peut afficher **« Windows a protégé votre ordinateur »** (SmartScreen).
   Clique sur **Informations complémentaires**, puis sur **Exécuter quand même**.

> **Pourquoi cet avertissement ?** L'exécutable n'est pas signé numériquement (un certificat de signature
> coûte plusieurs centaines d'euros par an). SmartScreen se méfie donc de tout programme récent et peu téléchargé.
> Le code source est public et l'exe est construit automatiquement par GitHub Actions à partir de ce code ;
> l'empreinte **SHA-256** de chaque version est indiquée dans sa release
> (vérification : `Get-FileHash .\TreeAICoach.exe` dans PowerShell).

**Configuration :** Windows 10 ou 11 (64 bits), League of Legends en mode d'affichage **Sans bordure** (ou Fenêtré).

## 🚀 Démarrage en 3 étapes

1. **Dans League of Legends** : `Échap` → **Vidéo** → **Mode d'affichage : Sans bordure**.
   *(En plein écran, Windows ne laisse aucun programme capturer l'image du jeu : la capture serait noire.)*
2. **Double-clique sur `TreeAICoach.exe`** : l'interface s'ouvre et l'analyse démarre toute seule ;
   elle attend le début de la partie (aucune capture tant que tu n'es pas en jeu).
3. **Joue !** Les alertes arrivent automatiquement sur la Faille de l'invocateur.

💡 Sans partie en cours, essaie **« Tester la voix »** et **« Mode démo »** sur le tableau de bord.

## ✨ Fonctionnalités

* 🗣️ **Alertes de gank vocales en français**, courtes et claires :
  « Attention, Lee Sin approche. », « Gank ! Lee Sin, recule ! », « Danger, 3 ennemis arrivent, recule ! »,
  « Jungler ennemi vu en haut. », et en option « Darius a disparu. » (ton adversaire de voie est MIA).
* 🎯 **Cercle du jungler** : quand le jungler ennemi entre dans le brouillard, la zone où il peut se trouver
  s'affiche et grandit avec le temps (en tenant compte des murs), à partir de sa dernière position vue.
* 📡 **Radar** : une copie agrandie de la minimap, placée juste à côté de la vraie, avec tes zones d'alerte,
  les ennemis visibles, leurs flèches d'approche et les dernières positions connues des ennemis disparus.
* 🧭 **HUD** : jauge de menace (sûr / attention / danger), ligne du jungler, les 5 ennemis
  (« vu » ou « MIA 23 s »), les objectifs et la dernière alerte.
* 🚨 **Flash de danger** : un cadre rouge sur les bords de l'écran pendant un gank (sans jamais couvrir la minimap).
* ⏱️ **Chronos des objectifs** : Dragon, Larves du Néant, Héraut, Atakhan, Baron, Dragon ancestral,
  annoncés 60 s et 20 s avant leur apparition.
* 🔔 **Rappels personnels** : « pense à rentrer » quand tu as assez d'or, balise de contrôle quand tu es en base,
  petit récap après ta mort, et une suggestion de pause après 3 défaites d'affilée.
* ⌨️ **Raccourcis** : **F9** « Où est le jungler ? » (réponse vocale), **F10** couper / rétablir la voix,
  **F11** afficher / masquer l'overlay.
* 📊 **Analyse d'après-partie** : un rapport s'ouvre à la fin de la partie — morts (avec ou sans alerte),
  ganks subis, habitudes du jungler ennemi (zones, timings, voies gankées), temps passé par zone, CS/min
  et **conseils personnalisés**.
* 🗂️ **Historique** : l'onglet **Analyses** liste toutes tes parties et tes statistiques de session.

## 🔍 Comment ça marche

1. **Capture d'écran uniquement** : l'app regarde la minimap en bas à droite de l'écran, exactement comme OBS ou
   Discord capturent ton écran. Elle trouve la minimap toute seule.
2. **Détection des icônes** : un petit réseau de neurones (format ONNX, exécuté sur le processeur) repère les icônes
   de champions. Il a été entraîné sur des **minimaps synthétiques** fabriquées à partir des **textures officielles**
   du jeu (carte, icônes des champions et de leurs skins, brouillard, sbires, balises…).
3. **Identification** : l'**API officielle Live Client Data** de Riot, fournie par le jeu lui-même pendant la partie
   (`https://127.0.0.1:2999`), donne la liste des 10 champions, leurs équipes et leurs skins. L'app reconnaît ainsi
   chaque icône et sait qui est le jungler ennemi.
4. **Analyse** : les positions sont suivies dans le temps ; si un ennemi dangereux se rapproche de toi,
   l'alerte est prononcée par la synthèse vocale de Windows.

Tout est calculé **sur ton PC** et aucune donnée personnelle n'est envoyée. La seule connexion à Internet
(facultative) télécharge les icônes des skins de la partie depuis CommunityDragon, pour mieux reconnaître les champions.

## 🛡️ Sécurité et règles de Riot

**Ce que TreeAI Coach fait :** il lit les pixels déjà affichés sur ton écran et l'API officielle Live Client Data.
Ses fenêtres d'overlay sont de simples fenêtres Windows transparentes, posées au-dessus du jeu.

**Ce qu'il ne fait jamais :**

* ❌ aucune lecture ni écriture de la mémoire du jeu, aucune injection, aucun hook DirectX ;
* ❌ aucune touche ni aucun clic simulé (les raccourcis F9/F10/F11 utilisent l'API Windows standard
  `RegisterHotKey`, comme Discord ou OBS) ;
* ❌ aucun contournement ni aucune dissimulation vis-à-vis de Vanguard ou de l'anti-triche ;
* ❌ aucun suivi des sorts, des ultimes ou des temps de recharge ennemis.

**Soyons honnêtes :**

* TreeAI Coach **n'est ni approuvé ni soutenu par Riot Games**.
* La politique de Riot sur les applications tierces **évolue** : par exemple, depuis **mars 2025**, les
  applications qui suivent les ultimes et les temps de recharge des ennemis sont **interdites**. Riot peut juger à tout
  moment qu'un outil donne un avantage injuste.
* Le **cercle du jungler** est la fonction **la plus sensible** : même s'il n'utilise que ce que tu as vu à l'écran,
  il estime la zone où se trouve un ennemi invisible. Tu peux le **désactiver** : onglet **Overlay** →
  **Cercle du jungler** → **Off**. Si tu veux être le plus prudent possible, désactive-le.
* **Tu utilises TreeAI Coach à tes propres risques**, sans aucune garantie (voir la [licence](LICENSE)).

## ⚙️ Réglages

| Onglet | Ce que tu peux régler |
| --- | --- |
| **Tableau de bord** | Démarrer / arrêter l'analyse, tester la voix, mode démo, calibrer la minimap |
| **Alertes & voix** | Chaque type d'alerte, **sensibilité** (taille de la zone d'alerte), voix, vitesse, volume, bip de danger, raccourcis |
| **Overlay** | Radar, HUD, flash de danger, **cercle du jungler** (Jungler / Tous / Off), position et taille des fenêtres (« Déplacer les fenêtres ») |
| **Analyses** | Historique des parties, rapports, statistiques de session |
| **Réglages** | Minimap automatique ou manuelle, côté de la minimap, images par seconde, détecteur, **lancer avec Windows**, journaux, réinitialisation |

Tes réglages, journaux et rapports sont rangés dans **`%APPDATA%\TreeAICoach`**
(copie ce chemin dans la barre d'adresse de l'Explorateur de fichiers).

## 🧰 Dépannage

| Problème | Solution |
| --- | --- |
| **Capture noire** / « passe en mode Sans bordure » | Dans LoL : `Échap` → **Vidéo** → **Mode d'affichage : Sans bordure**. |
| **Pas de voix** | Installe la voix française de Windows : **Paramètres > Heure et langue > Voix** → *Ajouter des voix* → **Français (France)**. Choisis-la ensuite dans **Alertes & voix** puis clique sur **Tester la voix**. Vérifie aussi que la voix n'est pas coupée (**F10**). |
| **Minimap non trouvée** | Tableau de bord → **Calibrer la minimap** : trace un carré autour de ta minimap. Si ta minimap est à gauche, change le côté dans **Réglages**. |
| **L'app ne voit pas la partie** | L'API de Riot ne répond qu'une fois la partie commencée (pas pendant l'écran de chargement). Seule la Faille de l'invocateur est prise en charge. |
| **Overlay invisible** | Vérifie le mode **Sans bordure**, l'onglet **Overlay**, et appuie sur **F11**. |
| **Antivirus : faux positif** | Les exécutables non signés créés avec PyInstaller sont parfois signalés à tort. Télécharge l'exe **uniquement** depuis la page officielle des Releases, compare son SHA-256, puis ajoute une exception dans ton antivirus. |
| **Démarrage un peu lent** | Normal : l'exe se décompresse en quelques secondes au lancement. |
| **Autre problème** | Réglages → **Ouvrir les journaux**, et joins le dernier fichier à ton signalement dans les [Issues](https://github.com/SosatLaZ/treeaicoach/issues). |

## 👩‍💻 Pour les développeurs

Python 3.11 (Windows pour la voix, la capture et l'overlay ; les tests tournent aussi sous Linux).
Commandes pour PowerShell, depuis le dossier du projet :

```powershell
git clone https://github.com/SosatLaZ/treeaicoach
cd treeaicoach
py -3.11 -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt

.venv\Scripts\python -m treeaicoach              # interface
.venv\Scripts\python -m treeaicoach --demo       # partie simulée
.venv\Scripts\python -m treeaicoach --selftest   # autotest (--selftest-out rapport.txt)
.venv\Scripts\python -m pytest -q                # tests
```

* Architecture et contrats entre modules : [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) ;
  faits mesurés sur de vraies minimaps : [`docs/MINIMAP_FACTS.md`](docs/MINIMAP_FACTS.md).
* **Entraînement du modèle** (PyTorch, hors exe) : toutes les commandes et options sont dans
  [`training/README.md`](training/README.md). En résumé :

  ```powershell
  .venv\Scripts\python -m pip install torch --index-url https://download.pytorch.org/whl/cpu
  .venv\Scripts\python -m pip install -r requirements-train.txt
  .venv\Scripts\python tools/fetch_assets.py     # ressources officielles (textures, icônes, skins)
  # puis training/train.py -> training/export_onnx.py -> training/evaluate.py
  # (le modèle ONNX est écrit dans treeaicoach/assets/model/)
  ```

* **Construire l'exe** :

  ```powershell
  .\packaging\build_exe.bat      # venv + tests + PyInstaller + autotest -> dist\TreeAICoach.exe
  # ou à la main :
  .venv\Scripts\python -m PyInstaller packaging/treeaicoach.spec --noconfirm --clean
  .venv\Scripts\python packaging/make_icon.py    # régénère packaging/icon.png et icon.ico
  ```

* **Publier** : chaque push sur la branche principale (ou sur une branche `claude…`) met à jour la pré-version
  « latest » ; un tag `vX.Y.Z` identique à `treeaicoach.__version__` crée une release stable :
  `git tag v1.3.0` puis `git push origin v1.3.0`.

## ⚖️ Mentions légales

TreeAI Coach isn't endorsed by Riot Games and doesn't reflect the views or opinions of Riot Games or anyone officially involved in producing or managing Riot Games properties. Riot Games, and all associated properties are trademarks or registered trademarks of Riot Games, Inc.

*TreeAI Coach n'est pas approuvé par Riot Games et ne reflète pas les opinions de Riot Games ni de quiconque
officiellement impliqué dans la production ou la gestion des propriétés de Riot Games. Riot Games et toutes les
propriétés associées sont des marques commerciales ou des marques déposées de Riot Games, Inc.*

Le code de TreeAI Coach est distribué sous [licence MIT](LICENSE). Les textures et icônes du jeu embarquées
dans `treeaicoach/assets` restent la propriété de Riot Games.
