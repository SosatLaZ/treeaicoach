"""Pipeline v2 scheduling (adaptive rate, staggered coaching slots, render-time prediction) and
performance budget / process resources."""

from __future__ import annotations

import sys
import threading
import time
from types import SimpleNamespace as NS

import pytest

from treeaicoach import scheduler as sch
from treeaicoach import sysperf
from treeaicoach.tracker import Track


# ------------------------------------------------------------------ rate governor
def test_rate_governor_calm_burst_unfocused_paused():
    g = sch.RateGovernor(6.0, 12.0)
    assert g.fps(0.0) == 6.0 and not g.bursting(0.0)
    g.trigger(1.0, "threat")
    assert g.fps(2.0) == 12.0 and g.bursting(3.9)
    assert g.fps(1.0 + sch.BURST_HOLD_S + 0.01) == 6.0
    assert g.fps(10.0, unfocused=True) == sch.UNFOCUSED_FPS
    assert g.period(10.0, paused="minimized") == pytest.approx(sch.PAUSED_PERIOD_S)
    g.configure(10.0, 8.0)                 # calm never above burst
    assert (g.calm_fps, g.burst_fps) == (8.0, 8.0)


def track(key, rel, pos, visible=True, appeared_at=None):
    return NS(key=key, relation=rel, visible=visible, appeared_at=appeared_at, position=lambda: pos)


def test_burst_reason():
    me = (0.5, 0.5)
    far = [track("A", "enemy", (0.9, 0.1))]
    assert sch.burst_reason(10.0, far, me, 0.22) is None
    assert sch.burst_reason(10.0, far, me, 0.22, threat=1) == "threat"
    assert sch.burst_reason(10.0, far, me, 0.22, fighting=True) == "fight"
    assert sch.burst_reason(10.0, [track("B", "enemy", (0.6, 0.6))], me, 0.22) == "near"
    assert sch.burst_reason(10.0, [track("C", "enemy", (0.9, 0.9), appeared_at=9.0)], me, 0.22) == "appeared"
    assert sch.burst_reason(10.0, [track("D", "ally", (0.5, 0.5))], me, 0.22) is None


# ------------------------------------------------------------------ heavy scheduler
def test_heavy_scheduler_synchronous_keeps_historical_behaviour():
    h = sch.HeavyScheduler(2.0)
    plans = [h.plan(i * 0.125, stagger=False) for i in range(9)]
    assert plans[0] == set(sch.SLOTS) and plans[4] == set(sch.SLOTS) and plans[8] == set(sch.SLOTS)
    assert all(p == set() for i, p in enumerate(plans) if i % 4)


def test_heavy_scheduler_stagger_one_slot_per_tick_all_slots_served():
    h = sch.HeavyScheduler(2.0)
    plans = [h.plan(i * 0.125, stagger=True) for i in range(40)]      # 5 s at 8 fps
    assert all(len(p) <= 1 for p in plans)
    for s in sch.SLOTS:
        assert 8 <= h.runs[s] <= 11, h.runs                              # ~2 Hz each
    # slower ticks (4 fps): still one per tick, each slot ~1 Hz
    h2 = sch.HeavyScheduler(2.0)
    plans = [h2.plan(i * 0.25, stagger=True) for i in range(40)]         # 10 s
    assert all(len(p) == 1 for p in plans[1:])
    assert all(h2.runs[s] >= 9 for s in sch.SLOTS)
    # clock going back (new timeline): re-anchored, no starvation
    assert h2.plan(0.0, stagger=True) is not None


# ------------------------------------------------------------------ motion snapshot / prediction
def walking_track(key="Ahri", v=(0.03, 0.0), n=10, dt=0.125, t0=0.0):
    tr = Track(key=key, alias=key, relation="enemy", team="CHAOS", first_seen=t0, last_seen=t0)
    from treeaicoach.tracker import _kf_init, _kf_step
    for i in range(n):
        t = t0 + i * dt
        u, vv = 0.2 + v[0] * (t - t0), 0.5 + v[1] * (t - t0)
        tr._obs.append((t, u, vv))
        if tr._kf is None:
            tr._kf = _kf_init(t, u, vv)
        else:
            _kf_step(tr._kf, t, u, vv)
        tr.last_seen = t
    return tr


