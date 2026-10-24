"""live_edge.py: gold-exact live numbers (lane gold diff, kill window, recall, defense, win reason,
objective with numbers, farm rates) and the anti-spam of LiveEdge."""
from __future__ import annotations

from types import SimpleNamespace

from treeaicoach import hype
from treeaicoach import live_edge as LE
from treeaicoach.itemization import load_items
from treeaicoach.live_client import GameInfo, PlayerInfo


def P(alias, team, pos="", level=1, items=(), dead=False, respawn=0.0, k=0, d=0, cs=0):
    return PlayerInfo(riot_id=f"{alias}#X", summoner_name=alias, champion_alias=alias, champion_name=alias,
                      team=team, position=pos, level=level, items=list(items), is_dead=dead, respawn_timer=respawn,
                      scores={"kills": k, "deaths": d, "assists": 0, "creepScore": cs, "wardScore": 0.0})


def G(gt=600.0, me=None, allies=(), enemies=(), gold=0.0, hp=(500.0, 600.0)):
    me = me or P("Garen", "ORDER", "TOP", level=6, items=[3044], cs=int(gt / 60 * 7))
    return GameInfo(game_time=gt, me=me, allies=list(allies), enemies=list(enemies), current_gold=gold,
                    map_number=11, champion_stats={"currentHealth": hp[0], "maxHealth": hp[1]})


def fresh():
    """A LiveEdge that already watched the game (no call in the first WATCH_S seconds)."""
    e = LE.LiveEdge()
    e._first_t = -1000.0
    return e


def Obj(key, alive=True):
    return SimpleNamespace(key=key, alive=alive, remaining=0.0)


# ------------------------------------------------------------------ 1. lane gold diff
def test_lane_gold_diff_exact_item_prices():
    me = P("Garen", "ORDER", "TOP", items=[3044, 1001])            # Phage 1100 + Bottes 300
    opp = P("Darius", "CHAOS", "TOP", items=[1036, 1055])           # Épée longue 350 + Doran 450
    assert LE.lane_gold_diff(me, opp) == 1400 - 800
    assert LE.gold_diff_text(600, "Darius") == "+600 or sur Darius"
    assert LE.gold_diff_text(-1250, "Darius") == "-1 250 or sur Darius"
    g = G(enemies=[opp, P("Lux", "CHAOS", "MIDDLE")], me=me)
    assert LE.lane_opponent(g) is opp
    sb = SimpleNamespace(my_matchup=SimpleNamespace(enemy_alias="Lux"))
    assert LE.lane_opponent(g, sb).champion_alias == "Lux"          # resolved roles (lane swap) win


# ------------------------------------------------------------------ 2. kill window
def test_kill_window_verdict():
    assert LE.kill_window(6, 5, 600, 0.9)[0]                      # level + ultimate + gold
    assert not LE.kill_window(6, 5, 600, 0.3)[0]                  # my HP too low
    assert not LE.kill_window(5, 6, 1500, 0.9)[0]                 # behind in levels: never
    assert LE.kill_window(7, 7, 1000, 1.0)[0]                     # a big item lead alone
    assert not LE.kill_window(7, 7, 300, 1.0)[0]
    t = LE.kill_window_text("Darius", 6, 5, 600)
    assert t == "Attaque Darius : fenêtre de kill (niv 6 vs 5, +600 or)" and len(t) <= 60


def test_kill_window_called_once_when_it_opens():
    opp = P("Darius", "CHAOS", "TOP", level=5, items=[1055])
    me = P("Garen", "ORDER", "TOP", level=6, items=[3044], cs=80)
    edge = fresh()
    notes = edge.update(100.0, G(600.0, me=me, enemies=[opp]), role="TOP")
    assert [n.feature for n in notes] == ["kill"] and notes[0].text.startswith("Attaque Darius")
    assert notes[0].voice == ""                                     # written only
    assert edge.update(130.0, G(630.0, me=me, enemies=[opp]), role="TOP") == []   # still open: not repeated
    assert edge.update(140.0, G(640.0, me=me, enemies=[opp]), role="TOP", busy=True) == []


# ------------------------------------------------------------------ 3. optimal recall
def test_recall_target_exact_component():
    table = load_items()
    rt = LE.recall_target(3071, [], 1300, table)                  # Couperet noir: Phage 1100 fits
    assert rt is not None and "Phage" in rt[0] and rt[1] >= 1100
    assert LE.recall_target(3071, [], 400, table) is None          # not worth a trip
    assert LE.recall_target(None, [], 5000, table) is None
    assert LE.recall_text("Phage", 1312.7) == "Rentre acheter Phage : tu as 1312 or"


