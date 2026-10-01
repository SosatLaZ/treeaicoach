"""Game changers (game_changers.py): the ranked library, its voice, the enemy-buys coach, the VALUE judge."""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from treeaicoach import game_changers as gc  # noqa: E402
from treeaicoach import macro, phase  # noqa: E402
from treeaicoach import voice_policy as vp  # noqa: E402
from treeaicoach.alerts import Alert, AlertKind, Level  # noqa: E402
from treeaicoach.live_client import GameInfo, PlayerInfo  # noqa: E402

ROSTER = (("Garen", "ORDER", "TOP"), ("Vi", "ORDER", "JUNGLE"), ("Lux", "ORDER", "MIDDLE"),
          ("Jinx", "ORDER", "BOTTOM"), ("Thresh", "ORDER", "UTILITY"), ("Darius", "CHAOS", "TOP"),
          ("LeeSin", "CHAOS", "JUNGLE"), ("Ahri", "CHAOS", "MIDDLE"), ("Caitlyn", "CHAOS", "BOTTOM"),
          ("Nautilus", "CHAOS", "UTILITY"))
TOP_ME = (0.085, 0.30)


def game(gt=300.0, levels=None, dead=None, items=None, kills=None, gold=500.0, hp=1.0):
    levels, dead, items, kills = dict(levels or {}), dict(dead or {}), dict(items or {}), dict(kills or {})
    ps = {}
    for alias, team, pos in ROSTER:
        k, d = kills.get(alias, (0, 0))
        ps[alias] = PlayerInfo(riot_id=f"{alias}#X", summoner_name=f"{alias}#X", champion_alias=alias,
                               champion_name="Lee Sin" if alias == "LeeSin" else alias, team=team, position=pos,
                               is_dead=alias in dead, respawn_timer=float(dead.get(alias, 0.0)),
                               level=levels.get(alias, 5), items=list(items.get(alias, [])),
                               has_smite=pos == "JUNGLE", scores={"kills": k, "deaths": d})
    return GameInfo(game_time=gt, game_mode="CLASSIC", map_number=11, me=ps["Garen"],
                    allies=[p for a, p in ps.items() if p.team == "ORDER" and a != "Garen"],
                    enemies=[p for p in ps.values() if p.team == "CHAOS"], events=[], current_gold=gold,
                    champion_stats={"currentHealth": 1000.0 * hp, "maxHealth": 1000.0})


def ctx_for(g, me_uv=TOP_ME, **kw):
    st = phase.map_state(g, g.game_time, "TOP")
    return macro.build_ctx(100.0, g.game_time, g, st, role="TOP", me_uv=me_uv, **kw)


def test_voice_lines_short_static_and_pregenerated():
    from treeaicoach.tts_neural import static_phrases

    lines = gc.voice_phrases()
    assert lines and all(len(x) <= gc.VOICE_MAX_CHARS for x in lines)
    assert set(lines) <= set(static_phrases())                 # cached at game start: no latency


def test_level_race_window_both_ways():
    c = ctx_for(game(130.0, levels={"Garen": 2, "Darius": 1}))
    call = gc.rule_level_race(c)
    assert call is not None and call.kind == "gc_level" and call.text.startswith("Frappe Darius maintenant")
    assert call.voice == "Niveau d'avance : frappe-le !"
    back = gc.rule_level_race(ctx_for(game(320.0, levels={"Garen": 5, "Darius": 6})))
    assert back is not None and back.text == "Recule vers ta tour : Darius est niveau 6, pas toi"
    assert gc.rule_level_race(ctx_for(game(320.0, levels={"Garen": 6, "Darius": 6}))) is None


def test_jungler_far_needs_a_fresh_sighting_on_the_other_side():
    lee = SimpleNamespace(alias="LeeSin", uv=(0.78, 0.86), visible=True, hidden_s=0.0)
    c = ctx_for(game(300.0), enemies=[lee])
    call = gc.rule_jungler_far(c)
    assert call is not None and call.text == "Joue agressif : Lee Sin est en bas"
    assert call.voice == "Leur jungler est en bas : avance !"
    old = SimpleNamespace(alias="LeeSin", uv=(0.78, 0.86), visible=False, hidden_s=20.0)
    assert gc.rule_jungler_far(ctx_for(game(300.0), enemies=[old])) is None


def test_jungler_unseen_with_pushed_wave_backs_off_but_not_with_recall_money():
    from treeaicoach.waves import LaneWave

    push = {"top": LaneWave("top", ally=5, enemy=2, meet=0.7, state="pushing")}
    c = ctx_for(game(300.0, gold=400.0), waves=push)
    call = gc.rule_jungler_unseen(c)
    assert call is not None and call.text.startswith("Recule vers ta tour : vague poussée")
    assert gc.rule_jungler_unseen(ctx_for(game(300.0, gold=1400.0), waves=push)) is None   # the recall call wins


def test_fed_defense_only_in_the_shop():
    g = game(700.0, kills={"Darius": (5, 0)}, gold=1000.0)
    shop = ctx_for(g, me_uv=(0.04, 0.96), in_base=True)
    call = gc.rule_fed_defense(shop)
    assert call is not None and call.text == "Achète Cotte de mailles : Darius est trop fort (5/0)"
    assert gc.rule_fed_defense(ctx_for(g, me_uv=(0.06, 0.74), in_base=True)) is None   # walking out


