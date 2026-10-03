# Journal des versions

Les versions les plus récentes en premier. Les chiffres de détection viennent du banc de test
(`tools/det_gym.py`), mesurés avec les mêmes bibliothèques que l'exe (numpy 1.26 / OpenCV 4.10).

## Prochaine version (en préparation)

- **Rapport ARAM / Arène honnête :** l'appli n'analyse en direct que la Faille de l'invocateur. Dans les
  autres modes, le rapport le dit et se limite à tes statistiques, sans conseils de jungle, de ganks, de
  dragons ou de vision.
- **Plus de fuite entre deux parties :** le plan de jeu d'une partie n'est plus enregistré dans la partie
  suivante.

## 2.6.0 · 3 octobre 2026

- **Nouveau launcher, refait de zéro (Qt) :**
  - toutes les pages sont prêtes en environ 1 s au lieu de 4 à 6 s, et changer de page prend environ 25 ms ;
  - design épuré en clair et en sombre (suit Windows), barre latérale, listes groupées, vrais interrupteurs ;
  - pages : Accueil, Overlay, Alertes et voix, Analyse, Réglages, À propos (Ctrl+1 … 6).
- **Voix naturelle d'abord :**
  - si elle ne répond pas, la meilleure voix Windows prend le relais (OneCore, puis SAPI en dernier recours) ;
  - « Tester la voix » fait entendre la vraie voix choisie ;
  - prononciation française des champions et des mots du jeu (Kai'Sa, K'Santé, jungler, drake, 1v2…) ;
  - sons doux à la place des bips.
- **Overlay environ 25 % plus compact**, à la même échelle sur 15 formats d'écran (720p à 4K, ultra-large,
  16:10), et plus grand si tu agrandis l'interface du jeu.
- **Détection et capture :**
  - la capture rapide (DXGI) ne se coupe plus pour toute la partie après un écart passager ;
  - la minimap n'est plus recherchée en boucle quand elle est seulement cachée (22 → 6 recherches sur 90 s) ;
  - ta position n'est plus prise sur ton jungler quand la caméra le suit pendant que tu es collé à ton ADC.
- **Alertes :**
  - une même alerte n'est plus enregistrée ni affichée plusieurs fois de suite ;
  - le rapport compte le « Recule ! » dit avant une mort.

## 2.5.1 · 3 octobre 2026

- Deux fois moins de mauvais noms sur les champions empilés, beaucoup moins de faux cercles sans nom.
- Ta position ne reste plus figée quand tu débloques la caméra.
- Un champion mort, ou caché sous un autre, n'est plus dessiné au mauvais endroit.
- Détection environ 14 % plus rapide (30 → 26 ms par image).

## 2.5.0 · 2 octobre 2026

- Corrections tirées de tes vraies parties :
  - plus de « Swain vu à deux endroits » quand tu es mort ;
  - duo empilé suivi ;
  - bonne taille d'icônes dès le début ;
  - plus de « minimap introuvable » au chargement ;
  - skins chroma reconnus (Kindred n'était presque jamais identifié).
- Launcher sans bug de chargement ; moins d'impact sur les images par seconde du jeu.

## 2.4.0 et 2.4.1 · 2 octobre 2026

- **Compréhension de la partie :** compositions, qui est plus fort et jusqu'à quand, conditions de victoire
  et ton rôle.
- **Ce que tu oublies :** point de compétence, potion, balises, or non dépensé, sort prêt à ta mort.
- IA corrigée (22 bugs). Trajets logiques du jungler ennemi activés (2.4.1).

## 2.3.0 · 1er octobre 2026

- Conseils qui changent la partie, avec la voix ; conseils sur les achats ennemis.
- Placement propre, sans rien sur l'interface du jeu.
- Santé TreeAI : l'appli se surveille et se répare seule.
- Données du jeu revérifiées.

## 2.2.0 et 2.2.1 · 1er octobre 2026

- Timers sur la minimap, cercles fins sur les ennemis visibles, mode économie d'énergie de Windows
  désactivé (fini les saccades).
- Conseils corrigés : une seule consigne de balise, achats que tu peux payer.

## 2.1.0 · 1er octobre 2026

- Détection plus fiable, messages réécrits, combat d'équipe distinct d'un gank.

## 2.0.0 · 1er octobre 2026

- Panneau en jeu épuré (une seule consigne, rien quand il n'y a rien à dire), bip immédiat pour les
  dangers, alerte 2 contre 1 et peu de vie.
- Nouvelle interface, données 2026 (plus d'Atakhan, Baron à 20:00), carte de sélection des champions.
