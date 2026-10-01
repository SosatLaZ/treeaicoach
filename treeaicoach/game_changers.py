"""GAME CHANGERS: the ranked library of calls that change what a beginner does in the next 10 s.

Real feedback (French beginner, Garen top): "the advice is terrible". Generic lines ("pose une
balise", "farme", "joue prudent") do not change a game; a Master+ coach says the ONE situational
thing that wins the next fight / objective, with its time window. This module is that library.

Each rule reads the same :class:`treeaicoach.macro.MacroCtx` as the macro planner (Live Client:
levels, items, gold, deaths + respawn timers, events, scores; minimap: positions, enemy jungler
location / fog, waves; objective timers 2026) and returns a :class:`treeaicoach.macro.GeniusCall`
(kind ``gc_*``): it goes through the SAME planner (:class:`treeaicoach.macro.MacroPlanner`: one
active call, hold, hysteresis, per-level tiers and gaps, never during a fight / gank / while dead),
so the card line, the banner (title + why) and the minimap arrow always say the same thing.

THE LIBRARY (rank = priority + score; voice: who hears it, see :func:`voice_for`)::

    kind                 trigger (all: alive, no fight, no gank threat)          card line                                   voice
    fight_won (macro)    2-3 enemies dead, net +2, respawn window covers walk+take "Prends le Baron maintenant : 3 ennemis..."  "Ils sont 3 morts : Baron !"  (big)
    jungler_dead (macro) their jungler dead >= 15 s, objective up for my role     "Prends le dragon maintenant : Lee Sin..."   "Leur jungler est mort : dragon !" (big)
    plates (macro)       lane opponent dead / in his base, my lane, tower up       "Plaque la tour (25 s) : Darius est mort"    "Il est mort : prends la plaque !" (lane)
    cross_trade (macro)  enemy jungler / 3+ enemies committed on the other side    "Frappe leur tour du haut maintenant : ..."  "Jungler en bas : frappe la tour !" (lane)
    gc_level             I reach 2 / 3 / 6 first in lane (window until he catches up) "Frappe Darius maintenant : tu es niveau 2, pas lui" "Niveau d'avance : frappe-le !" (lane)
    gc_level             HE reaches 2 / 3 / 6 first                                 "Recule vers ta tour : Darius est niveau 6, pas toi" "Il a son ultime : recule !" (lane)
    gc_jungler_far       enemy jungler SEEN on the other half (<= 10 s), laning    "Joue agressif : Lee Sin est en bas"         "Leur jungler est en bas : avance !" (lane)
    gc_jungler_unseen    jungler invisible >= 30 s + my wave pushed / me forward   "Recule vers ta tour : vague poussée, ..."   "Jungler invisible : recule !" (lane)
    wave_recall (macro)  wave crashed into their tower + gold (>= 1300) / low HP   "Rentre en base maintenant : ta vague ..."   "Ta vague est poussée : rentre !" (lane)
    gc_baron_setup       >= 20:00, Baron up, team gold lead >= 2500, no window     "Pose des balises au Baron puis prends-le"   "Vous êtes devant : balises au Baron." (big)
    gc_fed_defense       in the shop, an enemy fed (4+ kills), no armour / MR yet "Achète Cotte de mailles : Darius est trop fort (5/0)" -
    gc_facecheck         >= 20:00, alone in river / jungle, 3+ enemies unseen     "Ne va pas dans les buissons : 3 ennemis invisibles" "Pas de buisson sans balise !" (lane)

    (+ phase.EndGameCaller: ace -> "Va au Baron maintenant : 5 ennemis morts", spoken ("urgent:ace:"))

THE VOICE (:func:`voice_for`, keys ``gc:big:<kind>:<ident>`` / ``gc:lane:<kind>:<ident>``): a short
line (<= :data:`VOICE_MAX_CHARS` characters, < 2.5 s spoken), static wording (no champion name, no
live number but the count of dead enemies) so every line is pre-generated (:func:`voice_phrases`,
:func:`treeaicoach.tts_neural.static_phrases`). ``big`` calls (objective now, Baron set-up) are
spoken for the beginner and intermediate levels, ``lane`` calls for the beginner only.
:class:`treeaicoach.voice_policy.VoiceGate` drops them during a fight / high concentration / while
dead (the card already shows them: voice-only), its budget allows ~1-2 per minute, and one topic
is never spoken twice within :data:`VOICE_TOPIC_S`.

Riot policy: public Live Client data + the minimap pixels shown to the player. No enemy cooldown,
summoner spell or ultimate tracking. Pure Python, never raises from its public functions.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any

from treeaicoach import geometry
from treeaicoach import macro as M
from treeaicoach.fmtutil import finite as _f

log = logging.getLogger(__name__)

VOICE_MAX_CHARS = 38            # ~2.5 s at the neural voice's +15 % rate
VOICE_TOPIC_S = 60.0            # one topic is never spoken twice within this
#: skill levels hearing each voice class
VOICE_LEVELS: dict[str, frozenset[str]] = {"big": frozenset({"debutant", "intermediaire"}),
                                           "lane": frozenset({"debutant"})}
LANING_END_GT = M.LANING_END_GT

#: lane rules: the level race windows (2 / 3: first all-in windows; 6: the ultimate)
LEVEL_RACE = {2: (200.0, 0.75, "mid"), 3: (300.0, 0.62, "basic"), 6: (660.0, 0.80, "mid")}
LEVEL_LIFE_S = {2: 18.0, 3: 18.0, 6: 30.0}
JG_SEEN_MAX_AGE_S = 10.0        # "seen on the other side": at most this old
JG_UNSEEN_MIN_S = 30.0          # "invisible": not seen for at least this long
BARON_SETUP_GOLD = 2500
FED_MIN_KILLS = 4

#: champion ultimate, concrete (card line with {opp}, one-line why) - used for the level 6 line /
#: window ("Garde ton R pour achever Darius quand il est bas"). Champions not listed: no ult line.
ULT: dict[str, tuple[str, str]] = {
    "Garen": ("Garde ton R pour achever {opp} quand il est bas",
              "Ton R tape d'autant plus qu'il lui manque de la vie : lance-le en dernier."),
    "Darius": ("Mets 5 saignements à {opp} puis R pour l'achever",
               "Ton R fait le maximum à 5 marques et se relance s'il tue."),
    "Urgot": ("Lance ton R sur {opp} sous 25 % de vie",
              "Sous 25 % de vie, ton R l'exécute et fait peur à ses alliés."),
    "Pyke": ("Lance ton R sur un ennemi bas : il l'exécute",
             "Ton R achève sous le seuil et rend l'or à un allié."),
    "Chogath": ("Garde ton R pour achever {opp} : dégâts bruts",
                "Ton R ignore les résistances : garde-le pour finir le combat."),
    "Veigar": ("Garde ton R pour achever {opp} quand il est bas",
               "Ton R tape d'autant plus que sa vie est basse."),
    "Riven": ("Garde ton 2e R pour achever {opp}",
              "Le 2e tir de ton R fait plus de dégâts aux cibles blessées."),
    "Fiora": ("Lance ton R sur {opp} et touche ses 4 points vitaux",
              "Les 4 points touchés soignent ton équipe autour de toi."),
    "Mordekaiser": ("Lance ton R sur {opp} : un contre un sans aide",
                    "Ton R l'isole 7 s : personne ne peut l'aider."),
    "Renekton": ("Lance ton R au début du combat : plus de vie",
                 "Ton R donne de la vie et de la rage : n'attends pas d'être bas."),
    "Nasus": ("Lance ton R avant le combat : plus de vie et de dégâts",
              "Ton R dure 15 s : lance-le avant d'engager."),
    "Jax": ("Lance ton R au début du combat : plus de résistances",
            "Ton R te rend plus solide pendant tout l'échange."),
    "Irelia": ("Lance ton R sur {opp} pour le ralentir puis attaque",
               "Ton R le ralentit et marque les ennemis touchés."),
    "Camille": ("Lance ton R sur {opp} : il ne peut plus fuir",
                "Ton R l'enferme dans une zone avec toi."),
    "Aatrox": ("Lance ton R au début du combat : plus de soins",
               "Ton R augmente tes soins et ta vitesse."),
    "Tryndamere": ("Lance ton R quand ta vie est très basse",
                   "Pendant ton R tu ne peux pas mourir : 5 s pour gagner l'échange."),
    "Olaf": ("Lance ton R : plus aucun contrôle ne t'arrête",
             "Ton R t'immunise aux contrôles : fonce sur leur tireur."),
    "Shen": ("Garde ton R pour sauver un allié",
             "Ton R protège un allié n'importe où sur la carte."),
    "Trundle": ("Lance ton R sur {opp} : tu lui voles ses résistances",
                "Ton R te rend plus solide et lui retire ses défenses."),
    "Warwick": ("Lance ton R sur {opp} : il est bloqué",
                "Ton R le cloue au sol : tes alliés peuvent le tuer."),
    "Vi": ("Lance ton R sur {opp} : impossible à esquiver",
           "Ton R le suit et le projette : engage sur leur tireur."),
    "LeeSin": ("Utilise ton R pour renvoyer {opp} vers ton équipe",
               "Bien placé, ton R le renvoie vers tes alliés."),
    "Malphite": ("Engage avec ton R sur plusieurs ennemis",
                 "Ton R projette tous les ennemis touchés : vise le groupe."),
    "Malzahar": ("Lance ton R sur {opp} : il ne peut plus bouger",
                 "Ton R le bloque : ton jungler peut le tuer."),
    "Annie": ("Lance ton R quand ton étourdissement est prêt",
              "Avec 4 marques, ton R étourdit tous les ennemis touchés."),
    "Lux": ("Garde ton R pour achever {opp} de loin",
            "Ton R va très loin : achève les ennemis qui fuient."),
    "Ahri": ("Garde ton R pour esquiver ou poursuivre {opp}",
             "Ton R a 3 charges : avance, recule, achève."),
    "Yasuo": ("Lance ton R quand {opp} est projeté en l'air",
              "Ton R ne marche que sur un ennemi projeté en l'air."),
    "Yone": ("Lance ton R en ligne sur plusieurs ennemis",
             "Ton R projette tous les ennemis de la ligne."),
    "Jinx": ("Garde ton R pour achever un ennemi bas, même loin",
             "Ton R traverse la carte et tape plus les cibles blessées."),
    "Ezreal": ("Garde ton R pour achever un ennemi bas, même loin",
               "Ton R traverse la carte."),
    "Caitlyn": ("Garde ton R pour achever {opp} de loin",
                "Ton R vise un ennemi très loin : achève ceux qui fuient."),
    "Ashe": ("Lance ton R sur {opp} pour l'étourdir",
             "Ton R étourdit plus longtemps s'il vient de loin."),
    "Kennen": ("Lance ton R au milieu de plusieurs ennemis",
               "Ton R étourdit tous les ennemis autour de toi."),
    "Gragas": ("Utilise ton R pour renvoyer {opp} vers ton équipe",
               "Bien placé, ton R le pousse vers tes alliés."),
    "Sion": ("Engage avec ton R depuis loin",
             "Ton R projette le premier ennemi touché."),
    "Vladimir": ("Lance ton R au début du combat : dégâts bonus",
                 "Ton R augmente les dégâts que tout le monde leur fait."),
    "Kayle": ("Garde ton R pour te sauver d'une mort",
              "Ton R rend invincible quelques secondes."),
    "Teemo": ("Pose tes champignons dans les buissons de ta voie",
              "Tes champignons te protègent des ganks."),
    "Nautilus": ("Lance ton R sur le tireur ennemi",
                 "Ton R le projette en l'air : ton équipe peut le tuer."),
    "Leona": ("Lance ton R sur plusieurs ennemis groupés",
              "Ton R étourdit au centre et ralentit autour."),
}


def ult_line(alias: Any, opp: Any = None) -> tuple[str, str] | None:
    """``(card line, why)`` of my champion's ultimate (``{opp}`` filled), None when unknown."""
    try:
        row = ULT.get(str(alias or ""))
        if row is None:
            low = str(alias or "").lower()
            row = next((v for k, v in ULT.items() if k.lower() == low), None)
        if row is None:
            return None
        name = str(opp or "ton adversaire")
        return row[0].format(opp=name), row[1].format(opp=name)
    except Exception:
        return None


