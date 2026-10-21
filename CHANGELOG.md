# Journal des versions

Les versions les plus récentes en premier. Les chiffres de détection viennent du banc de test
(`tools/det_gym.py`), mesurés avec les mêmes bibliothèques que l'exe (numpy 1.26 / OpenCV 4.10).

## 2.14.0 · 21 octobre 2026

- **Lancement rapide :** TreeAICoach.exe devient un petit lanceur (7,5 Mo) qui installe l'appli une fois sur ton PC
  et la démarre directement ; mises à jour en un clic, seulement ce qui a changé ; plus de limite de taille.
- **« Hey Tree AI » dans le vocal Discord :** le bot entend n'importe quel membre, joue un petit son, puis répond dans
  le salon. Bouton pour faire passer tout le son par Discord, tout sur ton PC, ou les deux.
- **Matchup de chaque ami** du vocal au début de la partie ; bot plus stable en fin de partie.
- **Barre de build** : 6 icônes (achetés cochés, prochain entouré de vert avec l'or qui manque), à la base / mort /
  touche détails. Couleurs de l'overlay plus douces.
- **IA : 1000 requêtes par jour** (au lieu de 15 par partie).

## 2.13.0 · 21 octobre 2026

- **Plus de minimap en double :** le radar est supprimé ; les repères restent sur ta vraie minimap.
- **Capture de la fenêtre du jeu seule** (Windows 11) : rien de ce qui est affiché par-dessus n'est analysé.
- **Partie miroir** (toi Yone contre Yone) : l'équipe vient de la couleur du contour, le Yone d'en face ne peut plus
  être pris pour toi.
- **Conseils à la pièce d'or :** « Il te manque 137 PO pour Estropieur : 8 sbires (2 vagues) », course aux niveaux
  exacte (« niveau 6 contre 5 »).

## 2.12.0 · 21 octobre 2026

- **Détection : l'overlay ne se voit plus lui-même.** Sur tes captures, 59 % des images analysées contenaient nos propres
  anneaux et étiquettes « JGL » (faux ennemis, mauvais jungler). Toutes les fenêtres sont cachées des captures et
  vérifiées en continu ; si Windows ne le permet pas, rien n'est dessiné sur la minimap (repères à côté) et nos
  dessins sont effacés avant l'analyse.
- **Jungler ennemi = celui qui a Châtiment**, toujours ; personne en ARAM / Arène.
- **Assistant vocal « Hey Tree AI »** (désactivé par défaut, Alertes et voix) : pose ta question à voix haute, l'IA
  répond en 1-2 phrases. Reconnaissance sur ton PC ; modèle téléchargé une fois (42 Mo).
- **Calls d'équipe :** « Ils sont 4 bot : Baron maintenant », dragon libre avec sa fenêtre (« libre 22 s »),
  « 3 morts : Baron maintenant », « 5 morts : poussez mid ». Envoyables au vocal Discord (option « Calls d'équipe »).
- **Carte d'achat visible :** aussi en mode détaillé et après un achat vu dans ton inventaire, plus grande à la
  boutique avec un halo vert ; interrupteur « Achats ».
- **Infos des joueurs** (désactivé par défaut) : rang, LP, série, parties sur leur champion, par champion, une fois la
  partie lancée ; lu dans ton client LoL, gardé en mémoire pendant la partie seulement.

## 2.11.0 · 20 octobre 2026

- **Le bot Discord annonce aussi les ganks de tes amis :** il reconnaît qui, dans le vocal, joue quel champion de ton
  équipe (pseudo Discord comparé au Riot ID, corrigeable à la main sur la page Discord, section « Dans le vocal »),
  et dit « Bot gank » / « Top gank »… pour la voie de l'ami visé (un seul appel pour le duo bot). Option « Dire le
  pseudo » (« Paul, bot gank »). Ta voix sur le PC n'annonce que tes propres ganks.

## 2.10.1 · 20 octobre 2026

- **Captures de minimap activées par défaut** (une toutes les 10 s, 600 par session au plus, sur ton PC) : chaque partie
  devient un vrai cas de test, recalé sur les vraies positions du client LoL. Envoie-les avec Réglages → Maintenance et
  support → Créer le zip. Désactivable au même endroit.

## 2.10.0 · 20 octobre 2026

- **Voix naturelle réparée** (bot Discord ET voix du coach sur le PC) : Microsoft refusait l'ancienne version, tout
  retombait sur la voix Windows. Si elle retombe en panne : voix Windows, puis phrases enregistrées dans l'appli ;
  jamais le bip seul.
- **Bot Discord :** volume réglable (plus bas par défaut, bip sous la voix), et options désactivées par défaut :
  « Dragon / Baron / Larves dans 30 secondes », « Jungler ennemi vu … », résumé de fin, couper la voix du PC quand le
  bot parle, touche pour faire taire le bot.
- **Objet suivant mis en avant en jeu :** à la base (ou quand tu as l'or), la carte devient une carte d'objet avec son
  icône, « Achète … » ou le composant que ton or paie maintenant, le prix et le taux de victoire, bordure verte quand tu
  peux l'acheter. Icônes des objets, sorts et runes à la sélection des champions et dans le rapport.
- **Build de la méta d'abord :** les objets de contre ne passent plus avant ton build, sauf contre un ennemi très
  avancé ; données moins bruitées, patch 26.20.

## 2.9.0 · 20 octobre 2026

- **Page Discord dans le launcher :** l'état du bot en direct (avec la vraie raison d'une erreur), les étapes cochées
  (token, serveurs où le bot est invité, ton salon vocal, droits Se connecter / Parler, test de voix), les salons
  vocaux de chaque serveur avec qui est dedans, les boutons Rejoindre / Quitter, et le journal du bot.
- **Le bot te suit :** il rejoint ton salon vocal dès qu'il t'y voit et le quitte avec toi ; chaque échec de connexion
  au salon dit sa cause exacte.

## 2.8.1 · 20 octobre 2026

- **Bot Discord :** connexion plus robuste (autre magasin de certificats si celui de Windows refuse Discord,
  proxy de Windows utilisé), et le statut affiche maintenant la vraie raison quand la connexion échoue.

## 2.8.0 · 20 octobre 2026

- **Bot Discord** (désactivé par défaut, page Discord) : lancé par l'appli, il rejoint ton salon
  vocal et dit seulement « Top gank », « Mid gank » ou « Bot gank » après un bip, et le matchup au début de la
  partie en une phrase de l'IA (favorable ou pas). Chiffrement vocal Discord (DAVE) pris en charge.
- **Détection sur tes vraies captures botlane :** Kayn reconnu dans ses formes (aussi Méga Gnar, Valor de Quinn),
  plus de faux cercle sur le bouton rouge du coin de la minimap. Noms justes 89 → 98 %, cercles justes 88 → 96 %.
- **Nouveau logo** : l'arbre qui voit.
- **IA : 15 appels par partie** au lieu de 10.

## 2.7.0 · 20 octobre 2026

- **Builds de la méta :** à la sélection des champions, le build du moment pour ton champion et ton rôle (objets de
  départ, objets principaux dans l'ordre, sorts d'invocateur, runes, ordre des sorts), d'après les taux de victoire
  des parties classées (U.GG, rafraîchi une fois par jour et par patch ; runes et sorts officiels de Riot si U.GG ne
  répond pas). En jeu, l'objet suivant avec son taux de victoire, et « Rentre acheter … » quand tu as l'or. Nouvelle
  section du rapport : « Ton build face à la méta ».
- **Capture rapide réparée sur les portables à deux cartes graphiques :** l'appli essaie chaque carte et chaque
  écran, en commençant par celui qui affiche le jeu ; une image noire fait passer au suivant au lieu de revenir à la
  capture lente pour toute la partie. Nouvelle ligne « Capture d'écran » dans Accueil → Système.
- **Banc de test sur tes vraies parties :** 40 vraies captures de minimap de ta partie en Tahm Kench, recalées sur
  les vraies positions du client LoL. Chaque changement de détection est maintenant vérifié dessus.

## 2.6.2 · 9 octobre 2026

- **Tout envoyer en un seul fichier :** **Réglages → Maintenance et support → Créer le zip** rassemble tes
  captures de minimap, tes parties et leur vérité (fichiers `.truth.json`) et tes derniers diagnostics dans
  un seul `.zip`, puis ouvre le dossier. Plus besoin d'envoyer les fichiers un par un.

## 2.6.1 · 9 octobre 2026

- **Ta position ne saute plus à travers la carte.** Une position de toi que tu ne peux pas avoir atteinte
  à pied est refusée tant qu'elle n'est pas confirmée (au moins 3 reconnaissances sûres de ton champion sur
  1,5 s, par exemple à l'arrivée d'une Téléportation). Ta fontaine reste toujours possible (rappel,
  réapparition). Sur tes vraies parties en Swain, ta position sautait 77 fois en 15 minutes quand la caméra
  regardait ailleurs pendant que tu étais collé à ton ADC. Sur le banc de test qui reproduit ce cas, ta
  position est fausse dans 2 % des images au lieu de 51 %, et les mauvais noms passent de 194 à 23.
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
