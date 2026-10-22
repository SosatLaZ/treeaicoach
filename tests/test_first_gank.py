"""first_gank.py: the enemy jungler's first clear ends next to his red buff (~3:00-3:45)."""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from test_game_changers import ctx_for, game  # noqa: E402

from treeaicoach import first_gank as fg  # noqa: E402
from treeaicoach import macro  # noqa: E402


def test_danger_lane_is_next_to_enemy_red_buff():
    assert fg.danger_lane("ORDER") == "top" and fg.danger_lane("CHAOS") == "bot"
    assert fg.danger_lane(None) is None


def test_call_for_top_vs_chaos_in_window():
    call = fg.rule_first_gank(ctx_for(game(170.0)))
    assert call is not None and call.kind == fg.KIND
    assert call.text == "Reste près de ta tour : Lee Sin arrive de ton côté" and len(call.text) <= 60
    assert call.voice == ""                                  # forecast: card only, never spoken
    assert fg.rule_first_gank(ctx_for(game(150.0))) is None   # too early
    assert fg.rule_first_gank(ctx_for(game(230.0))) is None   # too late for a new call


def test_no_call_when_dead_seen_elsewhere_or_out_of_lane():
    assert fg.rule_first_gank(ctx_for(game(170.0, dead={"LeeSin": 10.0}))) is None
    lee = SimpleNamespace(alias="LeeSin", uv=(0.78, 0.86), visible=True, hidden_s=0.0)
    assert fg.rule_first_gank(ctx_for(game(170.0), enemies=[lee])) is None
    assert fg.rule_first_gank(ctx_for(game(170.0), me_uv=(0.5, 0.5))) is None


def test_registered_in_planner():
    kinds = {c.kind for c in macro.evaluate(ctx_for(game(170.0)))}
    assert fg.KIND in kinds
    assert fg.KIND in macro.KIND_COOLDOWN_S and fg.KIND in macro.OVERLAPS