def test_enemy_buys_lane_spike_and_stasis():
    buys = gc.EnemyBuys()
    buys.update(480.0, game(480.0, items={"Darius": [1055, 3044], "Ahri": [1056]}))
    g2 = game(500.0, items={"Darius": [1055, 3071], "Ahri": [1056, 3157]}, kills={"Ahri": (4, 0)})
    assert {(a, i) for _t, a, i in buys.update(500.0, g2)} == {("darius", 3071), ("ahri", 3157)}
    c = ctx_for(g2)
    c.buys = buys
    call = gc.rule_enemy_buys(c)
    assert call is not None and "Darius a fini Couperet noir" in call.text and call.color == "danger"
    buys.events = [e for e in buys.events if e[1] == "ahri"]
    call = gc.rule_enemy_buys(c)
    assert call is not None and call.text == "Fais utiliser son Sablier de Zhonya à Ahri avant ton combo"
    assert "prêt" not in call.text + call.why and "utilisé" not in call.text   # never a cooldown claim


def test_voice_for_big_and_lane_calls_by_level():
    g = game(1500.0, dead={"Ahri": 32.0, "Caitlyn": 30.0, "Nautilus": 28.0})
    c = ctx_for(g, me_uv=(0.42, 0.58), objectives=[SimpleNamespace(key="baron", alive=True, remaining=None)])
    won = next(x for x in macro.evaluate(c) if x.kind == "fight_won")
    for lvl in ("debutant", "intermediaire"):
        key, text = gc.voice_for(won, c, lvl)
        assert key.startswith("gc:big:fight_won") and text == "Ils sont trois morts : Baron !"
    assert gc.voice_for(won, c, "expert") is None
    lane = macro.GeniusCall("plates", "p", "PLAQUES !", "Plaque la tour (20 s) : Darius est mort.", "w")
    assert gc.voice_for(lane, c, "debutant")[1] == "Il est mort : prends la plaque !"
    assert gc.voice_for(lane, c, "intermediaire") is None


def test_voice_policy_gc_keys():
    a = Alert(kind=AlertKind.MACRO_TIP, level=Level.INFO, text="Ils sont trois morts : Baron !", key="gc:big:fight_won:x", t=0.0)
    assert vp.route(a, "minimal") == "voice" and vp.kind_name(a) == "gc"
    gate = vp.VoiceGate()
    assert gate.decide(a, 10.0, vp.SpeechContext(in_fight=True)) == "drop"      # voice-only, never in a fight
    assert gate.decide(a, 10.0, vp.SpeechContext()) == "voice"
    lane = Alert(kind=AlertKind.MACRO_TIP, level=Level.INFO, text="x", key="gc:lane:plates:x", t=0.0)
    assert vp.speech_priority(a) > vp.speech_priority(lane)


def test_ult_line_is_champion_aware():
    assert gc.ult_line("Garen", "Darius")[0] == "Garde ton R pour achever Darius quand il est bas"
    assert gc.ult_line("UnknownChamp", "Darius") is None


def test_tips_shop_and_ward_and_call_overlaps():
    from treeaicoach import tips

    c = tips.TipContext(gt=300.0, role="TOP", in_base=True, gold=900.0, shop_names="Cristal de rubis + Épée longue",
                        item="Couperet noir")
    buy = next(t for t in tips.TIPS if t.id == "buy_item")
    assert buy.applies(c) and buy.render(c) == "Achète Cristal de rubis + Épée longue : tu as l'or"
    rot = tips.TipRotator()
    ctx = tips.TipContext(gt=125.0, role="TOP", lane="top", ward_recent=True)
    assert all(tp.id not in tips.WARD_TIPS for _u, tp in [(0, rot._pick(ctx, 125.0))] if tp is not None)
    ctx2 = tips.TipContext(gt=400.0, role="TOP", recent_calls=frozenset({"gc_level"}), spike_who="opp",
                           spike_what="level", spike_level=6, opp="Darius")
    picked = rot._pick(ctx2, 400.0)
    assert picked is None or picked.id != "spike_opp_big"


# ------------------------------------------------------------------ VALUE judge (tools/ux_replay.py)
def _replay(frames, **kw):
    from tools import ux_replay as ux

    sc = ux.Scenario("synthetic", "synthétique", 0.0, 200.0, warmup=0.0, **kw)
    return ux.Replay(sc, "debutant", frames, 0.0)


def test_value_judge_catches_missed_moments_generic_lines_and_voice_budget():
    from tools import ux_replay as ux

    frames = [ux.Frame(float(t), ("vert", "Reste sur ta vague : 4,5 sbires/min, vise 7")) for t in range(40, 70)]
    m = ux.Moment(45.0, 60.0, "leur jungler est en bas", r"(?i)joue agressif", r"(?i)jungler est en bas")
    rules = {v.rule for v in ux.judge(_replay(frames, moments=[m]))}
    assert {"valeur:moment-manqué", "voix:moment-manqué", "valeur:générique", "valeur:statistique"} <= rules
    talk = [ux.Frame(float(t), None, voice=[(f"Phrase {t}", "macro_tip|gc:lane:x", 0)]) for t in (100, 110, 120)]
    rules = {v.rule for v in ux.judge(_replay(talk))}
    assert "voix:budget" in rules


def test_value_judge_card_banner_agreement_and_stale_countdown():
    from tools import ux_replay as ux

    frames = [ux.Frame(150.0, ("vert", "Va top : Héraut dans 1:00"),
                       banner=("insight", "MILIEU : Rejoins ton équipe au milieu", "MILIEU"))]
    frames += [ux.Frame(float(t), ("vert", "Va bot : Dragon dans 0:59")) for t in range(160, 170)]
    rules = {v.rule for v in ux.judge(_replay(frames))}
    assert "incohérence:carte-bandeau" in rules and "état:objectif-périmé" in rules
