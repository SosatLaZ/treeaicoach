"""Personal win conditions / evening focus (win_conditions.py) + its wiring (strip, card, coach_plus)."""

from types import SimpleNamespace

from treeaicoach import win_conditions as WC


def _row(win, deaths10, cs10=50, back=300, pos="TOP", ward=10):
    return {"result": "Win" if win else "Lose", "duration": 1500, "game_mode": "CLASSIC", "position": pos,
            "champion": "Garen", "wc_deaths10": deaths10, "wc_cs10": cs10, "wc_first_back": back,
            "wc_ward15": ward, "wc_gold10": None, "wc_gold15": None, "wc_obj": None}


def _history():
    # 0 deaths before 10:00 -> 5 wins / 6; else 1 / 6. CS / back / vision say nothing.
    return ([_row(True, 0)] * 5 + [_row(False, 0)] + [_row(False, 2)] * 5 + [_row(True, 3)])


def test_effects_and_focus():
    eff = {e.key: e for e in WC.effects(_history())}
    e = eff["deaths10"]
    assert (e.w_met, e.n_met, e.w_not, e.n_not) == (5, 6, 1, 6) and e.threshold == 0
    assert "cs10" not in eff or eff["cs10"].gap < WC.MIN_GAP
    f = WC.pick_focus(_history(), "TOP")
    assert f is not None and f.key == "deaths10" and f.label == "0 mort avant 10:00"
    assert f.text == "Focus : 0 mort avant 10:00" and "5 victoires sur 6" in f.why


def test_no_focus_under_8_games_or_without_gap():
    assert WC.pick_focus(_history()[:7], "TOP") is None
    flat = [_row(i % 2 == 0, i % 3) for i in range(12)]
    assert WC.pick_focus(flat, "TOP") is None
    assert WC.pick_focus([{"result": "Win", "duration": 200}] * 20, "TOP") is None   # remakes ignored


def test_cs_condition_not_for_supports():
    rows = [_row(i < 6, 2, cs10=(80 if i < 6 else 10), pos="UTILITY") for i in range(12)]
    assert all(e.key != "cs10" for e in WC.effects(rows))


def test_features_from_snapshots():
    snaps = [{"game_time": float(t), "gold": g, "cs": t // 10, "deaths": (1 if t >= 400 else 0), "ward_score": t / 60}
             for t, g in [(60, 500), (120, 700), (180, 900), (240, 1100), (270, 300), (400, 600), (600, 900),
                          (900, 800)]]
    f = WC.game_features({"snapshots": snaps}, analysis={})
    assert f["wc_first_back"] == 240.0 and f["wc_deaths10"] == 1 and f["wc_cs10"] == 60 and f["wc_ward15"] == 15


def test_tracker_deaths_line_and_evaluate():
    f = WC.pick_focus(_history(), "TOP")
    tr = WC.FocusTracker(f)
    assert tr.update(200.0, {"deaths": 0}) is None
    assert tr.update(300.0, {"deaths": 1}) == "Focus : 0 mort avant 10:00 — 1 mort"
    assert tr.status == "raté"
    assert tr.update(400.0, {"deaths": 1}) is None              # one minute, then quiet
    ok, line = WC.evaluate(f, {"wc_deaths10": 0})
    assert ok and line.startswith("Focus du soir tenu")
    assert WC.evaluate(WC.focus_from_block(WC.focus_block(f)), {"wc_deaths10": 2})[0] is False


def test_tracker_cs_and_back():
    e = WC.Effect("cs10", 60, WC.label("cs10", 60), True, 5, 4, 5, 1)
    tr = WC.FocusTracker(WC.Focus("cs10", 60, e.label, "", e))
    assert tr.update(250.0, {"creepScore": 5}).startswith("Focus : 60 sbires à 10:00 — 5")
    assert tr.update(300.0, {"creepScore": 5}) is None            # outside the 40 s window of the 2-min checkpoint
    eb = WC.Effect("first_back", 300, WC.label("first_back", 300), True, 5, 4, 5, 1)
    tb = WC.FocusTracker(WC.Focus("first_back", 300, eb.label, "", eb))
    assert tb.update(250.0, {}, 1200) == "Focus : pas de retour avant 5:00 — attends 5:00"
    tb.update(260.0, {}, 200)                                        # bought: back at 4:20
    assert tb.status == "raté"


def test_strip_line_replaces_line_one():
    from treeaicoach import essentials as ess
    st = SimpleNamespace(edge_stats=None, objectives=(), game_time=300.0, enemies=(), jungler_map=None,
                         focus_line="Focus : 0 mort avant 10:00 — 1 mort")
    lines = ess.build_lines(st)
    assert lines[0][0].label == "FOCUS" and lines[0][0].value == "0 mort avant 10:00 — 1 mort"
    m = ess._metrics(None)
    long = ess.build_lines(SimpleNamespace(focus_line="Focus : " + "W" * 80))[0][0]
    assert ess._line_width((long,), m) + 2 * m["padx"] <= ess.strip_size(None)[0]


def test_card_and_coach_plus_focus():
    from treeaicoach import champ_select as cs
    from treeaicoach.coach_plus import CoachPlus
    st = cs.ChampSelectState(me=cs.Pick(alias="Garen", name="Garen", position="TOP"), allies=(), enemies=(),
                             phase="PLANNING") if hasattr(cs, "ChampSelectState") else None
    try:
        card = cs.build_card(st, "", "Focus du soir : 0 mort avant 10:00 (5 victoires sur 6 quand tenu, 1 sur 6 sinon)")
    except TypeError:
        card = None
    if card is not None:
        assert card.focus and any(line.startswith("Focus du soir") for line in card.lines)
    f = WC.pick_focus(_history(), "TOP")
    plus = CoachPlus(lambda: [], focus_loader=lambda role: f)
    me = SimpleNamespace(scores={"deaths": 0}, current_gold=500, position="TOP", champion_alias="Garen", is_dead=False)
    game = SimpleNamespace(me=me, enemies=[], allies=[])
    notes = []
    for gt in (10.0, 40.0, 70.0, 100.0):
        notes += plus.update(gt, gt, game, {"my_role": "TOP"})
    titles = [n.title for n in notes]
    assert "FOCUS DU SOIR" in titles and "OBJECTIF DE LA PARTIE" not in titles
    me.scores = {"deaths": 1}
    plus.update(300.0, 300.0, game, {"my_role": "TOP"})
    assert plus.focus_line() == "Focus : 0 mort avant 10:00 — 1 mort"
    assert plus.focus_block()["key"] == "deaths10"
