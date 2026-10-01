"""Pipeline v2 engine + overlay behaviour: adaptive detection rate, staggered coaching slots,
occlusion / minimized freeze, window moves, render-time prediction, dirty-flag overlay refresh,
constant-alpha flash fades, focus hiding, minimap label rules, HUD dead / siege / ace states."""

from __future__ import annotations

import math
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from treeaicoach import overlay as ov
from treeaicoach import overlay_render as orr
from treeaicoach import paths
from treeaicoach.capture import Rect
from treeaicoach.config import Config
from treeaicoach.engine import (ACE_TEXT, MSG_OCCLUDED, SIEGE_TEXT, CoachEngine, siege_state,
                                structure_owner)

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_engine as TE  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setenv(paths.ENV_HOME, str(tmp_path / "home"))
    paths._reset_cache()
    yield
    paths._reset_cache()


def run(eng, clock, n, dt=0.125):
    for _ in range(n):
        clock.t += dt
        eng.step(clock.t)


# ------------------------------------------------------------------ engine scheduling
def test_adaptive_detection_rate():
    cap, loc = TE.FakeCapture(), None
    loc = TE.FakeLocator(cap)
    eng, clock, _ = TE.live_engine(cap, loc, cfg=replace(Config(), perf_mode="normal"))
    run(eng, clock, 8)
    prof = eng._budget.profile
    eng._governor.burst_until = -math.inf          # (the demo minimap has enemies near me)
    assert eng.detect_period(clock.t) == pytest.approx(1.0 / prof.calm_fps)
    eng._governor.trigger(clock.t, "threat")
    assert eng.detect_period(clock.t) == pytest.approx(1.0 / prof.burst_fps)
    eng._governor.burst_until = -math.inf
    eng._unfocused_since = clock.t - 5.0
    assert eng.detect_period(clock.t) == pytest.approx(0.5)            # 2 img/s while alt-tabbed
    eng.apply_config(replace(Config(), adaptive_rate=False, target_fps=10.0))
    assert eng.detect_period(clock.t) == pytest.approx(0.1)


def test_low_end_budget_slows_and_caps():
    cap = TE.FakeCapture()
    eng, clock, _ = TE.live_engine(cap, TE.FakeLocator(cap), cfg=replace(Config(), perf_mode="low_end"))
    run(eng, clock, 4)
    p = eng._budget.profile
    assert (p.name, p.calm_fps, p.burst_fps, p.overlay_fps) == ("low_end", 4.0, 8.0, 15.0)
    assert ov._budget_fps == 15.0
    eng2, clock2, _ = TE.live_engine(cap, TE.FakeLocator(cap), cfg=replace(Config(), perf_mode="normal"))
    run(eng2, clock2, 2)
    assert ov._budget_fps == 30.0


def test_staggered_coaching_slots_one_per_tick():
    cap = TE.FakeCapture()
    eng, clock, _ = TE.live_engine(cap, TE.FakeLocator(cap))
    eng.stagger = True
    plans = []
    orig = eng._heavy.plan
    eng._heavy.plan = lambda t, st, busy=False: plans.append(orig(t, st, busy)) or plans[-1]
    run(eng, clock, 48)
    assert plans and all(len(p) <= 1 for p in plans)
    assert all(eng._heavy.runs[s] >= 3 for s in eng._heavy.runs)


def test_occluded_minimap_freezes_the_tick():
    cap = TE.FakeCapture()
    loc = TE.FakeLocator(cap)
    eng, clock, _ = TE.live_engine(cap, loc)
    run(eng, clock, 10)
    rect = eng.get_status().minimap_rect
    assert rect is not None
    n_tracks = len(eng.tracker().tracks()) if callable(getattr(eng, "tracker", None)) else None
    eng.occlusion_probe = lambda r: True               # League client over the minimap
    grabs_before = sum(1 for g in cap.grabs if g == rect)
    last_update = eng._tracker.last_update
    run(eng, clock, 8)
    assert sum(1 for g in cap.grabs if g == rect) == grabs_before          # client pixels never read
    assert eng._tracker.last_update == last_update                       # tracks frozen, not "unseen"
    assert eng.get_status().message == MSG_OCCLUDED
    eng.occlusion_probe = lambda r: False
    run(eng, clock, 2)
    assert sum(1 for g in cap.grabs if g == rect) > grabs_before
    del n_tracks


