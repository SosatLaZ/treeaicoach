"""Macro definitions for the 9 classes + shared consumable macros.

Template syntax (rendered by generator.py, then resolved in game by Modules/Macros.lua):
  {key}         spell of the class knowledge base (spells.py) -> localized name at runtime
  {item:cat}    best item of a consumables category in the bags (consumables.py) -> item:ID
  ?/line        optional line: dropped in game when a spell/item of the line is missing
  /cast ?[c] X  optional clause (``;`` separated): dropped when its spell is not known yet
Variants are tried in order: the first whose required (non optional) spells are all known and
items all present wins, so a macro grows with the character (rank-agnostic names).
Blizzard rules: one GCD ability per key press; /castsequence, conditionals, /stopcasting,
/petattack, /use, /equip only. No automation.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .spells import CLASSES, MANA_CLASSES

# Classic-friendly targeting (no focus): mouseover first, then target, then self.
HELP = "[mod:shift,@player][@mouseover,help,nodead][help,nodead][@player]"
HARM = "[@mouseover,harm,nodead][harm,nodead]"
ICON = "INV_Misc_QuestionMark"  # with #showtooltip the icon follows the spell/item


@dataclass(frozen=True)
class Macro:
    name: str                 # in-game macro name (<= 16 chars)
    desc: str                 # short French description (shown in the addon)
    key: str                  # keybind suggestion
    variants: tuple[str, ...]
    classes: tuple[str, ...] = CLASSES
    icon: str = ICON
    default: bool = True      # installed by "/taimacro installer"
    kind: str = "spell"       # "spell" | "consumable" (re-rendered on bag changes)
    tags: tuple[str, ...] = field(default_factory=tuple)


def M(name, desc, key, *variants, classes=CLASSES, **kw) -> Macro:
    if isinstance(classes, str):
        classes = (classes,)
    return Macro(name, desc, key, tuple(v.strip("\n") for v in variants), tuple(classes), **kw)


NON_MANA = tuple(c for c in CLASSES if c not in MANA_CLASSES)
MANA = tuple(c for c in CLASSES if c in MANA_CLASSES)

# ---------------------------------------------------------------- shared (consumables)
SHARED = [
    M("TAI Repas", "Mange + boit (Maj : boire seul, Ctrl : manger seul)", "Maj+B", """
#showtooltip
?/use [nomod:shift] {item:food}
?/use [nomod:ctrl] {item:water}
""", classes=MANA, kind="consumable"),
    M("TAI Repas", "Mange la meilleure nourriture du sac", "Maj+B", """
#showtooltip
?/use {item:food}
""", classes=NON_MANA, kind="consumable"),
    M("TAI Survie", "Pierre de soins puis potion de soins (2e appui)", "Maj+H", """
#showtooltip
/castsequence reset=combat {item:hs}, {item:hpot}
""", """
#showtooltip
/use {item:hs}
""", """
#showtooltip
/use {item:hpot}
""", kind="consumable"),
    M("TAI Mana", "Gemme de mana puis potion de mana", "Maj+J", """
#showtooltip
/castsequence reset=combat {item:managem}, {item:mpot}
""", """
#showtooltip
/use {item:managem}
""", """
#showtooltip
/use {item:mpot}
""", classes="MAGE", kind="consumable"),
    M("TAI Mana", "Meilleure potion de mana du sac", "Maj+J", """
#showtooltip
/use {item:mpot}
""", classes=tuple(c for c in MANA if c != "MAGE"), kind="consumable"),
    M("TAI Bandage", "Bandage : survol > cible amicale > soi", "Maj+N", f"""
#showtooltip
/use {HELP.replace('[mod:shift,@player]', '')} {{item:bandage}}
""", kind="consumable"),
    M("TAI Munitions", "Équipe les meilleures munitions (arc/arbalète = flèches, fusil = balles)",
      "-", """
#showtooltip
/equip {item:ammo}
""", classes="HUNTER", kind="consumable"),
    M("TAI Poisons", "Poison instantané main droite (Maj : main gauche, mortel)", "-", """
#showtooltip
?/use [nomod] {item:poison_mh}
?/use [mod:shift] {item:poison_oh}
/use [mod:shift] 17; 16
/click StaticPopup1Button1
""", classes="ROGUE", kind="consumable"),
    M("TAI Marqueur", "Crâne sur la cible (Maj : croix) pour le groupe", "Ctrl+1", """