def test_recall_note_uses_meta_build(monkeypatch):
    from treeaicoach import itemization

    build = SimpleNamespace(core=(3071, 3078), boots=None, options=(), has_items=True)
    monkeypatch.setattr(itemization, "meta_build", lambda alias, role, *_mode: build)
    me = P("Garen", "ORDER", "TOP", level=6, items=[1055], cs=90)
    edge = fresh()
    notes = edge.update(10.0, G(700.0, me=me, gold=1300.0), role="TOP")
    assert notes and notes[0].feature == "recall" and notes[0].key.startswith("comp_ready:")
    assert "1300 or" in notes[0].text
    assert fresh().update(10.0, G(700.0, me=me, gold=1300.0), role="TOP", recall_ok=False) == []


# ------------------------------------------------------------------ 4. adaptive defense
def test_defense_against_ap_team():
    enemies = [P(a, "CHAOS") for a in ("Lux", "Syndra", "Ahri", "Vladimir", "Zed")]
    phys, magic, _ = LE.damage_profile(enemies)
    assert magic > 0.7 and abs(phys + magic + _ - 1.0) < 1e-6
    call = LE.defense_call(enemies, "Garen", "TOP", [])
    assert call is not None and call[0].startswith("Achète Sandales de Mercure") and "% AP" in call[0]
    assert len(call[0]) <= 60
    assert LE.defense_call(enemies, "Garen", "TOP", [3111, 2504]) is None or "AP" not in LE.defense_call(
        enemies, "Garen", "TOP", [3111, 2504])[0]


def test_defense_only_in_shop_or_dead():
    enemies = [P(a, "CHAOS") for a in ("Lux", "Syndra", "Ahri", "Vladimir", "Zed")]
    me = P("Garen", "ORDER", "TOP", level=9, items=[3044])
    assert all(n.feature != "defense" for n in fresh().update(1.0, G(900.0, me=me, enemies=enemies)))
    notes = fresh().update(1.0, G(900.0, me=me, enemies=enemies), in_base=True)
    assert notes and notes[0].feature == "defense"


# ------------------------------------------------------------------ 5. win probability + reason
def test_win_reason_names_the_swing():
    a = hype.WinFactors(game_time=1500.0, gold_diff=0)
    b = hype.WinFactors(game_time=1530.0, gold_diff=300, baron_diff=1, baron_buff=1)
    assert LE.swing_reason(a, b) == "Baron"
    assert LE.win_text(0.62, 8.0, "Baron") == "victoire 62 % (+8 : Baron)"
    assert LE.win_text(0.62, 1.0, "Baron") == "victoire 62 %"
    assert abs(sum(LE.win_terms(b).values()) - hype.win_logit(b)) < 1e-9     # transparent split


def test_level_term_in_win_model():
    up = hype.win_probability(hype.WinFactors(game_time=900.0, gold_diff=0, level_diff=1.0))
    assert 0.52 < up < 0.53              # the rest of a level lead is in the gold estimate (GOLD_LEVEL)
    gold = hype.gold_lead_estimate(0, 0, 0, 1.0)
    assert 2000 < gold < 2700


# ------------------------------------------------------------------ 6. objective with numbers
def test_number_advantage_baron():
    enemies = [P("Darius", "CHAOS", dead=True, respawn=31.0), P("Lux", "CHAOS", dead=True, respawn=26.0),
               P("Zed", "CHAOS", dead=True, respawn=40.0), P("Jinx", "CHAOS"), P("Leona", "CHAOS")]
    g = G(1500.0, enemies=enemies)
    out = LE.number_advantage(g, [Obj("baron"), Obj("dragon")], "TOP")
    assert out is not None and out[0] == "Prends le Baron maintenant : 3 ennemis morts (26 s)"
    assert out[1] == "Ils sont trois morts : Baron !"
    from treeaicoach.game_changers import voice_phrases
    assert out[1] in voice_phrases()                               # pre-generated voice line
    assert LE.number_advantage(g, [Obj("baron", alive=False)], "TOP") is None
    short = [P("Darius", "CHAOS", dead=True, respawn=10.0)] * 3
    assert LE.number_advantage(G(1500.0, enemies=short), [Obj("baron")], "TOP") is None   # window too short
    allies_dead = [P("A", "ORDER", dead=True, respawn=20.0), P("B", "ORDER", dead=True, respawn=20.0)]
    assert LE.number_advantage(G(1500.0, allies=allies_dead, enemies=enemies), [Obj("baron")], "TOP") is None


def test_objective_note_has_voice_and_is_not_repeated():
    enemies = [P("Darius", "CHAOS", dead=True, respawn=30.0), P("Lux", "CHAOS", dead=True, respawn=30.0),
               P("Zed", "CHAOS", dead=True, respawn=30.0)]
    edge = fresh()
    n = edge.update(5.0, G(1500.0, enemies=enemies), objectives=[Obj("baron")], role="MIDDLE")
    assert n and n[0].feature == "objective" and n[0].voice
    assert edge.update(40.0, G(1501.0, enemies=enemies), objectives=[Obj("baron")], role="MIDDLE") == []