def test_window_moved_offsets_the_minimap_without_relocating():
    cap = TE.FakeCapture()
    loc = TE.FakeLocator(cap)
    win = [Rect(0, 0, cap.W, cap.H)]
    clock = TE.Clock()
    client = TE.FakeLiveClient(lambda: TE.game_info(700 + clock.t))
    eng = CoachEngine(Config(), TE.FakeVoice(), detector=TE.ClassicDetector(), live_client=client, clock=clock,
                      locator=loc, window_finder=lambda: win[0], screen_capture=cap,
                      recorder_factory=lambda: None, enable_hotkeys=False, manage_overlay=False)
    run(eng, clock, 6)
    r0 = eng.get_status().minimap_rect
    win[0] = Rect(100, 50, cap.W, cap.H)
    run(eng, clock, 12)
    assert loc.locates == 1
    assert eng.get_status().minimap_rect == r0.offset(100, 50)


def test_overlay_state_carries_prediction_and_freshness():
    cap = TE.FakeCapture()
    eng, clock, _ = TE.live_engine(cap, TE.FakeLocator(cap))
    run(eng, clock, 16)
    st = eng.get_overlay_state()
    assert st is not None and callable(st.predict)
    pos = st.predict()
    assert isinstance(pos, dict)
    moved = orr.with_predicted(st, {v.key: ((0.123, 0.456), 0.05) for v in st.enemies + st.allies})
    vis = [v for v in moved.enemies + moved.allies if v.visible]
    assert all(v.uv == (0.123, 0.456) and v.age == 0.05 for v in vis)
    assert all(not v.visible or v.uv != (0.123, 0.456) for v in st.enemies)   # original untouched


# ------------------------------------------------------------------ siege / ace / HUD line
def gi(gt, my="ORDER", events=(), dead_mine=0):
    mine = [TE.player(a, my, "TOP", dead=i < dead_mine) for i, a in enumerate(("Garen", "Vi", "Ahri", "Jinx", "Lux"))]
    return NS(my_team=my, events=list(events), all_players=lambda: mine)


def test_structure_owner_and_siege_state():
    assert structure_owner("Turret_T1_C_05_A") == "ORDER" and structure_owner("Barracks_T2_L1") == "CHAOS"
    assert siege_state(gi(1500), 1500) == (None, None)
    inner = [{"EventName": "TurretKilled", "TurretKilled": "Turret_T1_R_01_A", "EventTime": 1490.0}]
    assert siege_state(gi(1500, events=inner), 1500) == ("siege", SIEGE_TEXT)
    old = [{"EventName": "InhibKilled", "InhibKilled": "Barracks_T1_L1", "EventTime": 900.0}]
    assert siege_state(gi(1500, events=old), 1500) == (None, None)
    assert siege_state(gi(1500, events=old), 1500, enemies_in_base=2) == ("siege", SIEGE_TEXT)
    outer = [{"EventName": "TurretKilled", "TurretKilled": "Turret_T1_L_03_A", "EventTime": 1495.0}]
    assert siege_state(gi(1500, events=outer), 1500, enemies_in_base=3) == (None, None)   # base intact
    theirs = [{"EventName": "TurretKilled", "TurretKilled": "Turret_T2_C_01_A", "EventTime": 1499.0}]
    assert siege_state(gi(1500, events=theirs), 1500) == (None, None)
    ace = [{"EventName": "Ace", "AcingTeam": "CHAOS", "EventTime": 1490.0}] + old
    assert siege_state(gi(1500, events=ace), 1500) == ("ace", ACE_TEXT)
    assert siege_state(gi(1500, dead_mine=4), 1500)[0] == "ace"


