"""Macro knowledge base for the 9 WoW Forever classes (spec × solo/groupe × level) + consumables.

Template syntax (rendered by generator.py, resolved in game by Modules/Macros.lua):
  {key}         class spell (spells.py) -> localized client name at runtime (rank-agnostic)
  {item:cat}    best item of a consumables category in the bags (consumables.py) -> item:ID
  {slot:13}     equipment slot 13/14 when the trinket there has a « Utiliser : » effect
  ?/line        optional line: dropped in game when a spell/item/trinket of the line is missing
  /cast ?[c] X  optional clause (``;`` separated): dropped while its spell is not learned
Variants are tried in order: the first whose required (non optional) spells are all known wins, so a
macro grows with the character. ``specs`` = specialisations it is written for (empty: all), ``ctx`` =
"solo" / "groupe" / "" (both). ``why`` = the « pourquoi » shown in the macro panel.

Blizzard rules: one ability on the global cooldown per key press (off-GCD lines — next-swing attacks,
cooldowns, racials, trinkets, pet commands — may accompany it); conditionals, /castsequence,
/stopcasting, /petattack, /use, /equip only; no automation. Targeting without focus (Forever is
Classic-family): mouseover first, then target, then self for helpful spells; Alt = self-cast.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .spells import CLASSES, MANA_CLASSES

ICON = "INV_Misc_QuestionMark"  # with #showtooltip the icon follows the spell/item

# --- targeting snippets -------------------------------------------------------------------------
MO = "[@mouseover,harm,nodead][]"                                         # hostile: mouseover > target
HELP = "[mod:alt,@player][@mouseover,help,nodead][@target,help,nodead][@player]"  # friendly: Alt=self
DEAD = "[@mouseover,help,dead][help,dead]"                                # resurrection


def mo(mod: str) -> str:
    """Hostile clause gated by a modifier: mouseover first, else target."""
    return f"[mod:{mod},@mouseover,harm,nodead][mod:{mod}]"


def hm(mod: str) -> str:
    """Friendly clause gated by a modifier: mouseover, else friendly target, else self."""
    return f"[mod:{mod},@mouseover,help,nodead][mod:{mod},help,nodead][mod:{mod},@player]"


TRINKETS = "?/use {slot:13}\n?/use {slot:14}"
RACIALS = "?/cast {blood_fury}\n?/cast {berserking}"


@dataclass(frozen=True)
class Macro:
    name: str                 # in-game macro name (<= 16 chars, "TAI " prefix)
    desc: str                 # short French description (one line in the panel)
    key: str                  # keybind suggestion
    variants: tuple[str, ...]
    classes: tuple[str, ...] = CLASSES
    why: str = ""             # « pourquoi » (French, shown in the panel)
    specs: tuple[str, ...] = ()
    ctx: str = ""             # "" | "solo" | "groupe"
    icon: str = ICON
    default: bool = True      # part of the per-character install set
    kind: str = "spell"       # "spell" | "consumable" (re-rendered on bag changes)
    tags: tuple[str, ...] = field(default_factory=tuple)


def M(name, desc, key, *variants, classes=CLASSES, specs=(), **kw) -> Macro:
    if isinstance(classes, str):
        classes = (classes,)
    if isinstance(specs, str):
        specs = (specs,)
    return Macro(name, desc, key, tuple(v.strip("\n") for v in variants), tuple(classes),
                 specs=tuple(specs), **kw)


NON_MANA = tuple(c for c in CLASSES if c not in MANA_CLASSES)
MANA = tuple(c for c in CLASSES if c in MANA_CLASSES)
CASTERS = ("PRIEST", "MAGE", "WARLOCK")

# ================================================================ shared (consumables)
SHARED = [
    M("TAI Repas", "Mange + boit (Maj : boire seul, Ctrl : manger seul)", "Maj+B", """
#showtooltip
?/use [nomod:shift] {item:food}
?/use [nomod:ctrl] {item:water}
""", classes=MANA, kind="consumable",
      why="Nourriture et boisson du sac les plus fortes pour ton niveau, mises à jour quand ton sac "
          "change (les invoquées passent d'abord, elles sont gratuites). Manger et boire ne déclenchent "
          "pas de temps de recharge global : un seul appui lance les deux."),
    M("TAI Repas", "Mange la meilleure nourriture du sac", "Maj+B", """
#showtooltip
?/use {item:food}
""", classes=NON_MANA, kind="consumable",
      why="La meilleure nourriture utilisable du sac, mise à jour automatiquement."),
    M("TAI Survie", "Pierre de soins, puis potion de soins au 2e appui", "Maj+H", """
#showtooltip
/castsequence reset=combat {item:hs}, {item:hpot}
""", """
#showtooltip
/use {item:hs}
""", """
#showtooltip
/use {item:hpot}
""", kind="consumable",
      why="Les deux ont des temps de recharge séparés : pierre d'abord (gratuite), potion ensuite. "
          "La séquence revient au début à la fin du combat."),
    M("TAI Mana", "Gemme de mana, puis potion de mana", "Maj+J", """
#showtooltip
/castsequence reset=combat {item:managem}, {item:mpot}
""", """
#showtooltip
/use {item:managem}
""", """
#showtooltip
/use {item:mpot}
""", classes="MAGE", kind="consumable",
      why="Gemme (gratuite, recharge séparée) puis potion au 2e appui."),
    M("TAI Mana", "Meilleure potion de mana du sac", "Maj+J", """
#showtooltip
/use {item:mpot}
""", classes=tuple(c for c in MANA if c != "MAGE"), kind="consumable",
      why="La potion de mana la plus forte utilisable à ton niveau."),
    M("TAI Bandage", "Bandage : survol > cible amicale > toi (Alt : toi)", "Maj+N", f"""
#showtooltip
/use {HELP} {{item:bandage}}
""", kind="consumable",
      why="Bandage le plus fort permis par ton Secourisme, posé sur l'allié survolé sans changer de cible."),
    M("TAI Munitions", "Équipe les meilleures munitions (arc/arbalète = flèches, fusil = balles)",
      "-", """
#showtooltip
/equip {item:ammo}
""", classes="HUNTER", kind="consumable",
      why="Choisit flèches ou balles selon ton arme, au meilleur niveau dans le sac."),
    M("TAI Poisons", "Poison main droite (Maj : main gauche)", "-", """
#showtooltip
?/use [nomod] {item:poison_mh}
?/use [mod:shift] {item:poison_oh}
/use [mod:shift] 17; 16
""", classes="ROGUE", kind="consumable",
      why="Applique le poison du sac sur l'arme (16 = main droite, 17 = main gauche)."),
    M("TAI Baguette", "Baguette sur survol ou cible (ne s'arrête pas si on rappuie)", "-", f"""
#showtooltip
/cast {MO} !{{wand}}
""", classes=CASTERS, ctx="solo",
      why="Le « ! » empêche de couper la baguette en rappuyant : économise ton mana entre deux sorts."),
    M("TAI Marqueur", "Crâne sur la cible (Maj : croix) pour le groupe", "Ctrl+1", """
/run SetRaidTarget("target",IsShiftKeyDown() and 7 or 8)
""", icon="Ability_Hunter_SniperShot", default=False, ctx="groupe",
      why="Marque la cible à tuer en premier (crâne) ou à contrôler (croix)."),
]

# ================================================================ WARRIOR
WARRIOR = [
    M("TAI Charge", "Hors combat : Charge (Combat) ; en combat : Interception (Berserker)", "1", """
#showtooltip
/startattack [harm,nodead]
/cast [nocombat,nostance:1] {battle_stance}; [nocombat] {charge}; ?[nostance:3] {berserker_stance}; ?{intercept}
""", specs=("armes", "fureur"),
      why="Même touche toute la partie : hors combat elle passe en Posture de combat puis charge ; en "
          "combat elle passe en Berserker puis Interception (niveau 30). /startattack lance l'attaque "
          "auto pour générer de la rage."),
    M("TAI Charge", "Hors combat : Charge ; en combat : retour en Posture défensive", "1", """
#showtooltip
/startattack [harm,nodead]
/cast [nocombat,nostance:1] {battle_stance}; [nocombat] {charge}; [nostance:2] {defensive_stance}
""", """
#showtooltip
/startattack [harm,nodead]
/cast [nostance:1] {battle_stance}; {charge}
""", specs="protection",
      why="Tank : Charge hors combat (Posture de combat), puis le même bouton te remet en Posture "
          "défensive dès que le combat commence — tu ne restes jamais en mauvaise posture."),
    M("TAI Attaque", "Frappe mortelle (Maj : Heurtoir)", "2", """
#showtooltip
/startattack
/cast ?[mod:shift] {slam}; {mortal_strike}
""", """
#showtooltip
/startattack
/cast ?[mod:shift] {slam}; {piercing_strike}
""", specs="armes",
      why="Ton coup principal sur le temps de recharge global : Frappe transperçante en montée, Frappe "
          "mortelle dès que le talent est pris (réduit aussi les soins reçus). Maj = Heurtoir."),
    M("TAI Attaque", "Sanguinaire (Maj : Tourbillon en Berserker)", "2", """