# ----------------------------------------------------------------------------- helpers
def _my_player(ctx: M.MacroCtx) -> Any:
    return getattr(ctx.game, "me", None)


def _alive_lane_opps(ctx: M.MacroCtx) -> list[Any]:
    out = []
    for a in ctx.lane_opps:
        p = M._player(ctx, a)
        if p is not None:
            out.append(p)
    return out


def _in_my_lane(ctx: M.MacroCtx) -> bool:
    lane = M._my_lane(ctx)
    return lane is not None and ctx.me_uv is not None and M._zone_lane(ctx.me_uv) == lane


def _wave(ctx: M.MacroCtx) -> tuple[float | None, str | None]:
    lw = M._lane_wave(ctx, M._my_lane(ctx))
    if lw is None:
        return None, None
    return _f(M._get(lw, "meet")), M._get(lw, "state")


def _jg_hidden_s(ctx: M.MacroCtx) -> float | None:
    """Seconds the enemy jungler has not been seen (game start when never seen), None if unknown."""
    a = ctx.jungler_alias
    if not a:
        return None
    tr = M._track(ctx, a)
    if tr is None or M._uv(getattr(tr, "uv", None)) is None:
        return max(0.0, ctx.gt - 90.0)                  # never seen since the camps spawned
    if bool(getattr(tr, "visible", False)):
        return 0.0
    return _f(getattr(tr, "hidden_s", None), 99.0)