def test_engine_hud_never_safe_during_siege_and_no_lane_tip_when_dead():
    cap = TE.FakeCapture()
    eng, clock, client = TE.live_engine(cap, TE.FakeLocator(cap))
    run(eng, clock, 12)
    eng._tip_text = "Joue agressif avant le niveau 6 : tu es plus fort tôt"
    st = eng.get_overlay_state()
    assert st.threat_text == "SÛR"
    events = [{"EventName": "InhibKilled", "InhibKilled": "Barracks_T1_C1", "EventTime": 700 + clock.t - 5}]
    client.factory = lambda: TE.game_info(700 + clock.t, dead=True, events=events)
    eng._game = client.factory()
    eng._overlay_cache = None
    st = eng.get_overlay_state()
    assert st.threat_level == 2 and "SÛR" not in st.threat_text and st.me_dead
    assert eng._hud_line(clock.t) == SIEGE_TEXT


def test_jungler_line_when_dead_and_early_game_tips_hidden():
    cap = TE.FakeCapture()
    eng, clock, _ = TE.live_engine(cap, TE.FakeLocator(cap))
    run(eng, clock, 4)
    game = TE.game_info(900.0)
    jg = next(p for p in game.enemies if p.has_smite)
    jg = replace(jg, is_dead=True, respawn_timer=18.0)
    eng._game_t = clock.t
    assert eng._jungler_line(game, jg, clock.t) == "Jungler : Lee Sin — mort (18 s)"
    eng._game = TE.game_info(26.0)
    eng._game_t = clock.t
    eng._tactics = eng._coach = None
    eng._text_msg = None
    eng._tip_text = "Joue agressif avant le niveau 6"
    eng._hud_shown = None
    assert eng._hud_line(clock.t) is None                      # 0:26: no lane-phase advice
    eng._game = TE.game_info(300.0)
    eng._hud_shown = None
    assert eng._hud_line(clock.t) == "Joue agressif avant le niveau 6"


def test_go_line_hidden_under_a_prudent_gauge():
    cap = TE.FakeCapture()
    eng, clock, _ = TE.live_engine(cap, TE.FakeLocator(cap))
    run(eng, clock, 4)
    eng._game = TE.game_info(900.0)
    eng._game_t = clock.t
    eng._tactics = eng._coach = None
    eng._text_msg = None
    eng._tip_text = "Joue agressif : leur jungler est mort"
    eng._gauge = NS(current=lambda: NS(step=-1, reason="", since=0.0))
    eng._hud_shown = None
    assert eng._hud_line(clock.t) is None
    eng._gauge = NS(current=lambda: NS(step=1, reason="", since=0.0))
    eng._hud_shown = None
    assert eng._hud_line(clock.t) == "Joue agressif : leur jungler est mort"


def test_trivial_component_chip_after_20_min():
    cap = TE.FakeCapture()
    eng, clock, _ = TE.live_engine(cap, TE.FakeLocator(cap))
    run(eng, clock, 2)
    eng._game_t = clock.t
    rec = NS(completes=False, buy_now=(1036,), buy_now_names=("Épée longue",), item_name="Couperet noir")
    assert eng._trivial_component_buy(rec, TE.game_info(1508.0), clock.t) is True
    assert eng._trivial_component_buy(rec, TE.game_info(600.0), clock.t) is False
    assert eng._trivial_component_buy(replace_ns(rec, completes=True), TE.game_info(1508.0), clock.t) is False


def replace_ns(ns, **kw):
    d = dict(vars(ns))
    d.update(kw)
    return NS(**d)


# ------------------------------------------------------------------ overlay manager (fake windows)
class FakeWin:
    def __init__(self, name):
        self.name, self.visible, self.failed, self.hwnd = name, False, False, 1
        self.updates, self.alphas = 0, []

    def update(self, img, x, y, alpha=255):
        self.updates += 1
        self.visible = True
        self.alphas.append(alpha)

    def set_alpha(self, a):
        self.alphas.append(("alpha", a))

    def hide(self):
        self.visible = False

    def keep_topmost(self):
        pass


