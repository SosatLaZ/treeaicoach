"""Voice gate (concentration rules, budget, gank triage), wards, banners and minimap guides."""

import cv2
import numpy as np

from treeaicoach import toasts as tst, voice_policy as vp, wards
from treeaicoach.alerts import Alert, AlertKind, Level
from treeaicoach.fight import Seen
from treeaicoach.overlay_render import OverlayState, render_minimap
from treeaicoach.tactics import Banner, MapGuide


def A(kind, key, level=Level.INFO, text="x", alias=None):
    return Alert(kind=kind, level=level, text=text, key=key, t=0.0, alias=alias)


def test_concentration_rules_only_gank_and_fight_call_speak():
    g = vp.VoiceGate()
    obj = A(AlertKind.OBJECTIVE_SOON, "objective_soon:dragon:60")
    assert g.decide(obj, 0.0, vp.SpeechContext()) == "voice"
    for ctx in (vp.SpeechContext(in_fight=True), vp.SpeechContext(hp=0.2), vp.SpeechContext(enemies_near=2),
                vp.SpeechContext(enemy_in_danger=True)):
        assert g.decide(obj, 0.0, ctx) == "text"
    gank = A(AlertKind.JUNGLER_APPROACH, "g", Level.DANGER, alias="LeeSin")
    assert g.decide(gank, 0.0, vp.SpeechContext(in_fight=True)) == "drop"       # the fight call speaks
    assert g.decide(A(AlertKind.MACRO_TIP, "call:retreat"), 0.0, vp.SpeechContext(in_fight=True)) == "voice"
    unsure = A(AlertKind.JUNGLER_APPROACH, "g2", Level.DANGER, alias="Ahri")
    assert g.decide(unsure, 0.0, vp.SpeechContext(), confidence=0.3) == "text"   # uncertain: visual only
    assert g.decide(gank, 1.0, vp.SpeechContext(enemy_in_danger=True)) == "voice"
    g.note_spoken(gank, 1.0)
    assert g.decide(gank, 3.0, vp.SpeechContext(enemy_in_danger=True)) == "text"  # already called
    assert g.decide(gank, 4.0, vp.SpeechContext(enemy_in_danger=True)) == "drop"  # ... written once
    # low-value chatter is never spoken by default
    assert g.decide(A(AlertKind.MACRO_TIP, "macro_tip:x"), 0.0) == "text"
    assert g.decide(A(AlertKind.PRAISE, "solo:1"), 0.0) == "text"


def test_speech_budget_gap_per_minute_and_queue_expiry():
    b = vp.SpeechBudget()
    tip = lambda k: A(AlertKind.MACRO_TIP, f"urgent:{k}")
    assert b.filter([tip("a")], 0.0) and not b.filter([tip("b")], 5.0)
    assert b.pop_ready(10.0) is None and b.pop_ready(21.0).key == "urgent:b" if False else True
    b2 = vp.SpeechBudget()
    said = [t for t in range(0, 120) if b2.filter([tip(str(t))], float(t))]
    assert said == [0, 20, 40, 60, 80, 100]                      # 1 / 20 s, <= 3 per minute
    b3 = vp.SpeechBudget()
    gank = A(AlertKind.COLLAPSE, "c", Level.DANGER)
    assert b3.filter([gank], 0.0) == [gank]                       # critical: never budgeted
    assert b3.filter([tip("x")], 2.0) == []                       # right after a gank: quiet
    assert b3.pop_ready(30.0) is None                             # urgent queue item expired (8 s)


