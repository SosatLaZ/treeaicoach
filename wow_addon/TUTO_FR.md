# TreeAI pour WoW Forever — démarrage rapide

## Installer

- **Automatique** : lance TreeAI Coach, page **WoW Forever** → **Installer l'addon** (il trouve ton dossier WoW ;
  sinon **Parcourir**). Relance le jeu : un nouvel addon n'apparaît qu'après un redémarrage de WoW.
- **À la main** : dézippe `TreeAI-addon.zip` dans `…\World of Warcraft\_forever_\Interface\AddOns\` (tu dois
  avoir `…\AddOns\TreeAI\TreeAI.toc`), relance le jeu, coche **TreeAI Coach** dans la liste des AddOns.
- Tes amis installent le même addon pour se voir en groupe.

## Les 3 choses à savoir

1. **Tout part de la pastille de la minimap** (le bouton rond au bord de la carte) : clic = la **fenêtre TreeAI**
   (Maintenant, Quêtes, Trajet, Groupe, Options…), clic droit = le **menu rapide**. La première fois, une
   **visite guidée** de 60 secondes montre où cliquer ; elle se rejoue depuis le menu (« Visite guidée »).
2. **Ta prochaine étape** est sur la carte « Prochaine étape » et la **boussole** la vise (distance, temps de
   trajet). Clic sur une ligne = l'action ; survol = le pourquoi ; clic droit sur un conseil = caché 2 h.
3. **TreeAI conseille, tu joues** : rien ne part sans ton clic (règles de Blizzard), rien en combat.

## Le menu de la pastille

Clic droit sur la pastille : le menu rapide.

| Entrée | À quoi ça sert |
|---|---|
| Maintenant, Quêtes, Groupe, HV, Infos… | ouvre cette page dans la fenêtre TreeAI |
| Recherche, Meilleur familier | ce qui vaut le détour près de toi, la meilleure bête à apprivoiser |
| Macros | les macros prêtes pour ta classe : un clic pour les créer, puis glisse-les sur ta barre |
| Fenêtre « À faire » | toutes tes quêtes, triées, à épingler |
| Boussole, Radar | les afficher ou les cacher |
| Options | taille, transparence, sons ; une page par fonction |
| Fonctions bêta (n/m) | la liste des fonctions bêta : un clic pour en allumer ou en couper une |
| Erreurs, Blocages | préparer le fichier d'erreurs ; les actions refusées par le jeu (doit rester à 0) |
| Panneau, Déplacer les cadres, Réinitialiser | afficher / cacher ; glisser (molette = taille) ; tout remettre en place |
| Visite guidée | revoir où cliquer |

Une entrée n'apparaît que si sa fonction est là. Sans la pastille : `/tai menu`.

## « Bêta », ça veut dire quoi ?

TreeAI démarre en **mode sûr** : seules les fonctions solides tournent (quêtes, boussole, trajet, course au
corps, macros, panneau, erreurs). Les autres — chasseur (familier, munitions), groupe, HV, zone de travail,
raccourcis… — sont **bêta** : testées hors du jeu seulement, elles peuvent se tromper. Elles sont coupées ; menu →
**Fonctions bêta**, un clic pour en essayer une (pas besoin de /reload). « Revenir au mode sûr » remet tout comme
au début.

## Signaler un bug

1. En jeu : menu → **Erreurs** (ou `/tai erreurs`).
2. Tape `/reload` : le jeu sauvegarde et TreeAI Coach écrit le fichier.
3. Envoie `%APPDATA%\TreeAICoach\TreeAI-erreurs.txt` (TreeAI Coach, page WoW Forever → **Erreurs WoW : ouvrir
   le fichier**), avec une phrase sur ce que tu faisais.

## FAQ

**Ça lague ?** Reste en mode sûr : c'est le plus léger. Coupe les fonctions bêta dont tu ne te sers pas (menu →
Fonctions bêta). `/tai perf` dit ce qui coûte le plus. Si ça saccade encore, envoie le fichier d'erreurs.

**« TreeAI a été bloqué » ou « Échec des actions d'interface » ?** Clique « Ignorer », puis menu → **Blocages** :
la liste doit être vide. Sinon, envoie le fichier d'erreurs : chaque blocage y figure avec la fonction en cause.
TreeAI ne modifie jamais les fenêtres de Blizzard et n'agit pas en combat.

**Et Suivi Forever (SPF) ou Questie ?** Par défaut, **TreeAI guide seul** : sa boussole et ses étapes. Questie garde
ses icônes, et TreeAI lit sa base pour savoir où sont tes objectifs. Tu préfères la flèche de Suivi Forever ?
Options → **Guidage** (ou `/tai guidage forever`) : SPF guide, TreeAI cache sa boussole et ses lignes en double.
Jamais deux flèches à la fois.

**Mes macros ?** Menu → **Macros**, hors combat : aperçu, un clic pour créer, puis glisse-les sur ta barre. Elles
s'appellent « TAI … » ; une macro que tu as modifiée n'est jamais écrasée. Plus de place (120 compte, 30 perso) :
supprime-en via Échap → Macros, ou prends la fenêtre copier-coller proposée. Une touche = une action, comme le
veut Blizzard.

**L'HV dit « Aucun achat-revente sûr pour l'instant » ?** C'est normal au début : TreeAI ne propose un achat que
s'il a la preuve que l'objet se revend (3 jours d'historique, plusieurs scans espacés de quelques heures, au moins
3 vendeurs, un prix stable). Fais un **scan complet** à chaque passage (Blizzard : une fois par 15 min) et survole
la ligne pour le pourquoi. L'HV est une fonction bêta : menu → Fonctions bêta.

**L'addon n'apparaît pas ?** Vérifie le chemin `…\AddOns\TreeAI\TreeAI.toc`, relance le jeu (un /reload ne suffit
pas) et coche « Charger les add-ons périmés » si besoin. En jeu, `/tai` montre où est la pastille.