def manager_and_windows(monkeypatch):
    monkeypatch.setattr(ov, "_monitor_rect_at", lambda api, x, y: None)
    m = ov.OverlayManager(Config(), lambda: None)
    wins = {n: FakeWin(n) for n in ("flash", "radar", "minimap", "hud", "toasts") + ov.WORLD_WINDOWS}
    api = NS(user32=NS(GetSystemMetrics=lambda i: 1080 if i else 1920))
    return m, wins, api


def overlay_state(**kw):
    e = orr.EnemyView(key="LeeSin", alias="LeeSin", name="Lee Sin", visible=True, uv=(0.4, 0.4), is_jungler=True,
                      age=0.1)
    base = dict(minimap_rect=Rect(1640, 800, 280, 280), screen_rect=Rect(0, 0, 1920, 1080), enemies=[e],
                my_team="ORDER", me_uv=(0.3, 0.7))
    base.update(kw)
    return orr.OverlayState(**base)


def test_minimap_layer_redrawn_only_when_it_changes(monkeypatch):
    m, wins, api = manager_and_windows(monkeypatch)
    cfg = Config()
    st = overlay_state(tip="Farme sous ta tour")          # compact HUD: a card only with something to say
    m._refresh(api, wins, st, cfg, False, {}, None, now=10.0)
    assert wins["minimap"].updates == 1 and wins["hud"].updates == 1
    m._refresh(api, wins, st, cfg, False, {}, None, now=10.04)
    m._refresh(api, wins, st, cfg, False, {}, None, now=10.08)
    assert wins["minimap"].updates == 1                            # same signature: no render, no ULW
    st2 = overlay_state(enemies=[replace(st.enemies[0], uv=(0.45, 0.4))])
    m._refresh(api, wins, st2, cfg, False, {}, None, now=10.12)
    assert wins["minimap"].updates == 2
    m._refresh(api, wins, st2, cfg, False, {}, None, now=10.12 + ov.FORCE_REDRAW_S + 0.01)
    assert wins["minimap"].updates == 3                            # forced refresh (safety)
    stats = m.stats
    assert "render_minimap" in stats and "ulw" in stats and stats["skipped"].get("minimap", 0) >= 2


def test_prediction_moves_the_minimap_layer(monkeypatch):
    m, wins, api = manager_and_windows(monkeypatch)
    pos = {"LeeSin": ((0.4, 0.4), 0.0)}
    st = overlay_state(predict=lambda: dict(pos))
    m._refresh(api, wins, st, Config(), False, {}, None, now=1.0)
    pos["LeeSin"] = ((0.41, 0.40), 0.1)                            # same tick, later render time
    m._refresh(api, wins, st, Config(), False, {}, None, now=1.034)
    assert wins["minimap"].updates == 2


def test_flash_fades_with_constant_alpha(monkeypatch):
    m, wins, api = manager_and_windows(monkeypatch)
    key = m._refresh(api, wins, overlay_state(flash=1.0), Config(), False, {}, None, now=1.0)
    assert wins["flash"].updates == 1 and wins["flash"].alphas[-1] == 255
    key = m._refresh(api, wins, overlay_state(flash=0.5), Config(), False, {}, key, now=1.1)
    assert wins["flash"].updates == 1 and wins["flash"].alphas[-1] == ("alpha", 128)
    m._refresh(api, wins, overlay_state(flash=0.0), Config(), False, {}, key, now=1.2)
    assert not wins["flash"].visible


def test_focus_hiding_with_grace(monkeypatch):
    m, _wins, _api = manager_and_windows(monkeypatch)
    m._foreground = lambda: (False, False)          # League client / browser in front
    assert m._focus_ok(1.0) is True and m._focus_ok(1.2) is True
    assert m._focus_ok(1.0 + ov.FOCUS_GRACE_S + 0.01) is False
    m._foreground = lambda: (True, False)
    assert m._focus_ok(2.0) is True                 # back instantly
    m._foreground = lambda: (False, True)           # our own settings window
    assert m._focus_ok(5.0) is True
    m._foreground = lambda: (None, False)           # no game (demo / overlay test)
    assert m._focus_ok(9.0) is True