#showtooltip
/startattack
/cast ?[mod:shift,stance:3] {whirlwind}; ?[mod:shift] {slam}; {bloodthirst}
""", """
#showtooltip
/startattack
/cast ?[mod:shift,stance:3] {whirlwind}; ?[mod:shift] {slam}; {piercing_strike}
""", specs="fureur",
      why="Coup principal de Fureur. Tourbillon n'existe qu'en Posture berserker : sans elle Maj lance "
          "Heurtoir."),
    M("TAI Attaque", "Heurt de bouclier (Maj : Vengeance)", "2", """
#showtooltip
/startattack
/cast ?[mod:shift] {revenge}; {shield_slam}
""", """
#showtooltip
/startattack
/cast ?[mod:shift] {revenge}; {sunder}
""", """
#showtooltip
/startattack
/cast {piercing_strike}
""", specs="protection",
      why="Menace d'abord : Fracasser armure puis Heurt de bouclier ; Vengeance (après une esquive / "
          "parade / blocage) sur Maj, c'est ta meilleure menace par rage."),
    M("TAI Frappe", "Frappe héroïque (Maj : Enchaînement) — vide la rage", "3", """
#showtooltip
/startattack
/cast ?[mod:shift] {cleave}; {heroic_strike}
""",
      why="Frappe héroïque remplace ta prochaine attaque blanche et ne déclenche pas le temps de recharge "
          "global : appuie quand tu as trop de rage. Maj = Enchaînement (2 cibles)."),
    M("TAI Fulgu", "Fulgurance (passe en Posture de combat si besoin)", "4", """
#showtooltip
/startattack
/cast [nostance:1] {battle_stance}; {overpower}
""", specs=("armes", "fureur"),
      why="Fulgurance n'existe qu'en Posture de combat après une esquive : 1er appui change de posture "
          "(Maîtrise tactique garde ta rage), 2e appui frappe."),
    M("TAI Exec", "Exécution sous 20 % (Maj : Brise-genou)", "E", """
#showtooltip
/startattack
/cast ?[mod:shift] {hamstring}; {execute}
""", """
#showtooltip
/startattack
/cast {hamstring}
""",
      why="Exécution convertit toute ta rage en dégâts sous 20 % de vie ; Brise-genou empêche les "
          "fuyards de partir chercher de l'aide."),
    M("TAI Interrupt", "Volée de coups (Berserker) ou Coup de bouclier", "Maj+4", """
#showtooltip
/cast ?[stance:3] {pummel}; {shield_bash}
""",
      why="Interrompt un sort : Volée de coups en Berserker, sinon Coup de bouclier (bouclier requis)."),
    M("TAI Provoc", "Provocation (survol), Ctrl : Cri de défi", "T", f"""
#showtooltip
/cast ?[mod:ctrl] {{challenging_shout}}; [nostance:2] {{defensive_stance}}; {MO} {{taunt}}
""", specs="protection", ctx="groupe",
      why="Reprend un monstre sur un allié survolé sans perdre ta cible ; passe en Posture défensive "
          "d'abord. Ctrl = Cri de défi quand tout le pack s'échappe."),
    M("TAI Provoc", "Coup railleur (Combat) pour reprendre un monstre", "T", """
#showtooltip
/cast [nostance:1] {battle_stance}; {mocking_blow}
""", specs=("armes", "fureur"), ctx="groupe", default=False,
      why="Hors tank : reprend 6 s un monstre qui tape le soigneur."),
    M("TAI Cris", "Cri de guerre (Maj : Cri démoralisant)", "5", """
#showtooltip
/cast ?[mod:shift] {demo_shout}; {battle_shout}
""",
      why="Cri de guerre = puissance d'attaque pour tout le groupe ; Cri démoralisant réduit les "
          "dégâts des monstres autour (bonne menace de zone pour le tank)."),
    M("TAI Rage", "Rage sanguinaire (+ Rage berserker en Berserker)", "R", """
#showtooltip {bloodrage}
/cast {bloodrage}
?/cast [stance:3] {berserker_rage}
""",
      why="Rage sanguinaire est hors temps de recharge global : rage gratuite au début du combat. En "
          "Berserker, le même appui lance Rage berserker (immunité peur)."),
    M("TAI Burst", "Témérité (+ Souhait mortel) + racial + bijoux", "Maj+R", f"""
#showtooltip
/cast ?[mod:shift] {{death_wish}}; [nostance:3] {{berserker_stance}}; {{recklessness}}
{RACIALS}
{TRINKETS}
""", f"""
#showtooltip
/cast {{death_wish}}
{RACIALS}
{TRINKETS}
""", f"""
#showtooltip
{RACIALS}
{TRINKETS}
""", specs=("armes", "fureur"),
      why="Tous tes gros cooldowns sur une touche : un seul est sur le temps de recharge global, les "
          "raciaux et bijoux « Utiliser » partent avec (lignes ajoutées seulement si tu les as)."),
    M("TAI Mur", "Mur protecteur (Maj : Dernier rempart)", "Maj+V", """
#showtooltip
/cast ?[mod:shift] {last_stand}; [nostance:2] {defensive_stance}; {shield_wall}
""", """
#showtooltip
/cast {last_stand}
""", specs="protection",
      why="Bouton panique du tank : -75 % de dégâts (Posture défensive requise), Maj = Dernier rempart."),
    M("TAI Blocage", "Maîtrise du blocage (Posture défensive)", "Q", """
#showtooltip
/cast [nostance:2] {defensive_stance}; {shield_block}
""", specs="protection",
      why="Blocage garanti = déclenche Vengeance ; le geste n°1 du tank sur les élites."),
    M("TAI Tonnerre", "Coup de tonnerre (Combat), Maj : Brise-genou", "Maj+Q", """
#showtooltip
/startattack
/cast ?[mod:shift] {hamstring}; [nostance:1] {battle_stance}; {thunder_clap}
""", ctx="groupe", default=False,
      why="Ralentit les attaques de tous les monstres autour : menace de zone et moins de dégâts."),
    M("TAI Victoire", "Ivresse de la victoire après un kill", "Maj+2", """
#showtooltip
/startattack
/cast {victory_rush}
""", ctx="solo",
      why="Rendue disponible par chaque monstre tué qui rapporte de l'XP : soin + dégâts gratuits, "
          "parfait en quête pour enchaîner sans manger."),
    M("TAI Peur", "Cri d'intimidation (Maj : Hurlement perçant)", "-", """
#showtooltip
/cast ?[mod:shift] {piercing_howl}; {intim_shout}
""", default=False,
      why="Fait fuir les monstres autour : sauve un pull raté (attention aux adds qui fuient)."),
]

# ================================================================ PALADIN
PALADIN = [
    M("TAI Lumiere", "Lumière sacrée (Maj : Éclair lumineux) survol > cible > toi", "1", f"""
#showtooltip
/cast ?{hm("shift")} {{flash_light}}; {HELP} {{holy_light}}
""",
      why="Soin sans changer de cible : survol d'abord, sinon ta cible amicale, sinon toi (Alt = toi). "
          "Éclair lumineux sur Maj : plus rapide et moins cher pour les petits dégâts."),
    M("TAI Jugement", "Jugement (Maj : Jugement rapide) + attaque", "2", f"""
#showtooltip
/startattack [harm,nodead]
/cast ?{mo("shift")} {{swift_judgement}}; {MO} {{judgement}}
""",
      why="Le Jugement est hors temps de recharge global dans Forever et ne consomme plus le sceau : "
          "à utiliser à chaque recharge. Jugement rapide (talent Protection) a sa propre recharge."),
    M("TAI Sceau", "Sceau de piété (Maj : Croisé, Ctrl : Justice, Alt : Lumière)", "3", """
#showtooltip
/cast ?[mod:shift] {seal_crusader}; ?[mod:ctrl] {seal_justice}; ?[mod:alt] {seal_light}; {seal_command}
""", """
#showtooltip
/cast ?[mod:shift] {seal_crusader}; ?[mod:ctrl] {seal_justice}; ?[mod:alt] {seal_light}; {seal_righteousness}
""", specs="vindicte",
      why="Sceau d'autorité dès le talent (gros coups en arme lente), sinon Sceau de piété. Justice "
          "empêche les fuyards, Lumière te soigne en tapant."),
    M("TAI Sceau", "Sceau de fureur (Maj : piété, Ctrl : Justice, Alt : Sagesse)", "3", """
#showtooltip
/cast ?[mod:shift] {seal_righteousness}; ?[mod:ctrl] {seal_justice}; ?[mod:alt] {seal_wisdom}; {seal_fury}
""", """
#showtooltip
/cast {seal_righteousness}
""", specs="protection",
      why="Sceau de fureur (Forever) : dégâts Sacrés et bouclier d'absorption à chaque coup avec un "
          "bouclier équipé — le sceau du tank."),
    M("TAI Sceau", "Sceau de sagesse (Maj : piété, Ctrl : Lumière, Alt : Croisé)", "3", """