def _opposite_side(lane: str | None) -> str | None:
    return {"top": "bot", "bot": "top"}.get(lane or "")


def _name(ctx: M.MacroCtx, p: Any) -> str:
    a = str(getattr(p, "champion_alias", "") or "").lower()
    return ctx.enemy_names.get(a) or str(getattr(p, "champion_name", "") or getattr(p, "champion_alias", "") or "")


def _stance_ok(ctx: M.MacroCtx) -> bool:
    """The play gauge is not PRUDENT / SAFE (a "go" call under a cautious gauge would contradict it)."""
    return ctx.stance_score is None or ctx.stance_score > M.STANCE_PRUDENT


# ----------------------------------------------------------------------------- rules
def rule_level_race(ctx: M.MacroCtx) -> M.GeniusCall | None:
    """I reach 2 / 3 / 6 before my lane opponent (attack window) - or he does (step back)."""
    if not ctx.me_alive or ctx.in_base or ctx.role in (None, "JUNGLE") or ctx.gt >= LANING_END_GT:
        return None
    if not _in_my_lane(ctx):
        return None
    opps = [p for p in _alive_lane_opps(ctx) if not bool(getattr(p, "is_dead", False))]
    if not opps:
        return None
    me = _my_player(ctx)
    mine = int(_f(getattr(me, "level", None), 0) or 0)
    theirs = max(int(_f(getattr(p, "level", None), 0) or 0) for p in opps)
    if mine <= 0 or theirs <= 0:
        return None
    # the facing opponent (ADC vs ADC): named in the line
    p0 = max(opps, key=lambda p: int(_f(getattr(p, "level", None), 0) or 0))
    opp = _name(ctx, p0)
    for lvl in (6, 3, 2):
        until, value, tier = LEVEL_RACE[lvl]
        if ctx.gt > until:
            continue
        if mine >= lvl > theirs and (ctx.keep or mine == lvl):
            if ctx.hp is not None and ctx.hp < 0.5:
                return None                              # a lead with half HP is not a window
            jl = M.jungler_location(ctx)
            if not jl.dead and jl.conf >= 0.5 and jl.side == M._my_lane(ctx):
                return None                              # their jungler is on my side: not now
            if not _stance_ok(ctx) and not ctx.keep:
                return None
            ult = ult_line(getattr(me, "champion_alias", None), opp) if lvl == 6 else None
            text = f"Frappe {opp} maintenant : tu as ton ultime, pas lui" if lvl == 6 \
                else f"Frappe {opp} maintenant : tu es niveau {lvl}, pas lui"
            why = ult[1] if ult else (f"Un sort de plus que {opp} pendant ~20 s : un échange maintenant est gagné."
                                      if lvl != 6 else f"{opp} n'a pas encore son ultime : c'est ta fenêtre.")
            voice = "Ton ultime est prêt : frappe-le !" if lvl == 6 else "Niveau d'avance : frappe-le !"
            return replace(M._call("gc_level", f"gc_level:me:{lvl}", f"NIVEAU {lvl} !", text, why,
                                   getattr(M._track(ctx, getattr(p0, "champion_alias", "")), "uv", None),
                                   tier=tier, score=value, priority=79, color="safe", genius=lvl == 6,
                                   life=LEVEL_LIFE_S[lvl], label=opp.upper()[:16] or "VA ICI",
                                   factors=(f"niveau {mine} contre {theirs}",)), voice=voice)
        if theirs >= lvl > mine and (ctx.keep or theirs == lvl):
            safe = M._safe_uv(ctx)
            text = f"Recule vers ta tour : {opp} est niveau {lvl}, pas toi"
            why = (f"{opp} a son ultime et pas toi : ne l'échange pas avant ton niveau 6." if lvl == 6
                   else f"{opp} a un sort de plus que toi : attends ton niveau {lvl} avant d'échanger.")
            voice = "Il a son ultime : recule !" if lvl == 6 else "Il a un niveau d'avance : recule."
            return replace(M._call("gc_level", f"gc_level:opp:{lvl}", f"{opp.upper()[:12]} NIVEAU {lvl}", text, why,
                                   safe, tier=tier, score=value - 0.05, priority=79, color="danger",
                                   life=LEVEL_LIFE_S[lvl], label="TA TOUR",
                                   factors=(f"niveau {mine} contre {theirs}",)), voice=voice)
    return None