# ------------------------------------------------------------------ HUD placement on real screenshots
#: League UI zones measured on the users' 2000 x 1125 screenshots (minimap at 1687, 809, 300 x 300)
REAL_MM = (1687, 809, 300, 300)
REAL_SCREEN = (0, 0, 2000, 1125)
REAL_ZONES = {
    "minimap+frame": (1677, 784, 323, 341),
    "ally portraits row": (1617, 705, 383, 80),
    "surrender / baron vote": (1670, 500, 330, 115),
    "camera buttons": (1600, 1080, 87, 45),
    "item / gold panel": (650, 1000, 650, 125),
}


@pytest.mark.parametrize("detailed", [False, True])
def test_hud_card_default_placement_clears_league_ui(detailed):
    st = replace(orr.sample_states()["warning"], hud_detailed=detailed)
    w = ov.hud_width(REAL_SCREEN)
    h = orr.hud_size(st, w)[1]
    x, y = ov.hud_placement(REAL_SCREEN, w, h, "left_of_minimap", avoid=[REAL_MM], anchor=REAL_MM, minimap=REAL_MM)
    card = (x, y, w, h)
    for name, zone in REAL_ZONES.items():
        assert not ov.rects_overlap(card, zone), name
    assert x >= 0 and y >= 0 and x + w <= 2000 and y + h <= 1125
    if not detailed:          # compact card also clears the open shop (screenshot 4)
        assert not ov.rects_overlap(card, (0, 0, 1558, 933))
    # explicit "above the minimap": clear of the minimap frame at least
    x2, y2 = ov.hud_placement(REAL_SCREEN, w, h, "above_minimap", avoid=[REAL_MM], anchor=REAL_MM,
                              anchor_gap=ov.minimap_clearance(REAL_MM), minimap=REAL_MM)
    assert y2 + h <= REAL_MM[1] - 0.3 * REAL_MM[3]


# ------------------------------------------------------------------ minimap label rules
def capture_tags(monkeypatch):
    texts = []
    orig = orr._tag

    def spy(cv_, x, y, off, text, font, fg, taken, alpha=1.0, drop=False):
        ok = orig(cv_, x, y, off, text, font, fg, taken, alpha, drop)
        if ok:
            texts.append((text, x, y))
        return ok

    monkeypatch.setattr(orr, "_tag", spy)
    return texts


def ev(key, uv, visible=True, ago=None, jungler=False, role=None, dead=False, age=0.1):
    return orr.EnemyView(key=key, alias=key, name=key, visible=visible, uv=uv, last_seen_ago=ago,
                         is_jungler=jungler, role=role, dead=dead, age=age if visible else None)


def test_labels_one_per_champion_capped_and_never_on_live_icons(monkeypatch):
    texts = capture_tags(monkeypatch)
    enemies = [
        ev("Kindred", (0.50, 0.47), visible=False, ago=3.0, jungler=True),     # ghost on a live icon
        ev("Ahri", (0.53, 0.47), role="MIDDLE"),                                # live
        ev("Ezreal", (0.82, 0.83), visible=False, ago=23.0, role="BOTTOM"),     # ghost, not jungler
        ev("Leona", (0.90, 0.82), role="UTILITY"),
        ev("Darius", (0.20, 0.20), role="TOP"),
        ev("Yi", (0.05, 0.05), visible=False, ago=10.0, dead=True, role="JUNGLE"),
    ]
    allies = [ev(f"A{i}", (0.3 + 0.05 * i, 0.9), role=r) for i, r in enumerate(("TOP", "MIDDLE", "BOTTOM"))]
    for a in allies:
        a.relation = "ally"
    st = orr.OverlayState(enemies=enemies, allies=allies, show_roles=True, show_allies=True, my_team="ORDER",
                          roles={"Ahri": "MIDDLE", "Leona": "UTILITY", "Darius": "TOP"})
    orr.render_minimap(st, 300, 300, now=5.0)
    words = [t.split(" ")[0] for t, _x, _y in texts]
    assert len(texts) <= orr.MM_MAX_LABELS
    assert len(words) == len(set(words))                           # one label per role word
    assert not any(t.startswith("JGL") for t, _x, _y in texts)     # jungler ghost sits on a live icon
    assert not any(any(ch.isdigit() for ch in t) and not t.startswith("JGL") for t, _x, _y in texts)