#showtooltip
/cast ?[mod:shift] {seal_righteousness}; ?[mod:ctrl] {seal_light}; ?[mod:alt] {seal_crusader}; {seal_wisdom}
""", """
#showtooltip
/cast ?[mod:ctrl] {seal_light}; ?[mod:alt] {seal_crusader}; {seal_righteousness}
""", specs="sacre",
      why="En soigneur, Sceau de sagesse + Jugement rend du mana à tout le groupe."),
    M("TAI Frappe", "Frappe sacrée (Maj : Consécration) + attaque", "4", """
#showtooltip
/startattack
/cast ?[mod:shift] {consecration}; {holy_strike}
""",
      why="Frappe sacrée (Forever, niveau 6) est ton coup instantané de mêlée ; Consécration pour les "
          "packs (et la menace du tank)."),
    M("TAI Exorcisme", "Exorcisme (Maj : Colère divine, Ctrl : Marteau de courroux)", "5", f"""
#showtooltip
/cast ?[mod:shift] {{holy_wrath}}; ?{mo("ctrl")} {{hammer_wrath}}; {MO} {{exorcism}}
""",
      why="Exorcisme : gros dégâts sur morts-vivants et démons ; Marteau de courroux achève sous 20 %."),
    M("TAI Marteau", "Marteau de la justice (Maj : Repentir) — interrompt", "Maj+4", f"""
#showtooltip
/stopcasting
/cast ?{mo("shift")} {{repentance}}; {MO} {{hoj}}
""",
      why="/stopcasting coupe ton soin en cours pour étourdir tout de suite un lanceur de sorts."),
    M("TAI Bulle", "Bouclier divin (Ctrl : Imposition des mains)", "Maj+V", f"""
#showtooltip
/stopcasting
/cast ?{hm("ctrl")} {{loh}}; {{divine_shield}}
""", f"""
#showtooltip
/stopcasting
/cast ?{hm("ctrl")} {{loh}}; {{divine_protection}}
""",
      why="Immunité d'urgence (interrompt ton sort) ; Ctrl = Imposition des mains sur l'allié survolé."),
    M("TAI Purifier", "Épuration / Purification : survol > cible > toi", "6", f"""
#showtooltip
/cast {HELP} {{cleanse}}
""", f"""
#showtooltip
/cast {HELP} {{purify}}
""",
      why="Retire poison / maladie (et magie avec Épuration) sans changer de cible."),
    M("TAI BoP", "Bénédiction de protection (Maj : liberté)", "Maj+6", f"""
#showtooltip
/cast ?{hm("shift")} {{freedom}}; {HELP} {{bop}}
""", ctx="groupe",
      why="Sauve un allié frappé au corps à corps (le monstre change de cible) ; Maj = Liberté contre "
          "ralentissements."),
    M("TAI Benediction", "Puissance (Maj : Sagesse, Ctrl : Rois)", "7", f"""
#showtooltip
/cast ?[mod:shift,help,nodead][mod:shift,@player] {{bow}}; ?[mod:ctrl,help,nodead][mod:ctrl,@player] {{bok}}; [help,nodead][@player] {{bom}}
""",
      why="Sur ta cible amicale sinon sur toi : Puissance pour les physiques, Sagesse pour les "
          "lanceurs, Rois pour tous."),
    M("TAI Aura", "Aura de dévotion (Maj : concentration, Ctrl : vindicte)", "-", """
#showtooltip
/cast ?[mod:shift] {aura_conc}; ?[mod:ctrl] {aura_retribution}; {aura_devotion}
""",
      why="Une touche, trois auras : armure (tank/solo), concentration (soins sans interruption), "
          "vindicte (dégâts renvoyés en farm)."),
    M("TAI Fureur", "Fureur vertueuse (Maj : la retirer)", "-", """
#showtooltip
/cancelaura [mod:shift] {righteous_fury}
/stopmacro [mod:shift]
/cast {righteous_fury}
""", specs="protection", ctx="groupe",
      why="Indispensable pour tanker (menace des sorts Sacrés), à retirer en solo pour ne pas tout "
          "attirer."),
    M("TAI Bouclier", "Bouclier sacré + attaque", "Q", """
#showtooltip
/startattack
/cast {holy_shield}
""", specs="protection",
      why="Plus de blocage et dégâts Sacrés à chaque blocage : à garder actif en tankant."),
    M("TAI Horion", "Horion sacré (soin ou dégâts) (Maj : Vigile de Lumière)", "Q", """
#showtooltip
/cast ?[mod:shift,@mouseover,help,nodead][mod:shift,help,nodead] {lights_vigil}; [@mouseover,exists,nodead][exists,nodead][@player] {holy_shock}
""", specs="sacre",
      why="Horion sacré soigne un allié ou frappe un ennemi selon qui est survolé / ciblé. Vigile : le "
          "prochain Horion sur cet allié soigne tout le groupe sans recharge."),
    M("TAI Rez", "Rédemption sur l'allié mort survolé", "-", f"""
#showtooltip
/cast {DEAD} {{redemption}}
""", ctx="groupe", default=False,
      why="Ressuscite le corps survolé sans devoir le cibler."),
]

# ================================================================ HUNTER (the user's class)
_SHOT = """
#showtooltip
/cast ?[mod:shift] {{multi_shot}}; {main}
/cast !{{auto_shot}}
/petattack [harm,nodead,nomod:alt]
"""
_SHOT_LOW = """
#showtooltip
/cast !{auto_shot}
/petattack [harm,nodead,nomod:alt]
"""
HUNTER = [
    M("TAI Tir", "Tir auto + familier + Invocation de faucon (Maj : Flèches multiples)", "1",
      _SHOT.format(main="?[mod:ctrl] {arcane_shot}; {summon_hawk}"),
      _SHOT.format(main="{arcane_shot}"), _SHOT_LOW, specs="bete",
      why="Un seul bouton à spammer : le « ! » garde le Tir automatique sans le couper, le familier "
          "attaque ta cible (Alt = il reste), puis ton tir instantané. Invocation de faucon (talent "
          "Bêtes) partage la recharge du Tir des arcanes : elle le remplace, Ctrl garde l'Arcane."),
    M("TAI Tir", "Tir auto + familier + Tir des arcanes (Maj : multiples, Ctrl : Visée)", "1",
      _SHOT.format(main="?[mod:ctrl] {aimed_shot}; {arcane_shot}"), _SHOT_LOW,
      specs=("precision", "survie"),
      why="Le « ! » garde le Tir automatique, le familier part sur ta cible (Alt = il reste). Visée "
          "(Ctrl) se lance juste après un tir auto pour ne pas le décaler."),
    M("TAI Marque", "Marque du chasseur (survol) puis familier", "2", f"""
#showtooltip
/cast {MO} {{hunters_mark}}
/petattack [nomod:alt,harm,nodead]
""",
      why="Ouvre le combat : marque (survol prioritaire) puis envoie le familier — il prend la menace "
          "avant ton premier tir."),
    M("TAI Morsure", "Morsure de serpent (Maj : vipère, Ctrl : scorpide)", "3", f"""
#showtooltip
/cast ?{mo("shift")} {{viper_sting}}; ?{mo("ctrl")} {{scorpid_sting}}; {MO} {{serpent_sting}}
""",
      why="Une piqûre par cible, posable au survol sur un 2e monstre sans changer de cible. Vipère "
          "sur les lanceurs de sorts, scorpide sur les gros frappeurs."),
    M("TAI Controle", "Trait de choc (Maj : dispersion, Ctrl : effrayer bête, Alt : Intimidation)",
      "4", f"""
#showtooltip
/cast ?{mo("shift")} {{scatter_shot}}; ?{mo("ctrl")} {{scare_beast}}; ?[mod:alt] {{intimidation}}; {MO} {{concussive_shot}}
""",
      why="Tous tes contrôles sur une touche. Effrayer une bête au survol garde ta cible ; "
          "Intimidation (talent) est hors temps de recharge global."),
    M("TAI Melee", "Mangouste / Raptor (Maj : Coupure d'ailes, Ctrl : Contre-attaque)", "Q", """
#showtooltip
/startattack
/cast ?[mod:shift] {wing_clip}; ?[mod:ctrl] {counterattack}; ?[mod:alt] {strider_kick}; ?{mongoose_bite}
/cast {raptor_strike}
""",
      why="Attaque du raptor remplace ton prochain coup (hors temps de recharge global) : elle part "
          "avec Mangouste dans le même appui. Coupure d'ailes pour reprendre ta distance."),
    M("TAI Pieges", "Piège givrant (Maj : givre, Alt : explosif, Ctrl : immolation)", "E", """