def rule_jungler_far(ctx: M.MacroCtx) -> M.GeniusCall | None:
    """Enemy jungler SEEN on the other half of the map (laning): my lane is free for ~20 s."""
    if not ctx.me_alive or ctx.in_base or ctx.role not in ("TOP", "BOTTOM", "UTILITY") or ctx.gt >= LANING_END_GT \
            or ctx.gt < 150.0:
        return None
    lane = M._my_lane(ctx)
    if not _in_my_lane(ctx) or lane not in ("top", "bot"):
        return None
    if ctx.hp is not None and ctx.hp < 0.5:
        return None
    if not _stance_ok(ctx) and not ctx.keep:
        return None
    jl = M.jungler_location(ctx)
    if jl.dead or jl.source != "seen" or jl.uv is None:
        return None
    if jl.age > (25.0 if ctx.keep else JG_SEEN_MAX_AGE_S) or jl.side != _opposite_side(lane):
        return None
    if not any(not bool(getattr(p, "is_dead", False)) for p in _alive_lane_opps(ctx)):
        return None                                      # the opponent is dead: plates (macro) say it
    where = M.SIDE_FR.get(jl.side, "loin")
    name = jl.name or "Leur jungler"
    return replace(M._call("gc_jungler_far", f"gc_jg_far:{jl.side}:{int(ctx.gt // 90)}", "JOUE AGRESSIF",
                           f"Joue agressif : {name} est {where}",
                           f"Il ne peut pas venir te ganker avant ~20 s : échange ou prends des sbires d'avance.",
                           None, tier="basic", score=0.62, priority=77, color="safe", life=14.0,
                           factors=(f"jungler vu {where} il y a {jl.age:.0f} s",)),
                   voice=f"Leur jungler est {where} : avance !")