/run SetRaidTarget("target",IsShiftKeyDown() and 7 or 8)
""", icon="Ability_Hunter_SniperShot", default=False),
]

# ---------------------------------------------------------------- HUNTER (user's class)
HUNTER = [
    M("TAI Tir", "Tir auto + familier attaque + Tir des arcanes (Maj : Flèches multiples, "
      "Alt : familier reste)", "1", """
#showtooltip
/petattack [nomod:alt,@target,harm,nodead]
/cast !{auto_shot}
/cast ?[mod:shift] {multi_shot}; {arcane_shot}
""", """
#showtooltip
/petattack [nomod:alt,@target,harm,nodead]
/cast !{auto_shot}
"""),
    M("TAI Marque", "Marque du chasseur (survol prioritaire) + envoie le familier", "2", f"""
#showtooltip
/cast {HARM} {{hunters_mark}}
/petattack [nomod:alt,harm,nodead]
"""),
    M("TAI Morsure", "Morsure de serpent (Maj : vipère, Ctrl : scorpide)", "3", """
#showtooltip
/cast ?[mod:shift] {viper_sting}; ?[mod:ctrl] {scorpid_sting}; {serpent_sting}
"""),
    M("TAI Controle", "Trait de choc (Maj : dispersion, Ctrl : effrayer bête, Alt : intimidation)",
      "4", """
#showtooltip
/cast ?[mod:shift] {scatter_shot}; ?[mod:ctrl] {scare_beast}; ?[mod:alt] {intimidation}; {concussive_shot}
"""),
    M("TAI Melee", "Mêlée : Mangouste + Raptor (Maj : Coupure d'ailes)", "Q", """
#showtooltip
/startattack
/cast ?[mod:shift] {wing_clip}; ?{mongoose_bite}
/cast {raptor_strike}
"""),
    M("TAI Pieges", "Piège givrant (Maj : givre, Alt : explosif, Ctrl : immolation)", "E", """
#showtooltip
/cast ?[mod:shift] {frost_trap}; ?[mod:alt] {explosive_trap}; ?[mod:ctrl] {immolation_trap}; {freezing_trap}
""", """
#showtooltip
/cast {immolation_trap}
"""),
    M("TAI FD Piege", "Feindre la mort puis Piège givrant (2e appui)", "Maj+E", """
#showtooltip
/stopcasting
/stopattack
/petfollow
/castsequence reset=6 {feign_death}, {freezing_trap}
"""),
    M("TAI Aspects", "Aspect du faucon (Maj : guépard, Ctrl : meute, Alt : singe), sans annuler",
      "X", """
#showtooltip
/cast ?[mod:shift] !{aspect_cheetah}; ?[mod:ctrl] !{aspect_pack}; ?[mod:alt] !{aspect_monkey}; !{aspect_hawk}
""", """
#showtooltip
/cast !{aspect_monkey}
"""),
    M("TAI Pet", "Familier : attaque (survol) / Maj suivre / Ctrl passif / Alt défensif", "F", """
/petattack [nomod,@mouseover,harm,nodead][nomod,harm,nodead]
/petfollow [mod:shift]
/petpassive [mod:ctrl]
/petdefensive [mod:alt]
""", icon="Ability_Hunter_Pet_Wolf"),
    M("TAI Familier", "Appel / Guérison du familier (Maj : ressusciter, Ctrl : nourrir avec la "
      "bonne nourriture)", "V", """
#showtooltip
?/cast [mod:ctrl] {feed_pet}
?/use [mod:ctrl] {item:petfood}
/stopmacro [mod:ctrl]
/cast [mod:shift][@pet,dead] {revive_pet}; [nopet] {call_pet}; {mend_pet}
""", """
#showtooltip
/cast [mod:shift][@pet,dead] {revive_pet}; {call_pet}
"""),
    M("TAI Burst", "Tir rapide + Courroux bestial + racial + bijoux", "R", """
#showtooltip {rapid_fire}
/cast {rapid_fire}
?/cast {bestial_wrath}
?/cast {berserking}
?/cast {blood_fury}
/use 13
/use 14
"""),
    M("TAI Tranqui", "Tir tranquillisant (enrage) sur survol ou cible", "-", f"""
#showtooltip
/cast {HARM} {{tranq_shot}}
""", default=False),
    M("TAI Pistage", "Pistage des bêtes (Maj : humanoïdes)", "-", """
