"""comfort.py: noise budget, danger flash pulse, beep gate, voice ducking, analysis slow-down."""

from types import SimpleNamespace as NS

import numpy as np

from treeaicoach import comfort
from treeaicoach.alerts import AlertKind, Level


def A(kind, level=0, text="x", key=None, alias=None):
    return NS(kind=kind, level=level, text=text, key=key or kind.value, alias=alias)


def test_quiet_moments_only_danger():
    nb = comfort.NoiseBudget("debutant")
    for q in (comfort.Quiet(dead=True), comfort.Quiet(shop_open=True), comfort.Quiet(in_base=True),
              comfort.Quiet(fight=True), comfort.Quiet(loading=True)):
        assert not nb.allow(A(AlertKind.MACRO_TIP, text=f"tip {q}"), 100.0, "text", q)
        assert not nb.allow(A(AlertKind.PERSONAL_DANGER, 1, text=f"warn {q}"), 100.0, "text", q)
    assert nb.allow(A(AlertKind.ROAM_APPROACH, 2, "Gank !"), 100.0, "voice", comfort.Quiet(dead=True))
    assert nb.allow(A(AlertKind.DEATH_RECAP, text="leçon"), 100.0, "text", comfort.Quiet(dead=True))


def test_caps_repeat_praise_scoreboard():
    nb = comfort.NoiseBudget("debutant")
    cap = comfort.LEVEL_CAPS["debutant"][0]
    ok = [nb.allow(A(AlertKind.MACRO_TIP, text=f"tip {i}"), 10.0 + i, "text") for i in range(cap + 2)]
    assert ok == [True] * cap + [False, False]
    assert nb.allow(A(AlertKind.MACRO_TIP, text="tip 99"), 80.0, "text")          # a new minute
    assert not nb.allow(A(AlertKind.MACRO_TIP, text="tip 99"), 100.0, "voice")    # same text < 60 s
    assert nb.allow(A(AlertKind.PRAISE, text="bravo"), 200.0, "text")
    assert not nb.allow(A(AlertKind.PRAISE, text="super"), 400.0, "text")         # < 5 min
    assert nb.allow(A(AlertKind.PRAISE, text="super"), 501.0, "text")
    assert nb.allow(A(AlertKind.SCOREBOARD, text="a"), 600.0, "text")
    assert not nb.allow(A(AlertKind.SCOREBOARD, text="b"), 700.0, "text")
    # danger: never capped, the same text not within 20 s
    g = A(AlertKind.ROAM_APPROACH, 2, "Gank ! recule")
    assert nb.allow(g, 1000.0, "voice") and not nb.allow(g, 1010.0, "voice") and nb.allow(g, 1021.0, "voice")


def test_objective_only_when_mine():
    nb = comfort.NoiseBudget("debutant")
    drag = A(AlertKind.OBJECTIVE_SOON, text="Dragon dans 20 s", key="objective_soon:dragon:20")
    herald = A(AlertKind.OBJECTIVE_SOON, text="Héraut dans 20 s", key="objective_soon:herald:20")
    assert nb.allow(drag, 300.0, "text", role="UTILITY")
    assert not nb.allow(herald, 400.0, "text", role="UTILITY")


def test_flash_pulse_short_thin_once_and_suppressed():
    fp = comfort.FlashPulse()
    vals = [fp.intensity(10.0 + k * 0.1, 10.0, "subtle") for k in range(30)]
    assert max(vals) <= comfort.SUBTLE_PEAK + 1e-6 and max(vals) > 0.3
    assert all(v == 0 for v in vals[round(comfort.SUBTLE_PULSE_S / 0.1) + 1:])          # <= 1.2 s
    # danger re-stamped every tick: no new pulse before the re-arm delay
    assert all(fp.intensity(12.0 + k * 0.1, 12.0 + k * 0.1, "subtle") == 0 for k in range(40))
    fp2 = comfort.FlashPulse()
    assert fp2.intensity(5.2, 5.0, "subtle", suppressed=True) == 0
    assert fp2.intensity(5.4, 5.0, "subtle") == 0                    # the shop closed: no late pulse
    full = comfort.FlashPulse()
    assert full.intensity(1.0, 1.0, "full") == 1.0 and full.intensity(3.01, 1.0, "full") == 0      # never > 2 s
    assert comfort.flash_thickness_scale("subtle") < comfort.flash_thickness_scale("full")


def test_beep_gate():
    bg = comfort.BeepGate()
    g = A(AlertKind.ROAM_APPROACH, 2, alias="Ahri")
    assert bg.allow(g, 100.0)
    assert not bg.allow(g, 103.0)                       # 5 s min gap
    assert not bg.allow(g, 110.0)                       # same threat < 20 s
    assert bg.allow(A(AlertKind.ROAM_APPROACH, 2, alias="Trundle"), 110.0)   # a NEW danger
    assert bg.allow(g, 121.0)
    assert not bg.allow(A(AlertKind.MACRO_TIP, 0), 200.0)                      # never for info
    assert not bg.allow(A(AlertKind.ROAM_APPROACH, 2, alias="Zed"), 300.0, comfort.Quiet(shop_open=True))
    assert bg.allow(A(AlertKind.PERSONAL_DANGER, 2, key="personal_danger:siege"), 400.0,
                    comfort.Quiet(dead=True))           # my base attacked: always


def test_ducking_and_slowdown():
    assert comfort.ducked_volume(100, 2) == 100 and comfort.ducked_volume(100, 0) == 70
    assert comfort.ducked_volume(80, 0, ducking=False) == 80
    assert comfort.low_value_reason(dead=True, gt=600) == "dead"
    assert comfort.low_value_reason(in_base=True, shop_open=True, gt=600) == "shop"
    assert comfort.low_value_reason(gt=3.0) == "loading"
    assert comfort.low_value_reason(dead=True, gt=600, threat=1) is None
    assert comfort.low_value_reason(in_base=True, gt=600) is None
    assert comfort.calm_period(0.2, "dead", False) == 0.5 and comfort.calm_period(0.2, None, False) == 0.2
    sm = comfort.SceneMotion()
    f = np.zeros((300, 300, 3), np.uint8)
    assert [sm.update(f) for _ in range(4)] == [False, False, False, True]
    g = f.copy()
    g[100:125, 100:125] = 200                           # an icon walks in
    assert not sm.update(g)


def test_bot_caps():
    eng = NS(_cfg=NS(skill_level="debutant"), _clock=lambda: 0.0)
    assert comfort.bot_allow(eng, "team_call", "Baron !", {"key": "b"}, t=10.0)
    assert not comfort.bot_allow(eng, "team_call", "Baron !", {"key": "b"}, t=20.0)       # repeat
    assert comfort.bot_allow(eng, "gank", "Top gank", None, t=21.0)
    assert comfort.bot_allow(eng, "game_start", "", None, t=21.0)