def rule_jungler_unseen(ctx: M.MacroCtx) -> M.GeniusCall | None:
    """Enemy jungler invisible for a while + my wave pushed (I stand far from my tower): back off."""
    if not ctx.me_alive or ctx.in_base or ctx.role in (None, "JUNGLE") or not (165.0 <= ctx.gt < LANING_END_GT):
        return None
    if not _in_my_lane(ctx):
        return None
    jl = M.jungler_location(ctx)
    if jl.dead or not ctx.jungler_alias or (jl.conf >= (0.4 if ctx.keep else 0.25) and jl.side != M._my_lane(ctx)):
        return None                                      # dead / known elsewhere: no "recule"
    hid = _jg_hidden_s(ctx)
    if hid is None or hid < (JG_UNSEEN_MIN_S * (0.6 if ctx.keep else 1.0)):
        return None
    if not ctx.keep and (ctx.gold >= M.RECALL_GOLD or (ctx.hp is not None and ctx.hp < M.LOW_HP)):
        return None                                      # a crashed wave + gold / low HP: the recall call is better
    meet, state = _wave(ctx)
    pushed = (meet is not None and meet >= (0.55 if ctx.keep else 0.6) and state == "pushing") or M._forward(ctx)
    if not pushed:
        return None
    name = jl.name or "leur jungler"
    return replace(M._call("gc_jungler_unseen", f"gc_jg_unseen:{int(ctx.gt // 120)}", "JUNGLER INVISIBLE",
                           f"Recule vers ta tour : vague poussée, {name} invisible",
                           f"Ta vague est près de leur tour, toi loin de la tienne : invisible depuis "
                           f"{int(min(hid, 99))} s, il peut être dans ta rivière.",
                           M._safe_uv(ctx), tier="basic", score=0.5, priority=73, color="danger", life=12.0,
                           label="TA TOUR", factors=(f"jungler invisible {int(min(hid, 99))} s", "vague poussée")),
                   voice="Jungler invisible : recule !")