#showtooltip
/cast ?[mod:shift] {track_humanoids}; {track_beasts}
""", default=False),
]

# ---------------------------------------------------------------- WARRIOR
WARRIOR = [
    M("TAI Charge", "Charge hors combat, Interception en combat (danse des postures)", "1", """
#showtooltip
/cast [nocombat,nostance:1] {battle_stance}; [nocombat] {charge}; [nostance:3] {berserker_stance}; {intercept}
""", """
#showtooltip
/cast [nostance:1] {battle_stance}; {charge}
"""),
    M("TAI Frappe", "Frappe héroïque + attaque auto (Maj : Enchaînement)", "2", """
#showtooltip
/startattack
/cast ?[mod:shift] {cleave}; {heroic_strike}
"""),
    M("TAI Fulgu", "Fulgurance (passe en Posture de combat si besoin)", "3", """
#showtooltip
/startattack
/cast [nostance:1] {battle_stance}; {overpower}
"""),
    M("TAI Interrupt", "Coup de bouclier (combat/déf.) ou Volée de coups (berserker)", "4", """
#showtooltip
/cast ?[stance:3] {pummel}; {shield_bash}
"""),
    M("TAI Provoc", "Provocation (passe en Posture défensive si besoin)", "T", """
#showtooltip
/cast [nostance:2] {defensive_stance}; {taunt}
"""),
    M("TAI Cris", "Cri de guerre (Maj : démoralisant)", "5", """
#showtooltip
/cast ?[mod:shift] {demo_shout}; {battle_shout}
"""),
    M("TAI Rage", "Rage sanguinaire + Rage berserker si posture berserker", "R", """
#showtooltip
/cast {bloodrage}
?/cast [stance:3] {berserker_rage}
?/cast {blood_fury}
?/cast {berserking}
"""),
    M("TAI Exec", "Exécution (Maj : Brise-genou)", "Q", """
#showtooltip
/startattack
/cast ?[mod:shift] {hamstring}; {execute}
"""),
    M("TAI Fracasser", "Fracasser armure / Vengeance (tank)", "E", """
#showtooltip
/startattack
/cast ?[mod:shift] {revenge}; {sunder}
"""),
]

# ---------------------------------------------------------------- ROGUE
ROGUE = [
    M("TAI Ouverture", "Camouflage puis Embuscade (Maj : Coup bas, Ctrl : Garrot)", "1", """
#showtooltip
/cast [nostealth] {stealth}; ?[mod:shift] {cheap_shot}; ?[mod:ctrl] {garrote}; {ambush}
""", """
#showtooltip
/cast [nostealth] {stealth}; {sinister_strike}
"""),
    M("TAI Combo", "Attaque pernicieuse + attaque auto (Maj : sournoise)", "2", """
#showtooltip
/startattack
/cast ?[mod:shift] {backstab}; {sinister_strike}
"""),
    M("TAI Finish", "Éviscération (Maj : Aiguillon perfide, Ctrl : Débiter)", "3", """
#showtooltip
/startattack
/cast ?[mod:shift] {kidney_shot}; ?[mod:ctrl] {slice_dice}; {eviscerate}
"""),
    M("TAI Kick", "Coup de pied (Maj : Suriner)", "4", """
#showtooltip
/cast ?[mod:shift] {gouge}; {kick}
"""),
    M("TAI Assommer", "Vol à la tire puis Assommer (survol)", "5", f"""
#showtooltip {{sap}}
?/cast [stealth] {{pick_pocket}}
/cast {HARM} {{sap}}
"""),
    M("TAI Fuite", "Évasion (Maj : Disparition, Ctrl : Sprint)", "R", """
#showtooltip
/cast ?[mod:shift] {vanish}; [mod:ctrl] {sprint}; ?{evasion}
""", """
#showtooltip
/cast {sprint}
"""),
    M("TAI Cecite", "Cécité sur survol ou cible", "-", f"""
#showtooltip
/cast {HARM} {{blind}}
"""),
]

# ---------------------------------------------------------------- MAGE
MAGE = [
    M("TAI Contresort", "Interrompt ton sort et Contresort (survol)", "4", f"""
#showtooltip
/stopcasting
/cast {HARM} {{counterspell}}
"""),
    M("TAI Mouton", "Métamorphose sur survol ou cible", "5", f"""