#showtooltip
/cast ?[mod:shift] {frost_trap}; ?[mod:alt] {explosive_trap}; ?[mod:ctrl] {immolation_trap}; {freezing_trap}
""", """
#showtooltip
/cast {immolation_trap}
""",
      why="Tous les pièges partagent une recharge : un seul bouton, le modificateur choisit."),
    M("TAI FD Piege", "Feindre la mort puis Piège givrant (2e appui)", "Maj+E", """
#showtooltip
/stopcasting
/stopattack
/petfollow
/castsequence reset=combat/8 {feign_death}, {freezing_trap}
""",
      why="Coupe ta Salve, fait revenir le familier, Feindre la mort puis piège au 2e appui : le grand "
          "classique pour sortir du combat et contrôler l'add."),
    M("TAI Aspects", "Faucon (Maj : guépard, Ctrl : meute, Alt : singe), sans annuler", "X", """
#showtooltip
/cast ?[mod:shift] !{aspect_cheetah}; ?[mod:ctrl] !{aspect_pack}; ?[mod:alt] !{aspect_monkey}; !{aspect_hawk}
""", """
#showtooltip
/cast !{aspect_monkey}
""",
      why="Le « ! » évite d'éteindre l'aspect en rappuyant. Pense à repasser en Faucon après le "
          "Guépard (hébétement si tu es frappé)."),
    M("TAI Pet", "Familier : attaque survol / Maj suivre / Ctrl passif / Alt défensif", "F", """
/petattack [nomod,@mouseover,harm,nodead][nomod]
/petfollow [mod:shift]
/petpassive [mod:ctrl]
/petdefensive [mod:alt]
""", icon="Ability_Hunter_Pet_Wolf",
      why="Contrôle complet du familier sans barre de familier : envoie-le sur le monstre survolé, "
          "rappelle-le (Maj) ou empêche-le de puller (Ctrl)."),
    M("TAI Grondement", "Grondement auto : coupé en groupe, actif en solo", "-", """
/petautocastoff [group] {growl}
/petautocaston [nogroup] {growl}
""", icon="Ability_Physical_Taunt",
      why="En groupe le familier ne doit pas voler les monstres au tank ; en solo il doit les garder. "
          "Un appui règle le Grondement automatique selon ta situation."),
    M("TAI Familier", "Guérison / Appel (Maj : ressusciter, Ctrl : nourrir)", "V", """
#showtooltip
?/cast [mod:ctrl] {feed_pet}
?/use [mod:ctrl] {item:petfood}
/stopmacro [mod:ctrl]
/cast [mod:shift][@pet,dead] {revive_pet}; [nopet] {call_pet}; {mend_pet}
""", """
#showtooltip
/cast [mod:shift][@pet,dead] {revive_pet}; {call_pet}
""",
      why="Une touche pour la vie du familier : appel s'il est absent, résurrection s'il est mort, "
          "guérison sinon. Ctrl le nourrit avec la nourriture du sac adaptée à son régime."),
    M("TAI Burst", "Tir rapide + Courroux bestial + racial + bijoux", "R", f"""
#showtooltip {{rapid_fire}}
/cast {{rapid_fire}}
?/cast {{bestial_wrath}}
{RACIALS}
{TRINKETS}
""",
      why="Tir rapide et Courroux bestial ne déclenchent pas le temps de recharge global : tout part "
          "en un appui (lignes ajoutées seulement si tu as le racial / un bijou à utiliser)."),
    M("TAI Defense", "Désengagement (Maj : Dissuasion)", "Maj+Q", """
#showtooltip
/cast ?[mod:shift] {deterrence}; {disengage}
""",
      why="Désengagement retire de la menace en mêlée ; Dissuasion (talent Survie) pare tout 10 s."),
    M("TAI Tranqui", "Tir tranquillisant (enrage) sur survol ou cible", "-", f"""
#showtooltip
/cast {MO} {{tranq_shot}}
""", ctx="groupe", default=False,
      why="Dissipe l'enrager d'un boss sans changer de cible."),
    M("TAI Fusee", "Fusée éclairante (Maj : enchantée) au curseur", "-", """
#showtooltip
/cast ?[mod:shift,@cursor] {enchanted_flare}; [@cursor] {flare}
""", default=False,
      why="Révèle les voleurs camouflés là où tu pointes, sans cercle de visée."),
    M("TAI Pistage", "Pistage des bêtes (Maj : humanoïdes)", "-", """
#showtooltip
/cast ?[mod:shift] {track_humanoids}; {track_beasts}
""", default=False, ctx="solo",
      why="Bêtes pour les quêtes de chasse, humanoïdes pour les camps."),
]

# ================================================================ ROGUE
_OPEN = """
#showtooltip
/cast [nostealth,nocombat] {{stealth}}; ?[stealth,mod:shift] {{{a}}}; ?[stealth,mod:ctrl] {{garrote}}; ?[stealth] {{{b}}}; {{sinister_strike}}
"""
ROGUE = [
    M("TAI Ouverture", "Camouflage puis Embuscade (Maj : Coup bas, Ctrl : Garrot)", "1",
      _OPEN.format(a="cheap_shot", b="ambush"), specs=("assassinat", "finesse"),
      why="Même touche : Camouflage hors combat, puis l'ouverture (Embuscade, dague requise). En "
          "combat elle donne Attaque pernicieuse, jamais un appui perdu."),
    M("TAI Ouverture", "Camouflage puis Coup bas (Maj : Embuscade, Ctrl : Garrot)", "1",
      _OPEN.format(a="ambush", b="cheap_shot"), specs="combat",
      why="Combat joue souvent épée/masse : Coup bas (étourdit) ouvre ; Embuscade sur Maj si dague."),
    M("TAI Combo", "Mutilation (Maj : Attaque sournoise)", "2", """
#showtooltip
/startattack
/cast ?[mod:shift] {backstab}; {mutilate}
""", """
#showtooltip
/startattack
/cast ?[mod:shift] {backstab}; {sinister_strike}
""", specs="assassinat",
      why="Générateur de points de combo : Mutilation (talent, +dégâts sur cible empoisonnée), sinon "
          "Attaque pernicieuse ; Attaque sournoise dans le dos."),
    M("TAI Combo", "Attaque pernicieuse (Ctrl : Surin main gauche)", "2", """
#showtooltip
/startattack
/cast ?[mod:ctrl] {shank}; ?[mod:shift] {backstab}; {sinister_strike}
""", specs="combat",
      why="Attaque pernicieuse pour les points ; Surin (Forever, niveau 24) frappe de la main gauche et "
          "rend de l'énergie."),
    M("TAI Combo", "Hémorragie (Maj : Attaque sournoise, Ctrl : Frappe fantomatique)", "2", """
#showtooltip
/startattack
/cast ?[mod:shift] {backstab}; ?[mod:ctrl] {ghostly_strike}; {hemorrhage}
""", """
#showtooltip
/startattack
/cast ?[mod:shift] {backstab}; ?[mod:ctrl] {ghostly_strike}; {sinister_strike}
""", specs="finesse",
      why="Hémorragie (talent Finesse) augmente les dégâts physiques du groupe sur la cible."),
    M("TAI Finish", "Éviscération (Maj : Aiguillon, Ctrl : Débiter, Alt : Rupture)", "3", """
#showtooltip
/startattack
/cast ?[mod:shift] {kidney_shot}; ?[mod:ctrl] {slice_dice}; ?[mod:alt] {rupture}; {eviscerate}
""",
      why="Tous les coups de grâce sur une touche : Débiter d'abord (vitesse), Éviscération pour "
          "achever, Aiguillon perfide pour étourdir un élite."),
    M("TAI Kick", "Coup de pied (Maj : Suriner) — interrompt", "4", f"""
#showtooltip
/cast ?{mo("shift")} {{gouge}}; {MO} {{kick}}
""",
      why="Interruption au survol possible sur un 2e lanceur à portée sans perdre ta cible."),
    M("TAI Assommer", "Vol à la tire + Assommer (survol)", "5", f"""
#showtooltip {{sap}}
?/cast [stealth,@mouseover,harm,nodead][stealth] {{pick_pocket}}
/cast {MO} {{sap}}
""",
      ctx="groupe",
      why="Vol à la tire ne déclenche pas le temps de recharge global : tu voles puis assommes dans le "
          "même appui (survol prioritaire pour assommer l'add de côté)."),
    M("TAI Fuite", "Évasion (Maj : Disparition, Ctrl : Sprint)", "R", """
#showtooltip
/cast ?[mod:shift] {vanish}; ?[mod:ctrl] {sprint}; {evasion}
""", """
#showtooltip
/cast {sprint}
""",
      why="Tous tes boutons de survie : Évasion contre la mêlée, Disparition pour tout lâcher."),
    M("TAI Cecite", "Cécité sur survol ou cible", "Maj+5", f"""
#showtooltip
/cast {MO} {{blind}}
""",
      why="Contrôle d'un add au survol sans lâcher ta cible."),
    M("TAI Burst", "Poussée d'adrénaline / Déluge de lames + racial + bijoux", "Maj+R", f"""