def test_motion_snapshot_predicts_to_render_time():
    tr = walking_track()
    last = tr.last_seen
    snap = sch.MotionSnapshot.from_tracks(last, [tr])
    truth_now = 0.2 + 0.03 * (last + 0.25)
    pred = snap.predict(last + 0.25)["Ahri"]
    (u, v), age = pred
    assert age == pytest.approx(0.25)
    med = tr.position()[0]
    assert abs(u - truth_now) < abs(med - truth_now)          # prediction beats the median
    assert abs(u - truth_now) < 0.004
    # beyond the horizon: no runaway extrapolation
    (u2, _), _ = snap.predict(last + 10.0)["Ahri"]
    assert u2 < 0.2 + 0.03 * (last + sch.PREDICT_HORIZON_S) + 0.01


def test_motion_snapshot_skips_hidden_and_handles_stacked():
    hidden = NS(key="Zed", visible=False, last_seen=1.0, stacked_with=None, position=lambda: (0.1, 0.1))
    stacked = NS(key="Lux", visible=True, last_seen=1.0, stacked_with="Ahri", position=lambda: (0.3, 0.3),
                 predict=lambda *a, **k: (0.9, 0.9))
    snap = sch.MotionSnapshot.from_tracks(1.0, [hidden, stacked])
    out = snap.predict(1.2)
    assert "Zed" not in out and out["Lux"][0] == (0.3, 0.3)     # stacked: the occluder's position


# ------------------------------------------------------------------ budget
def test_perf_budget_by_cores_and_measurement():
    assert sysperf.PerfBudget("auto", cores=2).profile.name == "low_end"
    b = sysperf.PerfBudget("auto", cores=12, target_fps=12.0)
    assert b.profile.name == "normal" and b.profile.burst_fps == 12.0 and b.profile.calm_fps == 6.0
    t = 0.0
    switched = False
    while t < sysperf.MEASURE_S + 2:
        switched = b.observe_tick(t, 45.0) or switched
        t += 0.1
    assert switched and b.profile.name == "low_end" and "lente" in b.reason
    fast = sysperf.PerfBudget("auto", cores=12)
    t = 0.0
    while t < sysperf.MEASURE_S + 2:
        assert not fast.observe_tick(t, 12.0)
        t += 0.1
    assert fast.profile.name == "normal"
    assert sysperf.PerfBudget("low_end", cores=16).profile.overlay_fps == 15.0
    assert sysperf.PerfBudget("normal", cores=2).profile.name == "normal"
    assert sysperf.PerfBudget("normal", target_fps=5.0).profile.burst_fps == 5.0      # user cap


def test_rolling_stats_rate_and_cpu_meter():
    st = sysperf.RollingStats(10)
    for x in range(1, 21):
        st.add(x)
    st.add(float("nan"))
    assert len(st) == 10 and st.pct(0.5) in (15, 16) and st.summary()["max"] == 20
    rm = sysperf.RateMeter()
    for i in range(11):
        rm.tick(i * 0.1)
    assert rm.rate() == pytest.approx(10.0)
    clock = [0.0]
    cpu = [0.0]
    m = sysperf.CpuMeter(clock=lambda: clock[0], cpu_time=lambda: cpu[0])
    assert m.sample() is None
    clock[0], cpu[0] = 2.0, 0.5
    assert m.sample() == 25.0


def test_precise_sleep_honours_stop():
    ev = threading.Event()
    t0 = time.perf_counter()
    assert sysperf.precise_sleep(0.03, ev) is False
    assert time.perf_counter() - t0 >= 0.029
    ev.set()
    t0 = time.perf_counter()
    assert sysperf.precise_sleep(5.0, ev) is True and time.perf_counter() - t0 < 0.5


def test_lower_priority_is_a_noop_off_windows():
    if sys.platform == "win32":
        pytest.skip("off-Windows behaviour")
    assert sysperf.lower_process_priority() == {"priority": False, "eco_qos": False}
    hp = sysperf.hardware_profile()
    assert hp["cores"] >= 1 and isinstance(hp["low_end_hint"], bool)


def test_main_applies_the_process_policy(monkeypatch):
    from treeaicoach import main

    calls = []
    monkeypatch.setattr(sysperf, "lower_process_priority", lambda eco_qos=True: calls.append(eco_qos) or {})
    main.apply_process_policy(NS(low_priority=True, eco_qos=False))
    main.apply_process_policy(NS(low_priority=False, eco_qos=True))
    assert calls == [False]
    import cv2
    assert cv2.getNumThreads() <= 2