#showtooltip
/cast {HARM} {{polymorph}}
"""),
    M("TAI Nova", "Stop incantation + Nova de givre (Maj : Transfert)", "Q", """
#showtooltip
/stopcasting
/cast ?[mod:shift] {blink}; {frost_nova}
"""),
    M("TAI Invoc", "Invocation d'eau (Maj : nourriture)", "-", """
#showtooltip
/cast [mod:shift] {conjure_food}; {conjure_water}
""", """
#showtooltip
/cast {conjure_food}
"""),
    M("TAI Armure", "Armure de glace/givre (Maj : Armure du mage, Alt : Bouclier de mana)", "-", """
#showtooltip
/cast ?[mod:shift] {mage_armor}; ?[mod:alt] {mana_shield}; {ice_armor}
""", """
#showtooltip
/cast ?[mod:alt] {mana_shield}; {frost_armor}
"""),
    M("TAI Intel", "Intelligence des arcanes : survol > cible > soi", "-", f"""
#showtooltip
/cast {HELP} {{arcane_intellect}}
"""),
    M("TAI Decurse", "Délivrance de la malédiction : survol > cible > soi", "-", f"""
#showtooltip
/cast {HELP} {{remove_curse}}
"""),
    M("TAI Burst", "Présence spirituelle / Pouvoir des arcanes / Combustion + bijoux", "R", """
#showtooltip
?/cast {presence_of_mind}
?/cast {arcane_power}
?/cast {combustion}
?/cast {berserking}
/use 13
/use 14
"""),
    M("TAI Bloc", "Bloc de glace (annule s'il est actif)", "-", """
#showtooltip
/stopcasting
/cancelaura {ice_block}
/cast {ice_block}
""", default=False),
]

# ---------------------------------------------------------------- PRIEST
PRIEST = [
    M("TAI SoinRapide", "Soins rapides : survol > cible > soi (Maj : soi)", "1", f"""
#showtooltip
/cast {HELP} {{flash_heal}}
"""),
    M("TAI Soin", "Soins / Soins supérieurs : survol > cible > soi", "2", f"""
#showtooltip
/cast {HELP} {{greater_heal}}
""", f"""
#showtooltip
/cast {HELP} {{heal}}
""", f"""
#showtooltip
/cast {HELP} {{lesser_heal}}
"""),
    M("TAI Renov", "Rénovation : survol > cible > soi", "3", f"""
#showtooltip
/cast {HELP} {{renew}}
"""),
    M("TAI Bouclier", "Mot de pouvoir : Bouclier : survol > cible > soi", "4", f"""
#showtooltip
/cast {HELP} {{pws}}
"""),
    M("TAI Dissip", "Dissipation (ami ou ennemi, survol d'abord)", "5", """
#showtooltip
/cast [@mouseover,exists,nodead][exists,nodead][@player] {dispel}
"""),
    M("TAI Robu", "Robustesse : survol > cible > soi", "-", f"""
#showtooltip
/cast {HELP} {{fortitude}}
"""),
    M("TAI Baguette", "Baguette (sans l'arrêter si déjà active)", "Q", """
#showtooltip
/stopmacro [noexists][dead][help]
/cast !{wand}
"""),
    M("TAI Oubli", "Stop incantation + Oubli (Maj : Cri psychique)", "R", """
#showtooltip
/stopcasting
/cast ?[mod:shift] {psychic_scream}; {fade}
""", """
#showtooltip
/stopcasting
/cast {fade}
"""),
    M("TAI Douleur", "Mot de l'ombre : Douleur sur survol ou cible", "E", f"""
#showtooltip
/cast {HARM} {{swp}}
"""),
]

# ---------------------------------------------------------------- PALADIN
PALADIN = [
    M("TAI Sceau", "Sceau de piété (Maj : Jugement, Ctrl : Croisé)", "1", """
#showtooltip
/startattack
/cast ?[mod:shift] {judgement}; ?[mod:ctrl] {seal_crusader}; {seal_righteousness}
"""),
    M("TAI Lumiere", "Lumière sacrée : survol > cible > soi (Maj : soi)", "2", f"""
#showtooltip
/cast {HELP} {{holy_light}}
"""),
    M("TAI Eclair", "Éclair lumineux : survol > cible > soi", "3", f"""
#showtooltip
/cast {HELP} {{flash_of_light}}
"""),
    M("TAI Purif", "Épuration/Purification : survol > cible > soi", "4", f"""