def rule_baron_setup(ctx: M.MacroCtx) -> M.GeniusCall | None:
    """>= 20:00, Baron up, a clear team gold lead and no respawn window: vision first, then Baron."""
    if not ctx.me_alive or ctx.in_base or ctx.gt < 1200.0 or ctx.phase == "laning":
        return None
    if not M._obj_up(ctx, "baron", 30.0):
        return None
    if ctx.gold_diff < (BARON_SETUP_GOLD * (0.8 if ctx.keep else 1.0)):
        return None
    if len(M._dead_list(ctx, "allies", 5.0)) >= 2 or len(M._dead_list(ctx, "enemies", 8.0)) >= 2:
        return None                                      # numbers decide: fight_won / fight_lost say it
    if M.pit_crowd(ctx, M.BARON_UV) >= 2:
        return None                                      # they already sit on it: no face-check set-up
    gd = f"{ctx.gold_diff / 1000:.1f}k".replace(".", ",")
    return replace(M._call("gc_baron_setup", f"gc_baron_setup:{int(ctx.gt // 180)}", "BARON : VISION",
                           f"Pose des balises au Baron puis prends-le : +{gd} d'or",
                           "Vous avez plus d'or : la vision d'abord, puis le Baron à 5 (tour suivante gratuite).",
                           M.BARON_UV, tier="mid", score=0.66, priority=81, color="safe", life=25.0,
                           label="BARON", factors=(f"or équipe +{int(ctx.gold_diff)}",)),
                   voice="Vous êtes devant : balises au Baron.")


def _resist_owned(items: Any, tag: str) -> bool:
    try:
        from treeaicoach.itemization import load_items

        table = load_items()
        return any(int(i) in table and tag in table[int(i)].tags for i in items or ())
    except Exception:
        return False


#: (component id when gold >= its price, cheaper fallback) per need
DEFENSE_ITEMS = {"Armor": ((1031, 800), (1029, 300)), "SpellBlock": ((1057, 850), (1033, 400))}


def rule_fed_defense(ctx: M.MacroCtx) -> M.GeniusCall | None:
    """In the shop with an enemy fed (4+ kills, +3) and no armour / magic resist: buy the counter now."""
    if not ctx.me_alive or not ctx.in_base or ctx.gt < 300.0 or ctx.gold < 300 or ctx.me_uv is None:
        return None
    if not geometry.in_fountain(ctx.me_uv[0], ctx.me_uv[1], ctx.my_team):
        return None                                      # walking out of the base: the shop is behind
    me = _my_player(ctx)
    best = None
    for p in getattr(ctx.game, "enemies", None) or []:
        sc = getattr(p, "scores", None) or {}
        k, d = int(_f(sc.get("kills"), 0) or 0), int(_f(sc.get("deaths"), 0) or 0)
        if k < FED_MIN_KILLS or k - d < 3:
            continue
        if best is None or k - d > best[1]:
            best = (p, k - d, k, d)
    if best is None:
        return None
    p, _lead, k, d = best
    try:
        from treeaicoach.itemization import damage_split, load_items

        ad, ap, _tr = damage_split(str(getattr(p, "champion_alias", "") or ""))
    except Exception:
        return None
    need = "Armor" if ad >= ap else "SpellBlock"
    if _resist_owned(getattr(me, "items", None), need):
        return None
    table = load_items()
    pick = None
    for iid, price in DEFENSE_ITEMS[need]:
        if ctx.gold >= price and iid in table:
            pick = table[iid].name
            break
    if pick is None:
        return None
    name = _name(ctx, p)
    kind = "physiques" if need == "Armor" else "magiques"
    return M._call("gc_fed_defense", f"gc_fed:{str(getattr(p, 'champion_alias', '')).lower()}:{need}:{int(ctx.gt // 300)}",
                   "ACHAT DÉFENSIF", f"Achète {pick} : {name} est trop fort ({k}/{d})",
                   f"{name} fait des dégâts {kind} : cet achat te fait survivre à son combo.", None,
                   tier="mid", score=0.6, priority=70, color="gold", life=20.0, factors=(f"{name} {k}/{d}",))


