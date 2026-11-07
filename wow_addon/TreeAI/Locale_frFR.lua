-- TreeAI - chaînes françaises. Les clés sont déjà en français: L[clé] renvoie
-- la clé si absente, donc ce fichier sert surtout de référence et de point
-- d'entrée pour une future traduction (ex. Locale_enUS.lua).
-- Les modules peuvent ajouter leurs chaînes: TreeAI.L["..."] = "...".
local TreeAI = TreeAI
if not TreeAI then return end
local L = TreeAI.L

local strings = {
  -- cœur / slash
  ["Commandes"] = "Commandes",
  ["Commande inconnue. Tape /tai aide"] = "Commande inconnue. Tape /tai aide",
  ["activé"] = "activé",
  ["désactivé"] = "désactivé",
  ["afficher le panneau"] = "afficher le panneau",
  ["cacher le panneau"] = "cacher le panneau",
  ["afficher/cacher"] = "afficher/cacher",
  ["Panneau verrouillé"] = "Panneau verrouillé",
  ["verrouiller/déverrouiller le panneau"] = "verrouiller/déverrouiller le panneau",
  ["Position du panneau réinitialisée."] = "Position du panneau réinitialisée.",
  ["remettre le panneau au centre"] = "remettre le panneau au centre",
  ["ouvrir les options"] = "ouvrir les options",
  ["liste des modules"] = "liste des modules",
  ["Module introuvable."] = "Module introuvable.",
  ["<Nom> on|off"] = "<Nom> on/off : activer/désactiver un module",
  ["Synchro envoyée au groupe."] = "Synchro envoyée au groupe.",
  ["Pas de groupe (ou comms désactivées)."] = "Pas de groupe (ou synchro désactivée).",
  ["synchroniser avec le groupe"] = "synchroniser avec le groupe",
  ["mode debug"] = "mode debug",
  ["Sacs libres"] = "Sacs libres",
  ["Amis TreeAI connus"] = "Amis avec TreeAI",
  ["état de l'addon"] = "état de l'addon",
  ["Exemple: conseil d'information."] = "Exemple : information.",
  ["Exemple: conseil utile."] = "Exemple : conseil utile.",
  ["Exemple: alerte urgente !"] = "Exemple : alerte urgente !",
  ["afficher des suggestions de test"] = "afficher des conseils de test",
  ["Impossible en combat."] = "Impossible en combat.",
  -- panneau
  ["Coach"] = "Coach",
  ["Rien à signaler."] = "Rien à signaler.",
  ["Macro proposée"] = "Macro proposée",
  ["Clic droit: masquer"] = "Clic droit : masquer",
  -- options
  ["Conseils uniquement: l'addon n'agit jamais à ta place."] = "Conseils uniquement : l'addon n'agit jamais à ta place.",
  ["Général"] = "Général",
  ["Activer TreeAI"] = "Activer TreeAI",
  ["Afficher le panneau"] = "Afficher le panneau",
  ["Verrouiller le panneau"] = "Verrouiller le panneau",
  ["Son pour les alertes urgentes"] = "Son pour les alertes urgentes",
  ["Écrire les conseils dans le chat"] = "Écrire les conseils dans le chat",
  ["Synchro avec le groupe (messages d'addon)"] = "Synchro avec le groupe (messages d'addon)",
  ["Priorité minimale"] = "Priorité minimale (1 info, 2 conseil, 3 urgent)",
  ["Lignes max"] = "Lignes max",
  ["Échelle"] = "Échelle",
  ["Opacité"] = "Opacité",
  ["Activé"] = "Activé",
  -- vocabulaire partagé pour les modules
  ["Hors combat uniquement."] = "Hors combat uniquement.",
  ["Macro créée"] = "Macro créée",
  ["Macro mise à jour"] = "Macro mise à jour",
  ["Plus de place pour les macros."] = "Plus de place pour les macros.",
}

for k, v in pairs(strings) do
  L[k] = v
end