#showtooltip
/cast {HELP} {{cleanse}}
""", f"""
#showtooltip
/cast {HELP} {{purify}}
"""),
    M("TAI Marteau", "Stop incantation + Marteau de la justice (survol)", "5", f"""
#showtooltip
/stopcasting
/cast {HARM} {{hammer_justice}}
"""),
    M("TAI Benedic", "Puissance (Maj : sagesse, Ctrl : rois) : survol > cible > soi", "-",
      """
#showtooltip
/cast ?[mod:ctrl] {bok}; ?[mod:shift] {bow}; [@mouseover,help,nodead][help,nodead][@player] {bom}
"""),
    M("TAI Aura", "Aura de dévotion (Maj : concentration, Ctrl : vindicte)", "-", """
#showtooltip
/cast ?[mod:shift] {concentration_aura}; ?[mod:ctrl] {retribution_aura}; {devotion_aura}
"""),
    M("TAI Bulle", "Stop incantation + Bouclier divin (Maj : BoP sur survol)", "R", """
#showtooltip
/stopcasting
/cast ?[mod:shift,@mouseover,help,nodead][mod:shift] {bop}; {divine_shield}
""", """
#showtooltip
/stopcasting
/cast {divine_protection}
"""),
]

# ---------------------------------------------------------------- WARLOCK
WARLOCK = [
    M("TAI Dots", "Corruption (Maj : agonie, Ctrl : Immolation)", "1", """
#showtooltip
/cast ?[mod:shift] {coa}; ?[mod:ctrl] {immolate}; {corruption}
""", """
#showtooltip
/cast {immolate}
"""),
    M("TAI Peur", "Peur sur survol ou cible", "2", f"""
#showtooltip
/cast {HARM} {{fear}}
"""),
    M("TAI Pet", "Démon : attaque (survol) / Maj suivre / Ctrl passif", "F", """
/petattack [nomod,@mouseover,harm,nodead][nomod,harm,nodead]
/petfollow [mod:shift]
/petpassive [mod:ctrl]
""", icon="Spell_Shadow_SummonImp"),
    M("TAI Interrupt", "Verrou magique (chasseur) / Séduction (succube) / Tourment", "4", """
#showtooltip
/cast ?{spell_lock}
/cast ?{seduction}
/cast ?{torment}
"""),
    M("TAI Connexion", "Connexion (Maj : Captation de vie)", "Q", """
#showtooltip
/cast ?[mod:shift] {health_funnel}; {life_tap}
"""),
    M("TAI Armure", "Armure démoniaque (sinon Peau de démon)", "-", """
#showtooltip
/cast {demon_armor}
""", """
#showtooltip
/cast {demon_skin}
"""),
    M("TAI Pierre", "Crée la meilleure Pierre de soins connue", "-", """
#showtooltip
/cast {hs_major}
""", """
#showtooltip
/cast {hs_greater}
""", """
#showtooltip
/cast {hs_normal}
""", """
#showtooltip
/cast {hs_lesser}
""", """
#showtooltip
/cast {hs_minor}
"""),
    M("TAI Fuite", "Voile mortel (Maj : Hurlement de terreur)", "R", f"""
#showtooltip
/stopcasting
/cast ?[mod:shift] {{howl}}; {HARM} {{death_coil}}
"""),
    M("TAI Siphon", "Siphon d'âme (fragment) sur survol ou cible", "-", f"""
#showtooltip
/cast {HARM} {{drain_soul}}
"""),
]

# ---------------------------------------------------------------- SHAMAN
SHAMAN = [
    M("TAI Horion", "Horion de terre (Maj : givre, Ctrl : flammes)", "1", """
#showtooltip
/cast ?[mod:shift] {frost_shock}; ?[mod:ctrl] {flame_shock}; {earth_shock}
"""),
    M("TAI Interrupt", "Stop incantation + Horion de terre (survol)", "4", f"""
#showtooltip
/stopcasting
/cast {HARM} {{earth_shock}}
"""),
    M("TAI Vague", "Vague de soins : survol > cible > soi (Maj : soi)", "2", f"""
#showtooltip
/cast {HELP} {{healing_wave}}
"""),
    M("TAI VagueInf", "Vague de soins inférieurs : survol > cible > soi", "3", f"""
#showtooltip
/cast {HELP} {{lesser_hw}}
"""),
    M("TAI Totems", "Totems un par appui : Force de la Terre, incendiaire, Fontaine de mana",
      "5", """