def rule_facecheck(ctx: M.MacroCtx) -> M.GeniusCall | None:
    """Late game, alone in the river / jungle with 3+ enemies (their jungler among them) unseen."""
    if not ctx.me_alive or ctx.in_base or ctx.gt < 1200.0 or ctx.me_uv is None:
        return None
    z = geometry.classify_zone(*ctx.me_uv)
    if geometry.is_base(z) or geometry.lane_of(z) is not None:
        return None
    if any(M._uv(getattr(a, "uv", None)) is not None and geometry.dist(M._uv(a.uv), ctx.me_uv) < 0.15  # type: ignore[arg-type]
           for a in ctx.allies):
        return None                                      # with my team: the group decides
    fresh = {str(getattr(e, "alias", "") or "").lower() for e in M._fresh_enemies(ctx, 8.0)}
    alive = [str(getattr(p, "champion_alias", "") or "").lower() for p in (getattr(ctx.game, "enemies", None) or [])
             if not bool(getattr(p, "is_dead", False))]
    unseen = [a for a in alive if a not in fresh]
    if len(unseen) < 3 or (ctx.jungler_alias and ctx.jungler_alias not in unseen):
        return None
    return replace(M._call("gc_facecheck", f"gc_facecheck:{int(ctx.gt // 120)}", "PAS DE BUISSON",
                           f"Ne va pas dans les buissons : {len(unseen)} ennemis invisibles",
                           "Sans balise, un buisson peut cacher 3 ennemis : passe derrière une balise ou avec ton équipe.",
                           M._safe_uv(ctx), tier="basic", score=0.5, priority=69, color="danger", life=10.0,
                           label="TON ÉQUIPE", factors=(f"{len(unseen)} invisibles",)),
                   voice="Pas de buisson sans balise !")


RULES = (rule_level_race, rule_jungler_far, rule_jungler_unseen, rule_baron_setup, rule_fed_defense,
         rule_facecheck)


# ----------------------------------------------------------------------------- voice
#: objective word of a call title (banner) -> spoken word
_OBJ_WORD = {"BARON": "Baron", "DRAGON": "dragon", "ANCESTRAL": "ancestral", "HÉRAUT": "Héraut",
             "LARVES": "larves", "INHIBITEUR": "inhibiteur", "TOUR": "tour"}
_NUM_FR = {2: "deux", 3: "trois", 4: "quatre", 5: "cinq"}


def _obj_word(call: Any) -> str | None:
    title = str(getattr(call, "title", "") or "").upper().rstrip(" !")
    return _OBJ_WORD.get(title)


def voice_class(kind: str) -> str | None:
    """"big" / "lane" voice class of a call kind, None when the kind is never spoken."""
    if kind in ("fight_won", "jungler_dead", "gc_baron_setup"):
        return "big"
    if kind in ("plates", "cross_trade", "free_dragon", "wave_recall", "gc_level", "gc_jungler_far",
                "gc_jungler_unseen", "gc_facecheck"):
        return "lane"
    return None