#showtooltip
/cast ?[mod:shift] {{blade_flurry}}; {{adrenaline_rush}}
{RACIALS}
{TRINKETS}
""", f"""
#showtooltip
/cast {{blade_flurry}}
{RACIALS}
{TRINKETS}
""", specs="combat",
      why="Cooldowns du Combat ; racial et bijoux « Utiliser » partent dans le même appui."),
    M("TAI Burst", "Sang froid + Éviscération", "Maj+R", """
#showtooltip {eviscerate}
/cast {cold_blood}
/cast {eviscerate}
""", specs="assassinat",
      why="Sang froid est hors temps de recharge global : critique garanti sur l'Éviscération lancée "
          "dans le même appui."),
    M("TAI Marque", "Marque de mort (coup de grâce différé)", "-", """
#showtooltip
/startattack
/cast {mark_of_death}
""", default=False,
      why="Forever, niveau 40 : stocke une partie de tes dégâts puis les inflige d'un coup ; sur les "
          "cibles qui vivent au moins sa durée."),
    M("TAI Distraction", "Distraction au curseur", "-", """
#showtooltip
/cast [@cursor] {distraction}
""", default=False,
      why="Retourne une patrouille sans cercle de visée pour passer dans son dos."),
]

# ================================================================ PRIEST
PRIEST = [
    M("TAI Soin", "Soins (Maj : rapides) survol > cible > toi", "1", f"""
#showtooltip
/cast ?{hm("shift")} {{flash_heal}}; {HELP} {{heal}}
""", f"""
#showtooltip
/cast {HELP} {{lesser_heal}}
""",
      why="Soin sans changer de cible (survol, puis cible amicale, puis toi ; Alt = toi). Soins rapides "
          "sur Maj quand ça presse."),
    M("TAI Gros soin", "Soins supérieurs survol > cible > toi", "Maj+1", f"""
#showtooltip
/cast {HELP} {{greater_heal}}
""", specs=("discipline", "sacre"), ctx="groupe",
      why="Le soin le plus efficace en mana sur le tank."),
    M("TAI Bouclier", "Mot de pouvoir : Bouclier (Maj : Plan d'urgence)", "2", f"""
#showtooltip
/cast ?{hm("shift")} {{contingency}}; {HELP} {{pw_shield}}
""",
      why="Absorbe les dégâts et empêche l'interruption des sorts : posé sur toi en solo avant "
          "d'incanter, sur le tank en groupe."),
    M("TAI Renovation", "Rénovation (Maj : Prière de guérison)", "3", f"""
#showtooltip
/cast ?{hm("shift")} {{prayer_mending}}; {HELP} {{renew}}
""",
      why="Soin sur la durée au survol ; Prière de guérison (talent Sacré) rebondit sur le groupe."),
    M("TAI Douleur", "Mot de l'ombre : Douleur (Ctrl : Peste dévorante)", "4", f"""
#showtooltip
/cast ?{mo("ctrl")} {{devouring_plague}}; {MO} {{sw_pain}}
""",
      why="DoT posable au survol sur plusieurs monstres sans changer de cible."),
    M("TAI Attaque", "Attaque mentale (Maj : Fouet mental)", "5", f"""
#showtooltip
/cast ?{mo("shift")} {{mind_flay}}; {MO} {{mind_blast}}
""", f"""
#showtooltip
/cast {MO} {{smite}}
""", specs="ombre",
      why="Attaque mentale en ouverture, Fouet mental (talent) pour finir et ralentir."),
    M("TAI Attaque", "Châtiment (Maj : Flammes sacrées, Ctrl : Attaque mentale)", "5", f"""
#showtooltip
/cast ?{mo("shift")} {{holy_fire}}; ?{mo("ctrl")} {{mind_blast}}; {MO} {{smite}}
""", specs=("discipline", "sacre"),
      why="En Sacré / Discipline, Flammes sacrées ouvre, Châtiment remplit."),
    M("TAI Penitence", "Pénitence : soigne l'allié ou frappe l'ennemi visé", "Q", """
#showtooltip
/cast [@mouseover,exists,nodead][exists,nodead][@player] {penance}
""", specs="discipline",
      why="Pénitence (talent Discipline, Forever) soigne un allié OU frappe un ennemi : elle suit le "
          "survol, puis ta cible, puis toi."),
    M("TAI Dissip", "Dissipation de la magie (Maj : maladies) survol > cible > toi", "6", f"""
#showtooltip
/cast ?{hm("shift")} {{abolish_disease}}; {HELP} {{dispel}}
""", f"""
#showtooltip
/cast ?{hm("shift")} {{cure_disease}}; {HELP} {{dispel}}
""",
      why="Retire les effets magiques négatifs d'un allié survolé."),
    M("TAI Peur", "Cri psychique (coupe ton sort)", "Maj+5", """
#showtooltip
/stopcasting
/cast {psychic_scream}
""", ctx="solo",
      why="/stopcasting puis peur instantanée quand deux monstres te tapent. À éviter en donjon."),
    M("TAI Urgence", "Prière du désespoir (Maj : Oubli)", "Maj+V", """
#showtooltip
/stopcasting
/cast ?[mod:shift] {fade}; {desperate_prayer}
""", """
#showtooltip
/cast {fade}
""",
      why="Soin instantané gratuit sur toi ; Oubli (Maj) baisse ta menace quand un monstre te vise."),
    M("TAI Buff", "Robustesse (Maj : Prière, Ctrl : Esprit divin) cible > toi", "-", """
#showtooltip
/cast ?[mod:shift] {prayer_fortitude}; ?[mod:ctrl,help,nodead][mod:ctrl,@player] {divine_spirit}; [help,nodead][@player] {pw_fortitude}
""",
      why="Buffs sur ta cible amicale sinon sur toi ; Prière = tout le groupe."),
    M("TAI Feu int", "Feu intérieur (Maj : Gardien de peur survol)", "-", f"""
#showtooltip
/cast ?{hm("shift")} {{fear_ward}}; {{inner_fire}}
""",
      why="Armure et puissance des sorts ; Gardien de peur sur le tank avant un lanceur de peur."),
    M("TAI Forme", "Forme d'Ombre (Maj : la quitter)", "-", """
#showtooltip
/cancelform [mod:shift]
/cast [noform,nomod] {shadowform}
""", specs="ombre",
      why="Ne se coupe jamais par erreur : [noform] empêche le rappui, Maj la quitte pour soigner."),
    M("TAI Infusion", "Infusion de puissance sur survol (Maj : toi)", "-", f"""
#showtooltip
/cast [mod:shift,@player][@mouseover,help,nodead][@target,help,nodead][@player] {{power_infusion}}
""", specs="discipline", ctx="groupe",
      why="+ dégâts des sorts sur le mage / démoniste survolé."),
    M("TAI Entraves", "Entraves des morts-vivants au survol", "-", f"""
#showtooltip
/cast {MO} {{shackle}}
""", ctx="groupe", default=False,
      why="Contrôle un mort-vivant au survol sans lâcher ta cible."),
    M("TAI Rez", "Résurrection sur l'allié mort survolé", "-", f"""
#showtooltip
/cast {DEAD} {{resurrection}}
""", ctx="groupe", default=False,
      why="Ressuscite le corps survolé sans le cibler."),
]

# ================================================================ SHAMAN
SHAMAN = [
    M("TAI Eclair", "Éclair (Maj : Chaîne d'éclairs) ; Ctrl : Maîtrise élémentaire", "1", f"""
#showtooltip {{lightning_bolt}}
?/cast [mod:ctrl] {{elemental_mastery}}
/cast ?{mo("shift")} {{chain_lightning}}; {MO} {{lightning_bolt}}
""",
      why="Nuke au survol ou sur la cible. Ctrl : le talent Élémentaire (hors temps de recharge global) "
          "part avec l'Éclair dans le même appui = critique garanti."),
    M("TAI Horion", "Horion de terre (Maj : givre, Ctrl : flammes)", "2", f"""
#showtooltip
/cast ?{mo("shift")} {{frost_shock}}; ?{mo("ctrl")} {{flame_shock}}; {MO} {{earth_shock}}
""",
      why="Les horions partagent une recharge : un bouton. Terre interrompt, Givre ralentit, Flammes "
          "brûle dans la durée."),
    M("TAI Coupure", "Interrompt : coupe ton sort + Horion de terre (survol)", "4", f"""
#showtooltip
/stopcasting
/cast {MO} {{earth_shock}}
""", ctx="groupe", default=False,
      why="/stopcasting annule ton Éclair en cours pour interrompre tout de suite."),
    M("TAI Frappe", "Courroux naturel (Maj : Horion de terre) + attaque", "3", """
#showtooltip
/startattack
/cast ?[mod:shift] {earth_shock}; {stormstrike}
""", specs="amelioration",
      why="Talent Amélioration : frappe des deux armes et augmente tes dégâts de Nature."),
    M("TAI Arme", "Furie-des-vents (Maj : Langue de feu, Ctrl : givre)", "-", """
