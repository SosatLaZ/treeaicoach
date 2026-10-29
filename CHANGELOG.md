# Journal des versions

Les versions les plus récentes en premier. Les chiffres de détection viennent du banc de test
(`tools/det_gym.py`), mesurés avec les mêmes bibliothèques que l'exe (numpy 1.26 / OpenCV 4.10).

## 3.5.0 · 29 octobre 2026

- **L'IA ne refuse plus jamais ta question :** plus de « L'IA réfléchit déjà », ta question passe avant tout.
- **« Hey coach » beaucoup mieux reconnu :** le premier moteur entendait « écoute » au lieu de « hé coach » ;
  Whisper relit maintenant ces phrases (en test : 148/180 au lieu de 57/180 au micro, 154/180 au lieu de 54/180
  sur Discord, sans faux réveil sur ce test).
- **Whisper ne ralentit plus le jeu :** 2 cœurs max, en priorité basse.
- **Carte graphique pour la minimap** (option, Réglages → Avancé → Performance).
- **Ta position quand tu es collé à ton ADC** bien trouvée (Seraphine 91 % → 94 %, 0 fausse détection) ;
  plus de saut impossible de ta position.

## 3.4.1 · 29 octobre 2026

- **Le bot répond en quelques secondes au lieu de ~2 minutes :** les délais s'additionnaient (file Whisper,
  réponse IA retenue pendant les combats, file Discord). Ta question passe en premier, rien ne reste en file
  plus de 3 s, la réponse commence par la première phrase. Temps de chaque étape écrit dans le journal.
- **« Hé coque » compris comme « hé coach ».**
- **Écran de chargement :** un petit badge sur la carte de chaque joueur (rang, OTP / 1re partie / série /
  main banni, ton matchup) ; plus aucun panneau en partie.

## 3.4.0 · 28 octobre 2026

- **Le bot ne se réveille plus tout seul :** un « Hey coach » n'est accepté que s'il est confirmé (deuxième
  moteur ou Whisper) ; un réveil sans question est ignoré, sans bip.
- **Plus jamais la voix Windows :** la voix naturelle ou rien (le message reste à l'écran).
- **Écran de chargement :** rang, mains, « 1re partie / OTP », 5 dernières parties de chaque ennemi, « son main
  a été banni », les 5 matchups et le plan (avec « Rangs des joueurs »).
- **Lanceur plus simple et plus rapide :** 6 pages au lieu de 8, options rares repliées, fenêtre ~2× plus vite.
- **Ménage :** bouton « Faire le ménage » (Réglages → Avancé → Stockage), beaucoup moins de captures,
  zip d'envoi ~4 Mo au lieu de ~30 Mo.
- **Détection :** allié manquant retrouvé par élimination, icône au coin de la fontaine, clones (Shaco,
  LeBlanc, Wukong, Neeko), captures HDR délavées, identités qui ne s'échangent plus entre équipes.

## 3.3.0 · 28 octobre 2026

- **Beaucoup moins de bruit :** sur une vraie partie de 40 min, 127 messages au lieu de 228 et 42 bips au lieu
  de 67. Mort, en base, en boutique ou en combat : seuls les dangers passent. Félicitations max 1 / 5 min.
- **Bordure rouge discrète** (fine, une pulsation, jamais en boutique ni mort) ; l'ancienne reste au choix.
- **L'IA répond librement à tes questions en partie** (n'importe quel sujet), sans faire de bilan.
- **Rangs justes :** « Platine 2 · 54 % (37 parties) » sur la file de ta partie, « Non classé » sinon.
- **Rapport d'analyse :** Ctrl+F8 ou Avancé → Maintenance, un petit zip à m'envoyer pendant une partie.
- **Niveaux :** plus aucune affirmation basée sur un niveau ennemi périmé ; textes au bon genre (« elle »).
- **Voix d'homme naturelle (Rémy)** par défaut, voix plus douce hors danger.
- **Moins de charge PC** quand tu es mort, en base ou en chargement (environ moitié moins).
- **Détection :** icônes coupées par le coin de la carte à la fontaine ; carte graphique (DirectML) prête en option.

## 3.2.0 · 28 octobre 2026

- **Moins de fausses alertes de gank :** une seule alerte par menace (plus de doublons « arrive » + « recule »
  + « collapse »), « X vient vers toi » seulement s'il continue d'approcher. Environ 30 % d'alertes en moins,
  et les annonces sont justes 8 fois sur 10 (au lieu de 3 sur 4).
- **Noms LoL mieux compris à la voix :** Kai'Sa, Lame d'infini… corrigés (74 % → 84 % justes en test).
- **F12 :** l'enregistrement dure tant que tu tiens la touche, bip au début et à la fin.

## 3.1.1 · 28 octobre 2026