def voice_for(call: Any, ctx: Any = None, level: Any = "intermediaire") -> tuple[str, str] | None:
    """``(key, spoken line)`` for a NEW planner call when it deserves the voice at this skill level
    (see the module docstring), else None. Never raises."""
    try:
        kind = str(getattr(call, "kind", "") or "")
        cls = voice_class(kind)
        if cls is None or M.level_key(level) not in VOICE_LEVELS[cls]:
            return None
        text = str(getattr(call, "voice", "") or "")
        dead = len(M._dead_list(ctx, "enemies", 5.0)) if ctx is not None and getattr(ctx, "game", None) is not None else 0
        obj = _obj_word(call)
        if kind == "fight_won" or (kind == "jungler_dead" and dead >= 2):
            if obj is None:
                return None
            n = max(2, min(5, dead or 2))
            text = f"Ils sont {_NUM_FR[n]} morts : {obj} !"
        elif kind == "jungler_dead":
            if obj is None:
                if str(getattr(call, "title", "")).startswith("ENVAHIS"):
                    text = "Leur jungler est mort : envahis !"
                else:
                    return None                              # "pousse ta vague": written only
            else:
                text = f"Leur jungler est mort : {obj} !"
        elif kind == "plates":
            line = str(getattr(call, "text", "") or "")
            gone = "Il est rentré" if "en base" in line else "Il est mort"
            text = f"{gone} : prends la plaque !" if str(getattr(call, "title", "")).startswith("PLAQUE") \
                else f"{gone} : frappe la tour !"
        elif kind in ("cross_trade", "free_dragon"):
            if obj in ("Héraut", "larves", "dragon", "ancestral"):
                side = "en haut" if kind == "free_dragon" else "en bas"
                text = f"Jungler {side} : {obj} !" if obj != "larves" else f"Jungler {side} : les larves !"
            elif obj == "tour":
                text = "Ils sont loin : frappe la tour !"
            else:
                return None
        elif kind == "wave_recall":
            gold = float(getattr(ctx, "gold", 0.0) or 0.0) if ctx is not None else 0.0
            hp = getattr(ctx, "hp", None) if ctx is not None else None
            # only the critical recalls: a big purse to spend, or low HP, with the wave crashed
            if "maintenant" not in str(getattr(call, "text", "")) or not (gold >= 1300 or (hp is not None and hp < 0.35)):
                return None
            text = "Ta vague est poussée : rentre !"
        if not text or len(text) > VOICE_MAX_CHARS:
            return None
        return f"gc:{cls}:{kind}:{getattr(call, 'ident', '')}", text
    except Exception:
        log.debug("game changer voice failed", exc_info=True)
        return None


def topic_of(kind: str) -> str:
    """Voice topic of a call kind (one topic is never spoken twice within :data:`VOICE_TOPIC_S`)."""
    return {"fight_won": "objective_now", "jungler_dead": "objective_now", "gc_baron_setup": "objective_now",
            "plates": "opp_gone", "cross_trade": "jungler_side", "free_dragon": "jungler_side",
            "gc_jungler_far": "jungler_side", "gc_jungler_unseen": "jungler_unseen",
            "wave_recall": "recall", "gc_level": "level", "gc_facecheck": "facecheck"}.get(kind, kind)


def voice_phrases() -> list[str]:
    """Every line :func:`voice_for` can say (static: pre-generated at game start)."""
    out: list[str] = []
    for n in range(2, 6):
        for obj in _OBJ_WORD.values():
            out.append(f"Ils sont {_NUM_FR[n]} morts : {obj} !")
    for obj in ("Baron", "dragon", "ancestral", "Héraut", "larves"):
        out.append(f"Leur jungler est mort : {obj} !")
    out += ["Leur jungler est mort : envahis !",
            "Il est mort : prends la plaque !", "Il est rentré : prends la plaque !",
            "Il est mort : frappe la tour !", "Il est rentré : frappe la tour !",
            "Jungler en bas : Héraut !", "Jungler en bas : les larves !", "Jungler en haut : dragon !",
            "Jungler en haut : ancestral !", "Ils sont loin : frappe la tour !",
            "Ta vague est poussée : rentre !",
            "Niveau d'avance : frappe-le !", "Ton ultime est prêt : frappe-le !",
            "Il a un niveau d'avance : recule.", "Il a son ultime : recule !",
            "Leur jungler est en bas : avance !", "Leur jungler est en haut : avance !",
            "Jungler invisible : recule !", "Vous êtes devant : balises au Baron.",
            "Pas de buisson sans balise !"]
    return [x for x in dict.fromkeys(out) if len(x) <= VOICE_MAX_CHARS]


def level_spike_text(alias: Any, opp: Any) -> str | None:
    """The one-shot level-6 card line (coach.MapCoach ``level6``): my ultimate, concrete, or None."""
    u = ult_line(alias, opp)
    return u[0] if u else None


__all__ = ["RULES", "ULT", "ult_line", "voice_for", "voice_class", "voice_phrases", "topic_of",
           "VOICE_MAX_CHARS", "VOICE_TOPIC_S", "VOICE_LEVELS", "level_spike_text"]