#showtooltip
/cast ?[mod:shift] {flametongue}; ?[mod:ctrl] {frostbrand}; {windfury_weapon}
""", """
#showtooltip
/cast ?[mod:shift] {flametongue}; ?[mod:ctrl] {frostbrand}; {rockbiter}
""", specs="amelioration",
      why="Furie-des-vents dès le niveau 30 sur arme lente à deux mains ; avant, Croque-roc."),
    M("TAI Arme", "Langue de feu (Maj : Croque-roc)", "-", """
#showtooltip
/cast ?[mod:shift] {rockbiter}; {flametongue}
""", """
#showtooltip
/cast {rockbiter}
""", specs=("elementaire", "restauration"),
      why="Lanceur de sorts : Langue de feu sur l'arme suffit."),
    M("TAI Bouclier", "Bouclier de foudre", "-", """
#showtooltip
/cast {lightning_shield}
""",
      why="À garder en permanence en solo : renvoie des dégâts à chaque coup reçu."),
    M("TAI Soin", "Vague de soins (Maj : inférieure) survol > cible > toi", "5", f"""
#showtooltip
/cast ?{hm("shift")} {{lesser_hw}}; {HELP} {{healing_wave}}
""",
      why="Soin sans changer de cible (Alt = toi). Vague inférieure, plus rapide, sur Maj."),
    M("TAI Chaine", "Salve de guérison (Maj : Remous)", "Maj+5", f"""
#showtooltip
/cast ?{hm("shift")} {{riptide}}; {HELP} {{chain_heal}}
""", specs="restauration", ctx="groupe",
      why="Salve rebondit sur 3 alliés ; Remous (talent) d'abord sur la cible renforce la Salve."),
    M("TAI NS Soin", "Rapidité de la nature + Vague de soins instantanée", "Maj+V", f"""
#showtooltip {{natures_swiftness}}
/cast {{natures_swiftness}}
/cast {HELP} {{healing_wave}}
""", specs="restauration",
      why="Rapidité de la nature est hors temps de recharge global : la Vague de soins du même appui "
          "devient instantanée. Le bouton qui sauve un tank."),
    M("TAI Terre", "Force de la Terre (Maj : Séisme, Ctrl : lien, Alt : Griffes)", "-", """
#showtooltip
/cast ?[mod:shift] {tremor_totem}; ?[mod:ctrl] {earthbind_totem}; ?[mod:alt] {stoneclaw_totem}; {strength_earth_totem}
""", """
#showtooltip
/cast ?[mod:ctrl] {earthbind_totem}; {stoneskin_totem}
""",
      why="Un bouton par élément : on ne peut avoir qu'un totem de Terre, les modificateurs "
          "choisissent lequel."),
    M("TAI Feu", "Totem incendiaire (Maj : Magma, Ctrl : Langue de feu)", "-", """
#showtooltip
/cast ?[mod:shift] {magma_totem}; ?[mod:ctrl] {flametongue_totem}; ?[mod:alt] {fire_res_totem}; {searing_totem}
""",
      why="Incendiaire en solo, Magma sur les packs, Langue de feu pour les mêlées."),
    M("TAI Eau", "Fontaine de mana / Guérisseur (Maj : poison, Ctrl : maladie)", "-", """
#showtooltip
/cast ?[mod:shift] {poison_cleansing_totem}; ?[mod:ctrl] {disease_cleansing_totem}; ?[mod:alt] {healing_stream_totem}; {mana_spring_totem}
""", """
#showtooltip
/cast ?[mod:shift] {poison_cleansing_totem}; {healing_stream_totem}
""", specs=("elementaire", "restauration"),
      why="Fontaine de mana pour les lanceurs ; Guérisseur sur Alt."),
    M("TAI Eau", "Totem guérisseur (Maj : poison, Ctrl : maladie)", "-", """
#showtooltip
/cast ?[mod:shift] {poison_cleansing_totem}; ?[mod:ctrl] {disease_cleansing_totem}; ?[mod:alt] {mana_spring_totem}; {healing_stream_totem}
""", specs="amelioration",
      why="Totem guérisseur pour tenir en mêlée ; purification sur Maj / Ctrl."),
    M("TAI Air", "Totem Furie-des-vents (Maj : Glèbe, Ctrl : Mur des vents)", "-", """
#showtooltip
/cast ?[mod:shift] {grounding_totem}; ?[mod:ctrl] {windwall_totem}; ?[mod:alt] {grace_air_totem}; {windfury_totem}
""", ctx="groupe",
      why="Furie-des-vents pour les mêlées du groupe ; Glèbe absorbe un sort ennemi."),
    M("TAI Totems", "Appel des éléments (Maj : Rappel totémique)", "6", """
#showtooltip
/cast [mod:shift] {totemic_recall}; {call_elements}
""",
      why="Forever : pose les 4 totems de ta barre de totems en un seul appui ; Maj les rappelle et "
          "rend du mana."),
    M("TAI Purge", "Expiation sur survol ou cible", "-", f"""
#showtooltip
/cast {MO} {{purge}}
""", ctx="groupe",
      why="Retire un buff magique d'un ennemi survolé."),
    M("TAI Guerison", "Guérison du poison (Maj : maladies) survol > cible > toi", "-", f"""
#showtooltip
/cast ?{hm("shift")} {{cure_disease}}; {HELP} {{cure_poison}}
""",
      why="Nettoie un allié survolé sans changer de cible."),
    M("TAI Loup", "Loup fantôme", "Maj+X", """
#showtooltip
/cast {ghost_wolf}
""", ctx="solo",
      why="+40 % de vitesse en extérieur, entre deux quêtes."),
    M("TAI Rez", "Esprit ancestral sur l'allié mort survolé", "-", f"""
#showtooltip
/cast {DEAD} {{ancestral_spirit}}
""", ctx="groupe", default=False,
      why="Ressuscite le corps survolé."),
]

# ================================================================ MAGE
_BURST_M = f"""
#showtooltip
?/cast {{presence_mind}}
?/cast {{arcane_power}}
?/cast {{combustion}}
{RACIALS}
{TRINKETS}
"""
MAGE = [
    M("TAI Nuke", "Éclair de givre (Maj : Javelot de glace, Ctrl : Givrefeu)", "1", f"""
#showtooltip
/cast ?{mo("shift")} {{ice_lance}}; ?{mo("ctrl")} {{frostfire_bolt}}; {MO} {{frostbolt}}
""", f"""
#showtooltip
/cast {MO} {{fireball}}
""", specs="givre",
      why="Éclair de givre au survol ou sur la cible ; Javelot de glace (talent, Forever) instantané "
          "et bien plus fort sur une cible gelée (après Nova de givre)."),
    M("TAI Nuke", "Boule de feu (Maj : Explosion pyrotechnique, Ctrl : Brûlure)", "1", f"""
#showtooltip
/cast ?{mo("shift")} {{pyroblast}}; ?{mo("ctrl")} {{scorch}}; {MO} {{fireball}}
""", specs="feu",
      why="Boule de feu en continu ; Explosion pyrotechnique en ouverture, Brûlure en mouvement."),
    M("TAI Nuke", "Déflagration des arcanes (Maj : Projectiles, Ctrl : Colère)", "1", f"""
#showtooltip
/cast ?{mo("shift")} {{arcane_missiles}}; ?{mo("ctrl")} {{arcane_wrath}}; {MO} {{arcane_blast}}
""", f"""
#showtooltip
/cast ?{mo("shift")} {{arcane_missiles}}; {MO} {{fireball}}
""", specs="arcanes",
      why="Déflagration des arcanes (talent, Forever) se renforce à chaque lancer mais coûte de plus "
          "en plus de mana : alterne avec Projectiles."),
    M("TAI Instant", "Trait de feu (Maj : Cône de froid, Ctrl : Vague explosive)", "2", f"""
#showtooltip
/cast ?[mod:shift] {{cone_cold}}; ?[mod:ctrl] {{blast_wave}}; {MO} {{fire_blast}}
""",
      why="Tes instantanés pour finir un monstre ou en reculant."),
    M("TAI Nova", "Coupe ton sort + Nova de givre (Maj : Transfert)", "3", """
#showtooltip
/stopcasting
/cast ?[mod:shift] {blink}; {frost_nova}
""",
      why="/stopcasting annule ton sort en cours pour geler / fuir tout de suite (sinon l'appui "
          "est perdu)."),
    M("TAI Contresort", "Coupe ton sort + Contresort (survol)", "4", f"""
#showtooltip
/stopcasting
/cast {MO} {{counterspell}}
""",
      why="Interruption au survol, ton sort en cours est coupé pour ne pas perdre la fenêtre."),
    M("TAI Mouton", "Métamorphose sur survol ou cible", "5", f"""
#showtooltip
/cast {MO} {{polymorph}}
""",
      why="Mouton sur l'add survolé sans perdre ta cible principale."),
    M("TAI AoE", "Explosion des arcanes (Maj : Blizzard, Ctrl : Choc de flammes au curseur)", "6", """
