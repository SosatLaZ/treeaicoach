# TreeAI V5 — « 24 h de jeu sans rien demander » (plan, 9 nov 2026)

Objectif : une version stable, rapide, claire, qui guide seule un chasseur 30+ en groupe de 4, sans bug ni freeze.

## A. Stabilité (bloquant)
- Zéro freeze : aucun calcul lourd dans un événement de combat / journal ; tout gros calcul découpé (≤ 2 ms/image),
  `/tai perf` vert sur 1 h simulée en groupe ; aucun cadre qui clignote ; aucune erreur Lua sur 4 h simulées.
- Une seule vérité « quoi faire » (trajet = boussole = boutons = fenêtre), cible stable, zone choisie respectée.
- Fichier d'erreurs propre par version ; autotest au démarrage visible en une ligne.

## B. Quêtes (cœur)
- Prises confirmées par le journal ; quêtes fantômes Questie apprises et retirées ; jamais d'objectif d'une quête
  non prise ; prises sur le chemin ; « Monter vite » ; XP/min réelle sur chaque quête (vertes pénalisées).
- Ascenseurs / tram mesurés (jamais inventés) ; bateaux « part dans » ; chemins appris partout.
- Étapes façon Suivi Forever (temps par étape), SPF alimenté si présent ; chapitres d'histoire prouvés.

## C. Actions rapides « de malade » (menu refait)
- Barre d'actions directes : Ranger · Vendre (sûr) · Réparer · Courses · Rendre · Prendre · Monter vite · Groupe ·
  Familier · Métiers — un clic = l'action (dans les règles : une action protégée par clic).
- Menu : 11 catégories, chaque page = 3 gros boutons en haut, rien à lire pour agir ; recherche d'actions.

## D. Inventaire / vente / HV
- Jamais vendre ce qui sert (consommables, compos du plan, quêtes, meilleur équipement) ; stock cible potions /
  nourriture / munitions / bandages + achats auto-proposés ; banque « sortir à vendre » ; HV liée aux métiers ;
  onglet natif dans l'HV ; bénéfices honnêtes.

## E. Équipement / groupe
- Meilleur stuff atteignable (quêtes, donjons, HV, artisanat), échanges conseillés dans le groupe, jets de butin.

## F. Métiers
- Session métier + courses en ville + points carte ; maître vs HV vs compos ; « besoin / en trop » partout.

## G. Hors addon
- TreeAI Lite (WeakAuras / Musca Auras) pour les amis sur GeForce NOW ; page « Parler à l'IA » dans l'app.

## Méthode (pour réussir sans retour)
1. Rejouer chaque bug réel (fichiers TreeAI-erreurs.txt, captures) en scénario d'abord.
2. Session simulée de 4 h (groupe de 4, combat, villes, HV, morts) : 0 erreur, 0 freeze > 50 ms, 0 contradiction.
3. Revue « ce que le joueur voit » écran par écran ; puis une seule publication V5.0.0.