- **Whisper démarre enfin sur Windows :** il plantait à chaque lancement (module manquant), toute la voix
  passait par l'ancien moteur. La raison d'un échec est maintenant affichée dans « Écoute ».
- **Plus de « Patiente » :** tu peux reposer une question à l'IA tout de suite.
- **Détection :** 3 nouvelles parties réelles en test ; ta position sous ton ADC mieux trouvée.
- **Alerte « jungler disparu »** un peu plus tôt (35 morts prévenues au lieu de 32 sur 36 parties).

## 3.1.0 · 28 octobre 2026

- **Surbrillance dans la boutique :** un anneau vert autour du prochain objet à acheter quand la boutique
  est ouverte (à toute résolution). Le set TreeAI arrive en premier dans l'onglet « Sets d'objets ».
- **Détection des champions :** les icônes à moitié cachées sous une autre sont reconnues ; sur tes parties
  réelles, 0 fausse détection et 0 erreur de nom (bot lane : 89 % des icônes trouvées).
- **Ta position :** plus fiable après une coupure de capture (erreurs 10 → 4 sur 32 cas réels).
- **Minimap trouvée sur les écrans gris** (mort, HDR) : 14/30 → 30/30 ; moins de fausses balises.
- **Mort / vivant lu sur le portrait** sans se tromper avec le compteur de mort rouge.

## 3.0.0 · 27 octobre 2026

- **Plus de chiffres périmés :** le jeu donne le niveau d'un ennemi tel qu'il était quand on l'a vu pour
  la dernière fois ; le coach l'estime maintenant quand il est caché depuis 30 s. Chaque phrase est
  revérifiée juste avant d'être dite ou affichée, et jetée si un fait a changé (niveau, mort, timer).