#showtooltip
/cast ?[mod:shift,@cursor] {blizzard}; ?[mod:ctrl,@cursor] {flamestrike}; {arcane_explosion}
""", ctx="groupe",
      why="Blizzard et Choc de flammes tombent directement sous la souris : pas de cercle à poser."),
    M("TAI Defense", "Bloc de glace (Maj : Morsure du froid, Ctrl : Barrière)", "Maj+V", """
#showtooltip
/stopcasting
/cast ?[mod:shift] {cold_snap}; ?[mod:ctrl] {ice_barrier}; {ice_block}
""", """
#showtooltip
/cast {mana_shield}
""",
      why="Immunité d'urgence (coupe ton sort) ; Morsure du froid réinitialise Bloc et Nova."),
    M("TAI Armure", "Armure de glace (Maj : du mage, Alt : Bouclier de mana)", "-", """
#showtooltip
/cast ?[mod:shift] {mage_armor}; ?[mod:alt] {mana_shield}; {ice_armor}
""", """
#showtooltip
/cast ?[mod:alt] {mana_shield}; {frost_armor}
""",
      why="Armure de glace en solo (ralentit les mêlées) ; Armure du mage en groupe (mana)."),
    M("TAI Intel", "Intelligence (Maj : Illumination) survol > cible > toi", "-", f"""
#showtooltip
/cast ?[mod:shift] {{arcane_brilliance}}; {HELP} {{arcane_intellect}}
""",
      why="Buff au survol ; Illumination pour tout le groupe."),
    M("TAI Decurse", "Délivrance de la malédiction mineure : survol > cible > toi", "-", f"""
#showtooltip
/cast {HELP} {{remove_curse}}
""", ctx="groupe",
      why="Retire une malédiction d'un allié survolé."),
    M("TAI Invoc", "Invocation d'eau (Maj : nourriture)", "-", """
#showtooltip
/cast [mod:shift] {conjure_food}; {conjure_water}
""", """
#showtooltip
/cast {conjure_food}
""",
      why="Ta nourriture gratuite : TAI Repas mange les invoqués en premier."),
    M("TAI Gemme", "Invoque la meilleure gemme de mana", "-", "#showtooltip\n/cast {mana_ruby}",
      "#showtooltip\n/cast {mana_citrine}", "#showtooltip\n/cast {mana_jade}",
      "#showtooltip\n/cast {mana_agate}",
      why="La macro suit ta progression : rubis > citrine > jade > agate."),
    M("TAI Evocation", "Évocation", "-", """
#showtooltip
/cast {evocation}
""",
      why="Rend ton mana pendant 8 s : évite de boire entre deux packs."),
    M("TAI Burst", "Présence spirituelle / Pouvoir des arcanes / Combustion + bijoux", "R", _BURST_M,
      specs=("arcanes", "feu"),
      why="Tous ces cooldowns sont hors temps de recharge global : ils partent ensemble, ton sort "
          "suivant en profite."),
]

# ================================================================ WARLOCK
WARLOCK = [
    M("TAI Trait", "Trait de l'ombre (Maj : Douleur brûlante, Ctrl : Incinérer)", "1", f"""
#showtooltip
/cast ?{mo("shift")} {{searing_pain}}; ?{mo("ctrl")} {{incinerate}}; {MO} {{shadow_bolt}}
""",
      why="Nuke au survol ou sur la cible. Incinérer (talent Destruction, Forever) fait plus si la "
          "cible brûle (Immolation)."),
    M("TAI DoT", "Corruption (Maj : Immolation) survol > cible", "2", f"""
#showtooltip
/cast ?{mo("shift")} {{immolate}}; {MO} {{corruption}}
""", f"""
#showtooltip
/cast {MO} {{immolate}}
""",
      why="DoTs posables au survol sur chaque monstre du pack sans jamais changer de cible."),
    M("TAI Maledict", "Agonie (Maj : témérité, Ctrl : éléments) ; Alt : amplifiée", "3", f"""
#showtooltip {{curse_agony}}
?/cast [mod:alt] {{amplify_curse}}
/cast ?{mo("shift")} {{curse_recklessness}}; ?[mod:ctrl] {{curse_elements}}; {MO} {{curse_agony}}
""", f"""
#showtooltip
/cast {MO} {{curse_weakness}}
""",
      why="Une malédiction par cible et par démoniste. Alt : Malédiction amplifiée (talent, hors "
          "temps de recharge global) part avec l'Agonie."),
    M("TAI Siphon", "Siphon de vie (Maj : Calvaire)", "4", f"""
#showtooltip
/cast ?{mo("shift")} {{calvaire}}; {MO} {{siphon_life}}
""", specs="affliction",
      why="Siphon soigne en continu ; Calvaire (Forever, niveau 40) renforce tous tes DoTs d'Ombre."),
    M("TAI Calvaire", "Calvaire sur survol ou cible", "4", f"""
#showtooltip
/cast {MO} {{calvaire}}
""", specs=("demonologie", "destruction"),
      why="Forever, niveau 40 : gros DoT qui augmente les dégâts de tes autres DoTs d'Ombre."),
    M("TAI Conflag", "Conflagration (Maj : Brûlure de l'ombre)", "5", f"""
#showtooltip
/cast ?{mo("shift")} {{shadowburn}}; {MO} {{conflagrate}}
""", """
#showtooltip
/cast {shadowburn}
""", specs="destruction",
      why="Conflagration consomme ton Immolation pour une grosse explosion ; Brûlure pour achever."),
    M("TAI Drain", "Drain de vie (Maj : Siphon d'âme, Ctrl : Drain de mana)", "6", f"""
#showtooltip
/cast ?{mo("shift")} {{drain_soul}}; ?{mo("ctrl")} {{drain_mana}}; {MO} {{drain_life}}
""", f"""
#showtooltip
/cast {MO} {{drain_soul}}
""",
      why="Drain de vie pour tenir sans manger ; Siphon d'âme sur un monstre presque mort = fragment."),
    M("TAI Peur", "Peur (Maj : Hurlement, Ctrl : Voile mortel) survol", "Maj+5", f"""
#showtooltip
/cast ?[mod:shift] {{howl_terror}}; ?{mo("ctrl")} {{death_coil}}; {MO} {{fear}}
""",
      why="Peur au survol sur l'add sans lâcher ta cible ; Voile mortel : peur instantanée + soin."),
    M("TAI Bannir", "Bannir sur survol ou cible", "-", f"""
#showtooltip
/cast {MO} {{banish}}
""", ctx="groupe",
      why="Contrôle sûr des démons et élémentaires."),
    M("TAI Connexion", "Connexion (Maj : Captation de vie vers le démon)", "Q", """
#showtooltip
/cast [mod:shift] {health_funnel}; {life_tap}
""", """
#showtooltip
/cast {life_tap}
""",
      why="Vie -> mana ; Maj soigne ton démon avec ta vie (utile pour garder le marcheur)."),
    M("TAI Demon", "Démon : attaque survol / Maj suivre / Ctrl passif / Alt défensif", "F", """
/petattack [nomod,@mouseover,harm,nodead][nomod]
/petfollow [mod:shift]
/petpassive [mod:ctrl]
/petdefensive [mod:alt]
""", icon="Spell_Shadow_SummonImp",
      why="Contrôle du démon sans barre de familier : envoie-le sur le survol, rappelle-le, "
          "empêche-le de puller."),
    M("TAI Demon sort", "Sort du démon : Verrou / Séduction / Tourment (Maj : Sacrifice)", "Maj+F",
      f"""
#showtooltip
/stopmacro [nopet]
/cast [mod:shift] {{sacrifice}}; {MO} {{torment}}
/cast {MO} {{spell_lock}}
/cast {MO} {{seduction}}
""", icon="Spell_Shadow_MindSteal",
      why="Une touche pour le sort utile de chaque démon : seul celui du démon présent part "
          "(Tourment du marcheur, Verrou magique du chasseur corrompu, Séduction de la succube)."),
    M("TAI Invoc", "Marcheur du Vide (Maj : diablotin, Ctrl : succube, Alt : chasseur)", "-", """
#showtooltip {summon_vw}
?/cast [combat] {fel_domination}
/cast ?[mod:shift] {summon_imp}; ?[mod:ctrl] {summon_succubus}; ?[mod:alt] {summon_fh}; {summon_vw}
""", """
#showtooltip
/cast {summon_imp}
""",
      why="Domination corrompue (talent, hors temps de recharge global) rend l'invocation instantanée "
          "en combat."),
    M("TAI Pierres", "Pierre de soins (Maj : d'âme, Ctrl : de feu, Alt : de sort)", "-", """
#showtooltip
/cast ?[mod:shift] {create_ss}; ?[mod:ctrl] {create_fs}; ?[mod:alt] {create_spellstone}; {create_hs}
""",
      why="Crée la pierre ; TAI Survie l'utilise ensuite (pierre puis potion)."),
    M("TAI Armure", "Armure démoniaque (Maj : Gardien de l'ombre)", "-", """
