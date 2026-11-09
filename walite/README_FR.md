# TreeAI Lite — version WeakAuras (pour GeForce NOW)

Sur GeForce NOW on ne peut pas copier de fichiers, donc pas d'addon TreeAI. Mais WeakAuras (WA) accepte
un **texte d'import** qui peut contenir du code. TreeAI Lite est un petit groupe WA, uniquement informatif.

## Installer (2 minutes)
1. Ouvre `release/wow/TreeAI-Lite-WeakAura.txt` et copie **tout** le texte (il commence par `!WA:2!`).
2. En jeu : active l'addon WeakAuras (bouton Addons de GeForce NOW), puis tape `/wa`.
3. Clique **Importer**, colle le texte, clique **Importer**. WA affiche un avertissement « code
   personnalisé » : c'est normal (le code est lisible dans `walite/*.lua`).
4. Déplace le groupe « TreeAI Lite » où tu veux (dans `/wa`, déverrouille et glisse).
   Si le collage est refusé sur GFN, colle en plusieurs fois (NVIDIA conseille ça pour les gros imports).

## Ce que ça affiche
- **Quêtes** (1 fois par seconde, rien en combat) :
  - `> devant à d. · 230 m · Rune gravée : 5/10` = l'objectif le plus proche, direction par rapport à ton
    personnage (devant / à gauche / derrière…) et distance en mètres ;
  - `À rendre (3)` puis la liste des quêtes finies dans un **ordre de rendu court** (plus proche voisin
    + 2-opt), avec la distance de chacune ; « lieu inconnu » si le jeu ne donne pas le lieu.
  - Positions lues dans le jeu (C_QuestLog.GetNextWaypoint + points de quête de la carte) : sans Questie,
    certains objectifs n'ont pas de point. Rien en donjon (le jeu y cache la position).
- **Chasseur** (seulement si tu es chasseur) : munitions basses (< 200, rouge < 50, emplacement vide)
  et familier « mécontent » / « content » avec rappel de nourriture (son régime).

Lecture seule : aucun clic automatique, aucune fonction protégée, rien dans le chat, valeurs « secrètes »
ignorées (famille Midnight). Pas de bloc « sacs/gris » dans cette version, pour rester léger.

## Ce qui n'est PAS vérifié (honnêtement)
- **WeakAuras sur WoW: Forever** : d'après Icy Veins et des fiches CurseForge, le WeakAuras normal ne
  marche pas (ou mal) sur Forever (restrictions de combat type Midnight). Il existe un portage,
  **Musca Auras** (WeakAuras 5.22.0 pour le client 1.60.1, mis à jour le 7/11/2026), qui importe les
  chaînes WA « comme d'habitude » et accepte le code perso. Nos auras n'utilisent ni journal de combat ni
  auras : elles devraient marcher hors combat — **non testé en jeu**.
- **Liste d'addons de GeForce NOW** : la page NVIDIA dit que WeakAuras est supporté et que les imports sont
  gardés entre deux sessions, mais je n'ai pas pu lire la liste actuelle (page inaccessible) : on ne sait
  pas si WA, ou Musca Auras, est proposé pour **Forever** sur GFN. À vérifier dans le bouton Addons.
- La chaîne est produite par `tools/build_walite.py` (format LibSerialize + DEFLATE + alphabet WA, recodé en
  Python d'après le code source de WeakAuras/LibSerialize/LibDeflate) et relue par notre décodeur ; elle n'a
  pas encore été importée dans un vrai client.
- Le code a tourné sur un faux client (test `tests/walite/mock_run.lua`), pas sur le vrai.

## Pour les développeurs
- Code des auras : `walite/quest_init.lua`, `walite/quest_trigger.lua`, `walite/hunter_trigger.lua`.
- Reconstruire : `python tools/build_walite.py` (vérifier : `--check`). Tests : `pytest tests/walite`.
- Détails WA retenus : `internalVersion` 82 (les migrations 83→90 ne touchent pas nos champs),
  déclencheurs « custom / stateupdate » sur événements, tick 1 Hz via `C_Timer.NewTicker` +
  `WeakAuras.ScanEvents("TREEAI_LITE_TICK")`, pas de `pcall` (bloqué dans le bac à sable WA).

Sources : icy-veins.com (no WeakAuras in WoW Forever), curseforge.com/wow/addons/musca-auras,
nvidia.custhelp.com « World of Warcraft – AddOns », github.com/WeakAuras/WeakAuras2 (Transmission.lua,
AuraEnvironment.lua, Modernize.lua), LibSerialize, LibDeflate.