- **Mode rapide (Swiftplay) corrigé :** plus de larves ni de Héraut, Baron à 12:00, Ancien à 15:00
  (avant : objectifs annoncés après qu'ils soient déjà pris, 9 fois sur 181 → 0).
- **Un seul conseil quand les moteurs se contredisent** (danger > combat > objectif > plaques > macro).
- **Primes :** « Vise Caitlyn en premier : grosse prime (série de 4) », « Rentre dépenser tes 1 400 PO :
  ta tête vaut une prime ».
- **Balise de contrôle avant Dragon / Baron**, pas de conseils macro en URF / Un pour tous / entraînement.

## 2.29.2 · 27 octobre 2026

- **Le bot Discord marche hors des parties :** il reste dans ton salon vocal et t'écoute tout le temps (il
  n'écoutait que pendant une partie et quittait le salon à la fin).
- **Phrases moins coupées :** une pause d'une seconde ne coupe plus ta phrase, les questions peuvent durer
  12 s (au lieu de 6 s).

## 2.29.1 · 24 octobre 2026

- **Plus de « l'IA a besoin d'une partie » :** hors partie, F12 / « Hey coach » répond comme une IA normale
  (toujours experte LoL), à voix haute sur ton PC.

## 2.29.0 · 24 octobre 2026

- **L'IA t'écoute, toi :** touche **F12** = parler (appuie, pose ta question, réponse aussitôt), micro du PC
  rallumé, sans attendre « Hey coach ». Sur Discord, le bot n'écoute plus que ta voix (« Seulement moi »).

## 2.28.0 · 24 octobre 2026

- **Ganks mieux annoncés :** nouvelle alerte « Recule vers ta tour : Lee Sin a disparu près de toi » quand
  le jungler sort de la carte tout près de toi. Sur 31 parties : 102 alertes justes au lieu de 25.
- **Moins de « Recule » pour rien :** « jungler invisible » seulement s'il peut vraiment arriver
  (46 → 13 conseils, faux 30 → 4) ; « Recule ! » en combat seulement avec au moins 2 ennemis visibles.
- **Même champion dans les deux équipes :** l'adversaire qui joue ton champion n'est plus pris pour toi,
  chaque copie a sa propre mort (détection 0,82 → 0,89 sur ces parties).
- **Conseils mieux ordonnés :** « Plaque la tour : Darius est mort » passe avant « rentre » ; deux rappels
  discrets ne se remplacent plus en quelques secondes.

## 2.27.0 · 24 octobre 2026

- **Discord, cerveau d'équipe :** annonces pour chaque pote, par son champion : « Ahri, ton adversaire est en
  base : plaque », « Jinx, jungler probablement chez toi », « Jinx et Thresh, rentrez ensemble », et une ligne
  d'équipe avant l'objectif (« Dragon dans 1:00 : bot et jungle, rentrez maintenant »). Au plus une phrase par
  pote toutes les 30 s, les ganks passent toujours en premier.
- **Les 3 moments décisifs** de la partie dans le rapport (et le récap Discord) : ce qui s'est passé et quoi
  faire à la place, avec la minute.

## 2.26.0 · 24 octobre 2026

- **Focus du soir :** d'après tes parties, la condition qui sépare le plus tes victoires de tes défaites
  (ex. présent à ≥ 40 % des objectifs : 9/13 gagnées contre 2/12). Une seule par partie, suivie en direct
  dans la barre du haut quand elle est en danger, bilan dans le rapport.
- **Bilan des conseils :** chaque conseil est vérifié après la partie (suivi ? et ensuite ?) ;
  « Conseils utiles : x/y » dans le rapport ; ceux que tu ignores toujours se font plus rares (jamais les
  alertes de danger). Remise à zéro dans Avancé → Analyse.

## 2.25.0 · 24 octobre 2026

- **Plus de limite de requêtes IA :** le nombre de requêtes du jour est seulement affiché (Réglages → Conseils IA).
- **Coach IA aussi assistant normal :** hors partie, « Hey coach » répond à toute question simple (toujours
  expert LoL : champions, objets, runes, matchups). En partie, il reste centré sur la décision du moment.

## 2.24.1 · 24 octobre 2026

- **Correctif urgent :** le bot Discord ne démarrait plus en 2.24.0 (erreur de chargement). Réparé, et chaque
  version vérifie maintenant que tous les modules se chargent avant d'être publiée.
- **Objectifs préparés :** « Rentre maintenant : Phage et retour pour le Dragon (1:30) », « Ne rentre pas :
  Dragon dans 0:35 », « Ne prends aucun risque : mourir = Dragon perdu » (sur tes parties : mort juste avant
  = objectif perdu 4 fois sur 6).

## 2.24.0 · 24 octobre 2026

- **Coach IA par Discord uniquement :** le micro du PC n'est plus jamais ouvert. « Hey coach » est écouté
  dans le salon vocal Discord (toi compris), transcrit par Whisper en local, réponse par l'IA (Groq).
- **Où est le jungler :** « JGL 70 % bas » dans la barre du haut et zones probables légèrement teintées sur
  ta minimap quand il est caché (vérifié sur 20 parties : bonne moitié de carte 81 % du temps, 77 % avant).
- **« Après Phage tu gagnes l'échange : back à 1 100 or »** quand ton prochain objet te fait passer devant.

## 2.23.0 · 24 octobre 2026

- **Builds corrigés :** on lisait les stats Master+ au lieu d'Émeraude+ (Garen recevait Youmuu au lieu de
  Danse fantôme, Swain Embrasement au lieu d'Épuisement, Seraphine montait W au lieu de A). Vérifié contre
  U.GG et OP.GG sur 6 champions. Objets situationnels dans l'ordre le plus joué ; pas de build Faille en ARAM.
- **Prochain objet façon OP.GG :** icône + barre d'or + « 450 PO » (ou « Achetable ») dans la barre du haut.
- **Brouillard décodé :** « Lee Sin est à la base, 25 s sans gank », « Darius est en base : pousse et plaque »,
  calcul du combat avant un objectif (« Force le dragon : 5v4, +3,2k or » / « Laisse le dragon »).
- **Victoire % plus juste** (niveaux d'équipe ajoutés), aperçu Overlay au nouveau design, tests stabilisés.

## 2.22.0 · 24 octobre 2026

- **Discord transcrit avec Whisper** (local, gratuit) pour toi et chaque pote, avec les champions de la partie
  en vocabulaire. Un seul moteur pour tout le monde : ~0,7 s par question, ~5 % d'erreurs de mots.
- **Micro muet en partie :** l'appli essaie les autres micros et prend celui qui t'entend.
- **Conseils croisés :** ton matchup (draft) et le rang de ton adversaire ajustent la fenêtre de kill ;
  « Farme sans risque » quand il gagne cette phase.
- **Le coach apprend :** chaque alerte de gank est vérifiée après la partie avec le vrai trajet du jungler ;
  le rapport donne le taux de réussite et le seuil s'ajuste tout seul, lentement.
- **Pages Alertes et Discord** au nouveau design ; code mort supprimé.

## 2.21.1 · 23 octobre 2026

- **Discord t'entend enfin :** le bot ignorait ta voix quand l'assistant du PC était activé (pour ne pas
  répondre deux fois). Maintenant il t'écoute toujours ; si le PC et Discord entendent le même « Hey coach »,
  une seule réponse.

## 2.21.0 · 23 octobre 2026

- **Reconnaissance vocale refaite :** Whisper en local (gratuit, aucune requête), téléchargé au 1er usage ;
  découpage des phrases par Silero VAD ; « Hey coach » plus tolérant.
- **Micro :** choix automatique du micro qui capte, alerte claire si Windows bloque le micro.
- **Nouveau design sobre** « graphite » : plus de dorures ni d'effets, une couleur d'accent, lancement < 0,8 s.
- **Options rangées :** une seule place par réglage, sous-options cachées si inutiles, libellés clairs.
- **Rangs sur Discord :** au début de la partie, message avec les rangs et séries + 3 annonces vocales.

## 2.20.0 · 23 octobre 2026

- **L'essentiel en haut de l'écran :** une petite barre (victoire %, or vs ton adversaire, CS/min, prochain
  objectif, dernier jungler vu), au seul endroit toujours vide du HUD. Redessinée seulement quand elle change.
- **« Hey coach » réparé sur Discord** (fins de phrase perdues quand quelqu'un se tait) et plus tolérant
  (« et coach », « coach »…). Micros faibles amplifiés, son plus propre.
- **Test micro 5 s :** montre ce que l'appli entend et le niveau du micro. Compteurs audio sur la page Discord.

## 2.19.0 · 23 octobre 2026

- **En partie (données du jeu) :** écart d'or exact sur ton adversaire, fenêtre de kill, back au bon moment
  (« Rentre acheter Phage : 1312 or »), défense adaptée (« 70 % AP : Mercure »), victoire % en direct avec la
  raison, Baron/Dragon quand ils sont morts, CS/min vs objectif de ton rang.
- **Sélection :** condition de victoire de la compo, qui gagne la lane et quand, 1er objet ajusté à la compo
  ennemie, verdict des 5 voies (aussi pour tes potes sur Discord), départ et 1er gank du jungler ennemi, anti-tilt.
- **Après-partie :** écarts à 10/15 min, timing d'objets vs méta et or qui dort, carte de toutes tes morts,
  profil 6 axes sur 10 parties, conseils de pool de champions, tournant de la partie, récap d'équipe Discord
  (désactivé par défaut).

## 2.18.0 · 23 octobre 2026

- **Séquence de lancement** courte (≈ 1 s, passable) avec les vraies vérifications : minimap, client LoL, voix, overlay.
- **Accueil façon LoL :** image du champion de ta partie (ou de la dernière), ligne « TOP · GAREN · EN PARTIE », fins
  coins dorés.
- **Proportions revues** (grille régulière, titres plus grands, chiffres en police à chasse fixe).

## 2.17.1 · 23 octobre 2026

- **Windows 10 : nos dessins sont retirés exactement de l'image analysée** quand Windows refuse de cacher l'overlay
  (sur ta partie Garen avec nos marques : détection 0,83 → 0,85, comme sans marques).
- **Option « Capture de la fenêtre du jeu (cadre jaune Windows 10) »** dans Avancé.
- Plus d'erreur répétée dans le journal.

## 2.17.0 · 23 octobre 2026

- **Nouvelle interface saphir :** arbre de vie discret en fond, bleus saphir, police Inter, style épuré ; 8 pages
  (nouvelle page Avancé) ; icône saphir.
- **« Hey coach » plus fiable :** 47 sur 48 détectés, 0 faux déclenchement ; « Coach, … » marche aussi. Ligne
  « Écoute » (niveau du micro, derniers mots entendus) et bouton de test hors partie ; même chose par membre sur la
  page Discord.
- **Vocal Discord :** chaque membre marqué « joue … », « joue en face … » ou « ne joue pas ».
- **Bandeau** réduit à une ligne ; ligne du jungler sur la carte.
- **Voix ElevenLabs** (accent africain possible, avec ta clé).
- **Captures** toutes les 30 s + une capture de toute la fenêtre toutes les 2 min environ.

## 2.16.0 · 23 octobre 2026

- **Jungler ennemi suivi :** sa dernière position + « JGL 35 s » restent sur la minimap 150 s ; ligne « Lee Sin · vu
  il y a 25 s, rivière du bas » sur la carte ; prévision du premier gank (fin de son premier tour de jungle).
- **IA :** plus de plans automatiques ; elle répond quand tu demandes, et d'elle-même seulement quand tu es mort ou
  juste après ton kill sans ennemi proche. Réponses en 1 phrase avec un fait exact.
- **Boutique :** le build de la méta est écrit dans l'onglet « Recommandés » du jeu ; boutons pour importer les runes
  et mettre les sorts ; méta adaptée à ton rang.
- **Conseils de balise plus courts**, le jungler nommé.

## 2.15.0 · 22 octobre 2026

- **« Hey coach »** remplace « Hey Tree AI » : reconnaissance bien plus fiable (23 sur 24, aucun faux déclenchement
  sur 80 phrases de test).
- **Voix québécoise** par défaut (Sylvie ; Antoine, Jean, Thierry au choix).
- **Analyse ARAM :** KDA, participation, morts trop tôt ou isolées, objets face à leur compo (anti-soin, résistances),
  3 conseils ; en jeu : achat à la mort, « rejoins ton équipe », « recule » quand deux alliés tombent.

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