#showtooltip
/cast ?[mod:shift] {shadow_ward}; {demon_armor}
""", """
#showtooltip
/cast {demon_skin}
""",
      why="Armure + régénération ; Gardien de l'ombre contre un lanceur d'Ombre."),
    M("TAI AoE", "Pluie de feu (Maj : Flammes infernales)", "-", """
#showtooltip
/cast ?[mod:shift] {hellfire}; [@cursor] {rain_fire}
""", ctx="groupe", default=False,
      why="Pluie de feu tombe directement sous la souris."),
]

# ================================================================ DRUID
DRUID = [
    M("TAI Attaque", "Selon la forme : Mutiler (ours), Griffe / Lambeau (félin), Colère", "1", f"""
#showtooltip
/startattack [form:1/3,nostealth]
/cast ?[form:1] {{maul}}; ?[form:3,stealth] {{ravage}}; ?[form:3,mod:shift] {{shred}}; ?[form:3] {{claw}}; {MO} {{wrath}}
""",
      why="Une touche qui suit ta forme : Mutiler en ours (prochain coup, hors temps de recharge "
          "global), Griffe en félin (Maj = Lambeau dans le dos, Ravage en Rôdeur), Colère sinon."),
    M("TAI Fauve", "Ours : Lacérer / Balayage (Maj) ; Félin : coups de grâce", "2", """
#showtooltip
/startattack
/cast ?[form:1,mod:shift] {swipe}; [form:1] {lacerate}; ?[form:3,mod:shift] {rip}; ?[form:3] {ferocious_bite}
""", """
#showtooltip
/startattack
/cast ?[form:1,mod:shift] {demo_roar}; [form:1] {swipe}; ?[form:3,mod:shift] {rip}; ?[form:3] {ferocious_bite}; ?[form:3] {rip}
""", specs="farouche",
      why="Ours : Lacérer (talent, menace) ou Balayage sur Maj pour les packs. Félin : Morsure féroce, "
          "Déchirure sur Maj pour les cibles qui durent."),
    M("TAI Ours", "Forme d'ours redoutable (Maj : retour humanoïde)", "3", """
#showtooltip
/cancelform [mod:shift]
/cast [nomod] !{dire_bear}
""", """
#showtooltip
/cancelform [mod:shift]
/cast [nomod] !{bear}
""",
      why="Le « ! » évite de quitter la forme en rappuyant ; Maj revient en forme normale pour soigner."),
    M("TAI Felin", "Forme de félin, puis Rôder hors combat", "4", """
#showtooltip
/cast [noform:3] !{cat}; [nocombat,nostealth] {prowl}
""", """
#showtooltip
/cast !{cat}
""",
      why="1er appui : félin ; 2e appui hors combat : Rôder. Pas de risque de casser la forme."),
    M("TAI Rodeur", "Ouverture félin : Traquenard (Maj : Ravage)", "Maj+4", """
#showtooltip
/cast [noform:3] !{cat}; ?[stealth,mod:shift] {ravage}; ?[stealth] {pounce}; {rake}
""", specs="farouche", ctx="solo",
      why="Depuis Rôder : Traquenard (étourdit) ou Ravage dans le dos ; en combat : Griffure."),
    M("TAI Provoc", "Grondement (survol) en ours (Maj : Rugissement provocateur)", "T", f"""
#showtooltip
/cast [noform:1] !{{bear}}; ?[mod:shift] {{challenging_roar}}; {MO} {{growl}}
""", specs="farouche", ctx="groupe",
      why="Reprend le monstre qui tape un allié survolé ; passe en ours d'abord."),
    M("TAI Charge", "Charge farouche (ours) ; sinon Sarments au survol", "Maj+1", f"""
#showtooltip
/cast [form:1] {{feral_charge}}; {MO} {{roots}}
""", f"""
#showtooltip
/cast ?[form:1] {{bash}}; {MO} {{roots}}
""", f"""
#showtooltip
/cast {MO} {{roots}}
""",
      why="En ours : Charge farouche (talent, interrompt) ou Sonner ; hors forme : Sarments au survol."),
    M("TAI Voyage", "Aquatique dans l'eau, Voyage dehors, Félin dedans", "Maj+X", """
#showtooltip
/cast [swimming] !{aquatic}; ?[outdoors] !{travel}; ?{cat}
""",
      why="Une touche de déplacement qui choisit la bonne forme selon le terrain."),
    M("TAI Soin", "Toucher guérisseur (Maj : Rétablissement) survol > cible > toi", "5", f"""
#showtooltip
/cast ?{hm("shift")} {{regrowth}}; {HELP} {{healing_touch}}
""",
      why="Soin sans changer de cible (Alt = toi)."),
    M("TAI Recup", "Récupération (Maj : Croissance sauvage, Ctrl : Prompte guérison)", "6", f"""
#showtooltip
/cast ?{hm("shift")} {{wild_growth}}; ?[mod:ctrl] {{swiftmend}}; {HELP} {{rejuvenation}}
""",
      why="Soin sur la durée au survol ; Croissance sauvage (talent, Forever) soigne tout le groupe."),
    M("TAI NS Soin", "Rapidité de la nature + Toucher guérisseur instantané", "Maj+V", f"""
#showtooltip {{natures_swiftness}}
/cast {{natures_swiftness}}
/cast {HELP} {{healing_touch}}
""", specs="restauration",
      why="Rapidité de la nature est hors temps de recharge global : le Toucher guérisseur du même "
          "appui devient instantané."),
    M("TAI Eclat", "Éclat lunaire (Maj : Essaim d'insectes) survol", "7", f"""
#showtooltip
/cast ?{mo("shift")} {{insect_swarm}}; {MO} {{moonfire}}
""",
      why="DoT au survol, posable sur plusieurs monstres sans changer de cible."),
    M("TAI Stellaire", "Feu stellaire (Maj : Colère)", "8", f"""
#showtooltip
/cast ?{mo("shift")} {{wrath}}; {MO} {{starfire}}
""", specs="equilibre",
      why="Gros nuke d'Équilibre ; Colère, plus rapide, sur Maj."),
    M("TAI Selenien", "Forme de sélénien (sans la quitter)", "-", """
#showtooltip
/cast !{moonkin}
""", specs="equilibre",
      why="Le « ! » évite de sortir de la forme par erreur."),
    M("TAI Lucioles", "Lucioles sur survol ou cible", "-", f"""
#showtooltip
/cast {MO} {{faerie_fire}}
""", default=False,
      why="Réduit l'armure et empêche le camouflage."),
    M("TAI MdF", "Marque du fauve (Maj : Don, Ctrl : Épines) cible > toi", "-", f"""
#showtooltip
/cast ?[mod:shift] {{gift_wild}}; ?{hm("ctrl")} {{thorns}}; {HELP} {{motw}}
""",
      why="Buffs au survol ; Épines sur le tank."),
    M("TAI Decurse", "Abolir le poison (Maj : malédiction) survol > cible > toi", "-", f"""
#showtooltip
/cast ?{hm("shift")} {{remove_curse}}; {HELP} {{abolish_poison}}
""", f"""
#showtooltip
/cast {HELP} {{cure_poison}}
""",
      why="Nettoie un allié survolé sans changer de cible."),
    M("TAI Defense", "Écorce (ours : Régénération frénétique)", "Maj+Q", """
#showtooltip
/cast ?[form:1] {frenzied_regen}; {barkskin}
""", """
#showtooltip
/cast [form:1] {enrage}
""",
      why="Ours : Régénération frénétique ; sinon Écorce."),
    M("TAI Innerv", "Innervation sur survol (Maj : toi)", "-", f"""
#showtooltip
/cast [mod:shift,@player][@mouseover,help,nodead][@target,help,nodead][@player] {{innervate}}
""", ctx="groupe",
      why="Mana au soigneur survolé quand il est à sec."),
    M("TAI Rez", "Renaissance en combat, Ressusciter hors combat (survol)", "-", """
#showtooltip
/cast ?[combat,@mouseover,help,dead][combat,help,dead] {rebirth}; [@mouseover,help,dead][help,dead] {revive}
""", ctx="groupe", default=False,
      why="Forever donne au druide Ressusciter (hors combat, niveau 24) : la même touche fait "
          "Renaissance en combat."),
]

_BY_CLASS = {
    "WARRIOR": WARRIOR, "PALADIN": PALADIN, "HUNTER": HUNTER, "ROGUE": ROGUE, "PRIEST": PRIEST,
    "SHAMAN": SHAMAN, "MAGE": MAGE, "WARLOCK": WARLOCK, "DRUID": DRUID,
}


def macros_for(cls: str) -> list[Macro]:
    """Class macros first, then the shared consumable macros that apply to the class."""
    own = [m for m in _BY_CLASS.get(cls, [])]
    shared = [m for m in SHARED if cls in m.classes]
    return own + shared