# ------------------------------------------------------------------ 7. farm rates
def test_farm_rates_targets_and_nudge():
    me = P("Garen", "ORDER", "TOP", items=[3044], cs=60)
    cs, gpm = LE.farm_rates(G(600.0, me=me, gold=400.0))
    assert cs == 6.0 and gpm == round((1100 + 400 - 500) / 10.0)
    assert LE.farm_targets("TOP", "low")[0] == 5.5 and LE.farm_targets("UTILITY", "")[0] is None
    assert LE.farm_nudge(6.0, 7.5) is None
    assert LE.farm_nudge(3.8, 6.5) == "Farme mieux ta vague : 3,8 CS/min, objectif 6,5"
    assert LE.farm_nudge(4.1, 6.5) is None
    assert LE.farm_text(6.8, 7.0) == "6,8 CS/min (obj 7)"


def test_live_line_and_global_gap():
    opp = P("Darius", "CHAOS", "TOP", level=9, items=[1055])
    me = P("Garen", "ORDER", "TOP", level=9, items=[3044], cs=40)          # 4 CS/min at 10:00: far below
    edge = fresh()
    notes = edge.update(0.0, G(600.0, me=me, enemies=[opp]), role="TOP")
    line = edge.line()
    assert line.startswith("+650 or sur Darius · 4 CS/min (obj 7,5) · victoire")
    assert notes and notes[0].feature == "farm" and notes[0].text.startswith("Farme")
    assert edge.update(5.0, G(605.0, me=me, enemies=[opp]), role="TOP") == []         # global gap
    assert fresh().update(0.0, GameInfo()) == []                                 # spectating: nothing


def test_never_raises_on_garbage():
    edge = fresh()
    assert edge.update(0.0, SimpleNamespace(me=object(), game_time="x")) == []
    assert LE.damage_profile([object()]) == (0.0, 0.0, 0.0)
    assert LE.defense_call([], "", None, [None]) is None or True


def test_no_call_right_after_app_start():
    me = P("Garen", "ORDER", "TOP", level=9, items=[3044], cs=10)
    edge = LE.LiveEdge()
    assert edge.update(0.0, G(600.0, me=me)) == [] and edge.line()
    assert edge.update(LE.WATCH_S + 1, G(631.0, me=me))[0].feature == "farm"


def test_calls_only_in_normal_rift_modes():
    me = P("Garen", "ORDER", "TOP", level=9, items=[3044], cs=40)
    opp = P("Darius", "CHAOS", "TOP", level=9, items=[1055])
    for mode, calls, win in (("CLASSIC", True, True), ("SWIFTPLAY", True, True), ("URF", False, True),
                             ("PRACTICETOOL", False, False)):
        g = G(600.0, me=me, enemies=[opp])
        g.game_mode = mode
        edge = fresh()
        notes = edge.update(0.0, g, role="TOP")
        assert bool(notes) == calls, mode
        assert ("victoire" in (edge.line() or "")) == win, mode
    edge = fresh()                                              # practice tool alone: no win %
    edge.update(0.0, G(600.0, me=me), role="TOP")
    assert "victoire" not in (edge.line() or "")


def test_kill_window_stricter_against_a_favoured_opponent():
    # the draft / rank prior (draft_edge.lane_prior) raises the score asked
    assert LE.kill_window(7, 7, 1000, 1.0)[0]
    assert not LE.kill_window(7, 7, 1000, 1.0, extra=0.675)[0]      # Garen vs Darius levels 1-5
    assert LE.kill_window(8, 7, 1000, 1.0, extra=0.675)[0]          # a level more: open again


def test_kill_window_uses_the_live_prior(monkeypatch):
    from treeaicoach import draft_edge

    opp = P("Darius", "CHAOS", "TOP", level=7, items=[1055])
    me = P("Garen", "ORDER", "TOP", level=7, items=[3044, 1036, 1036], cs=80)
    edge = fresh()
    monkeypatch.setattr(draft_edge, "live_prior", lambda a, b: draft_edge.LanePrior(score=-1.0, kill_extra=1.0))
    assert edge.update(100.0, G(600.0, me=me, enemies=[opp]), role="TOP") == []
    monkeypatch.setattr(draft_edge, "live_prior", lambda a, b: draft_edge.LanePrior())
    notes = fresh().update(100.0, G(600.0, me=me, enemies=[opp]), role="TOP")
    assert [n.feature for n in notes] == ["kill"]