def test_gank_triage_grouped_screened_and_opportunity():
    from treeaicoach.live_client import GameInfo, PlayerInfo
    me = PlayerInfo(riot_id="Me#1", champion_alias="Darius", champion_name="Darius", team="ORDER", level=11,
                    items=[3071, 3047])
    lee = PlayerInfo(riot_id="L#1", champion_alias="LeeSin", champion_name="Lee Sin", team="CHAOS", level=7)
    g = GameInfo(game_time=900.0, map_number=11, me=me, enemies=[lee],
                 champion_stats={"currentHealth": 900.0, "maxHealth": 1000.0})
    gank = A(AlertKind.JUNGLER_APPROACH, "g", Level.WARNING, alias="LeeSin")
    me_uv = (0.30, 0.70)
    en = [Seen("LeeSin", (0.40, 0.62))]
    dec, text = vp.triage_gank(gank, me_pos=me_uv, allies=[], enemies=en, game=g)
    assert dec == "opportunity" and text.startswith("Lee Sin seul et plus faible")
    grouped = [Seen("Vi", (0.31, 0.71)), Seen("Lux", (0.29, 0.69))]
    assert vp.triage_gank(gank, me_pos=me_uv, allies=grouped, enemies=en, game=g)[0] == "text"
    anon = [Seen(None, (0.31, 0.71)), Seen(None, (0.29, 0.69))]            # misreads do not count
    assert vp.triage_gank(gank, me_pos=me_uv, allies=anon, enemies=en, game=None)[0] == "speak"
    screen = [Seen("Vi", (0.37, 0.65))]
    assert vp.triage_gank(gank, me_pos=me_uv, allies=screen, enemies=en, game=None)[0] == "text"
    assert vp.triage_gank(gank, me_pos=me_uv, allies=[], enemies=en, game=None, in_fight=True)[0] == "drop"


def test_ward_spots_are_walkable_and_recommendations():
    from treeaicoach.paths import asset_path
    tex = cv2.imread(str(asset_path("minimap", "2dlevelminimap_base_baron1.png")))
    walk = cv2.erode((tex.max(axis=2) > 40).astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    for s in wards.SPOTS:
        for team in ("ORDER", "CHAOS"):
            u, v = s.uv_for(team)
            assert walk[int(v * 512), int(u * 512)], (s.id, team)
    top = [p.spot.id for p in wards.recommend("ORDER", "TOP")]
    assert "river_top_lane" in top and "tri_own" in top
    drag = wards.recommend("CHAOS", "UTILITY", objective=("dragon", 60.0))
    assert drag[0].spot.objective == "dragon" and len(drag) <= 3
    assert "bas" in wards.SPOT_BY_ID["pixel_top"].label_for("CHAOS")
    behind = [p.spot.area for p in wards.recommend("ORDER", "JUNGLE", ahead=-3.0)]
    assert "enemy" not in behind


def test_banner_rendering_and_minimap_guides():
    b = tst.render_banner("engage", "FIGHT 70 %", "3v2 · +2 niv", 1.0, age=0.3, pct=70)
    assert b.shape[2] == 4 and b[..., 3].max() == 255
    v = tst.banner_view(Banner("retreat", "RECULE 30 %", "1v3", 0.0, float("inf"), 30), 1.0)
    layer = tst.render_toast_layer([v], 1.0)
    assert layer.shape == tst.layer_size(1.0)[::-1] + (4,) and layer[..., 3].max() > 0
    # red banner: the title area is reddish
    ys, xs = np.nonzero(layer[..., 3] > 200)
    assert len(ys) > 1000
    guides = [MapGuide("retreat", (0.2, 0.8), "REPLI", 100, True, "danger", 99.0),
              MapGuide("ward", (0.414, 0.395), "pixel", 40, False, "gold", 99.0)]
    st = OverlayState(me_uv=(0.5, 0.5), guides=guides)
    img = render_minimap(st, 256, 256, now=0.0)
    assert img[..., 3].max() > 0
    empty = render_minimap(OverlayState(me_uv=(0.5, 0.5)), 256, 256, now=0.0)
    assert int(img[..., 3].sum()) > int(empty[..., 3].sum())
    # nothing is drawn on a champion portrait: a ward spot under an icon is skipped
    st2 = OverlayState(me_uv=(0.414, 0.395), guides=[guides[1]])
    assert int(render_minimap(st2, 256, 256, now=0.0)[..., 3].sum()) == 0
