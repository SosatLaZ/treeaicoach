"""Compact card jungler tracker row (2.15 real feedback "pas de tracker") + fog timer placement."""

import numpy as np

from treeaicoach import overlay_render as orr


def _st(line, **kw):
    return orr.OverlayState(jungler_line=line, **kw)


def test_tracker_row_text():
    assert orr.compact_jungler(_st("Jungler : Lee Sin — vu il y a 25 s, top")) == "Lee Sin · vu il y a 25 s, top"
    assert orr.compact_jungler(_st("Jungler : Lee Sin — vu il y a 2:30, bot")) == "Lee Sin · vu il y a 2:30, bot"
    assert orr.compact_jungler(_st("Jungler : Lee Sin — mort (12 s)")) == "Lee Sin · mort (12 s)"
    assert orr.compact_jungler(_st("Jungler : Lee Sin — vu il y a 3:05, bot")) == ""      # too old
    assert orr.compact_jungler(_st("Jungler : Lee Sin — visible, rivière du haut")) == ""  # ring on the map
    assert orr.compact_jungler(_st("Jungler : Lee Sin — pas encore vu")) == ""
    assert orr.compact_jungler(_st(None)) == ""


def test_tracker_row_alone_shows_a_small_card():
    st = _st("Jungler : Lee Sin — vu il y a 25 s, top")
    assert orr.compact_content(st, 0.0) is None and orr.hud_visible(st, 0.0)
    img = orr.render_hud(st, 300, now=0.0)
    assert img.shape[0] < 45 and img[..., 3].any()
    assert not orr.hud_visible(_st(None), 0.0)


def test_tracker_row_under_the_instruction():
    st = _st("Jungler : Lee Sin — vu il y a 10 s, bot", tip="Pousse ta vague puis rentre", tip_since=0.0)
    with_row = orr.render_hud(st, 300, now=1.0)
    without = orr.render_hud(_st(None, tip="Pousse ta vague puis rentre", tip_since=0.0), 300, now=1.0)
    assert with_row.shape[0] > without.shape[0]


def test_fog_timer_never_at_a_multi_point_centroid():
    fog = orr.FogEstimate(key="LeeSin", alias="LeeSin", name="Lee Sin", last_uv=(0.5, 0.5), last_seen=0.0,
                          elapsed=2.0, speed=0.02, radius=0.2, region=None, confidence=0.9, is_jungler=True,
                          seeds=((0.1, 0.1), (0.9, 0.9)))
    e = orr.EnemyView(key="LeeSin", alias="LeeSin", name="Lee Sin", visible=False, uv=None, is_jungler=True)
    st = orr.OverlayState(enemies=[e], fogs=[fog], my_team="ORDER")
    a = orr.render_minimap(st, 256, 256, now=0.0)
    fog.seeds = ()
    b = orr.render_minimap(st, 256, 256, now=0.0)
    c = (slice(110, 146), slice(100, 156))
    assert not a[c][..., 3].any() and b[c][..., 3].any()


def test_jungler_last_position_kept_next_to_an_identified_icon():
    lee = orr.EnemyView(key="LeeSin", alias="LeeSin", name="Lee Sin", visible=False, uv=(0.80, 0.80),
                        last_seen_ago=90.0, is_jungler=True)
    cait = orr.EnemyView(key="Caitlyn", alias="Caitlyn", name="Caitlyn", visible=True, uv=(0.83, 0.82))
    st = orr.OverlayState(enemies=[lee, cait], my_team="ORDER")
    a = orr.render_minimap(st, 256, 256, now=0.0)
    cait.confidence = 0.3                      # anonymous icon there: may be him, no ghost
    b = orr.render_minimap(st, 256, 256, now=0.0)
    assert int((a[..., 3] > 0).sum()) > int((b[..., 3] > 0).sum())