#showtooltip
/castsequence reset=combat/60 {soe_totem}, {searing_totem}, {manaspring_totem}
""", """
#showtooltip
/castsequence reset=combat/60 {stoneskin_totem}, {searing_totem}
"""),
    M("TAI Arme", "Arme Furie-des-vents (Maj : Langue de feu, Ctrl : Croque-roc)", "-", """
#showtooltip
/cast ?[mod:shift] {flametongue}; ?[mod:ctrl] {rockbiter}; {windfury}
""", """
#showtooltip
/cast ?[mod:shift] {flametongue}; {rockbiter}
"""),
    M("TAI Loup", "Loup fantôme (sans annuler)", "Z", """
#showtooltip
/cast !{ghost_wolf}
"""),
    M("TAI Purge", "Purge sur survol ou cible", "-", f"""
#showtooltip
/cast {HARM} {{purge}}
"""),
    M("TAI Bouclier", "Bouclier de foudre", "-", """
#showtooltip
/cast {lightning_shield}
"""),
]

# ---------------------------------------------------------------- DRUID
DRUID = [
    M("TAI Ours", "Forme d'ours / en ours : Mutilation (Maj : Grondement, Ctrl : Sonner)", "1", """
#showtooltip
/cast [nostance:1] {dire_bear_form}; ?[mod:shift] {growl}; ?[mod:ctrl] {bash}; {maul}
/startattack [stance:1]
""", """
#showtooltip
/cast [nostance:1] {bear_form}; ?[mod:shift] {growl}; ?[mod:ctrl] {bash}; {maul}
/startattack [stance:1]
"""),
    M("TAI Felin", "Forme de félin / en félin : Griffe (camouflé : Ravager, Maj : Lambeau)", "2",
      """
#showtooltip
/cast [nostance:3] {cat_form}; ?[stealth] {ravage}; ?[mod:shift] {shred}; {claw}
/startattack [stance:3,nostealth]
"""),
    M("TAI Voyage", "Nage : aquatique, dehors : voyage, sinon félin", "Z", """
#showtooltip
/cast ?[swimming] {aquatic_form}; ?[outdoors] {travel_form}; {cat_form}
"""),
    M("TAI Soin", "Toucher guérisseur : survol > cible > soi (Maj : soi)", "3", f"""
#showtooltip
/cast {HELP} {{healing_touch}}
"""),
    M("TAI Recup", "Récupération : survol > cible > soi (Maj : soi)", "4", f"""
#showtooltip
/cast {HELP} {{rejuvenation}}
"""),
    M("TAI Retab", "Rétablissement : survol > cible > soi (Maj : soi)", "-", f"""
#showtooltip
/cast {HELP} {{regrowth}}
"""),
    M("TAI Racines", "Sarments sur survol ou cible (sort de forme)", "5", f"""
#showtooltip
/cast {HARM} {{roots}}
"""),
    M("TAI Lucioles", "Lucioles (version farouche en forme)", "E", """
#showtooltip
/cast ?[stance:1/3] {faerie_fire_feral}; {faerie_fire}
"""),
    M("TAI Fauve", "Marque du fauve (Maj : Épines) : survol > cible > soi", "-", """
#showtooltip
/cast ?[mod:shift] {thorns}; [@mouseover,help,nodead][help,nodead][@player] {motw}
"""),
    M("TAI Innerv", "Innervation : survol > cible > soi", "-", f"""
#showtooltip
/cast {HELP} {{innervate}}
"""),
    M("TAI Decurse", "Délivrance de la malédiction (Maj : poison) : survol > cible > soi", "-",
      """
#showtooltip
/cast ?[mod:shift] {cure_poison}; [@mouseover,help,nodead][help,nodead][@player] {remove_curse}
"""),
]

BY_CLASS = {
    "HUNTER": HUNTER, "WARRIOR": WARRIOR, "ROGUE": ROGUE, "MAGE": MAGE, "PRIEST": PRIEST,
    "PALADIN": PALADIN, "WARLOCK": WARLOCK, "SHAMAN": SHAMAN, "DRUID": DRUID,
}


def macros_for(cls: str) -> list[Macro]:
    """All macros (class + shared) for a class, class macros first."""
    own = [m for m in BY_CLASS.get(cls, ()) if cls in m.classes or m.classes == CLASSES]
    return own + [m for m in SHARED if cls in m.classes]