def test_ghost_rules_dead_fountain_and_old(monkeypatch):
    texts = capture_tags(monkeypatch)
    st = orr.OverlayState(enemies=[ev("Kindred", (0.4, 0.4), visible=False, ago=50.0, jungler=True)],
                          my_team="ORDER")
    orr.render_minimap(st, 300, 300, now=1.0)
    assert texts == []                                              # > 45 s: faded ghost, no text
    st = orr.OverlayState(enemies=[ev("Kindred", (0.4, 0.4), visible=False, ago=12.0, jungler=True)],
                          my_team="ORDER")
    orr.render_minimap(st, 300, 300, now=1.0)
    assert [t for t, *_ in texts] == ["JGL 12 s"]
    texts.clear()
    for e in (ev("Kindred", (0.95, 0.05), visible=False, ago=12.0, jungler=True),          # enemy fountain
              ev("Kindred", (0.4, 0.4), visible=False, ago=12.0, jungler=True, dead=True)):  # dead
        orr.render_minimap(orr.OverlayState(enemies=[e], my_team="ORDER"), 300, 300, now=1.0)
    assert texts == []


def test_stale_or_stacked_views_are_ghosts_without_labels(monkeypatch):
    texts = capture_tags(monkeypatch)
    allies = [ev("Jinx", (0.88, 0.63), role="BOTTOM", age=1.2), ev("Thresh", (0.88, 0.66), role="UTILITY")]
    allies[0].stacked = True
    for a in allies:
        a.relation = "ally"
    orr.render_minimap(orr.OverlayState(allies=allies, show_allies=True, show_roles=True, hud_detailed=True),
                       300, 300, now=1.0)
    assert [t for t, *_ in texts] == ["SUP"]
    assert orr.is_ghost(allies[0]) and not orr.is_ghost(allies[1])


# ------------------------------------------------------------------ HUD / toasts wording
def test_hud_dead_header_and_short_chips(monkeypatch):
    drawn = []
    orig = orr.Canvas.text

    def spy(self, x, y, text, *a, **k):
        drawn.append(text)
        return orig(self, x, y, text, *a, **k)

    monkeypatch.setattr(orr.Canvas, "text", spy)
    st = replace(orr.sample_states()["safe"], me_dead=True, respawn_s=7.2, threat_text="SÛR",
                 hint="Pense à la balise de contrôle")
    orr.render_hud(replace(st, hud_detailed=True), 300)
    assert "MORT · retour dans 8 s" in drawn                       # detailed card
    assert not any("SÛR" in t for t in drawn)
    drawn.clear()
    orr.render_hud(st, 300)                                       # compact: the game shows the timer
    assert not any("MORT" in t or "SÛR" in t for t in drawn)
    assert orr.short_chip_text("Pense à la balise de contrôle") == "Balise de contrôle"
    assert orr.short_chip_text("1 450 PO — pense à rentrer") == "Rentrer · 1 450 PO"


def test_toasts_have_no_em_dash():
    from treeaicoach import toasts

    assert toasts.no_em_dash("Thresh — Rayonnement du vide (2e objet)") == "Thresh · Rayonnement du vide (2e objet)"
    img = toasts.render_toast("praise", "Thresh — Rayonnement du vide", "Bien joué")
    assert img.ndim == 3 and img.shape[2] == 4 and img[..., 3].max() > 0
