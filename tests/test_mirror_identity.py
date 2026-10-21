"""Mirror match (the same champion in both teams): identity is (champion, team)."""

from __future__ import annotations

import numpy as np

from treeaicoach.detector import Detection
from treeaicoach.identifier import Identified, _Member, _Roster, ChampionIdentifier
from treeaicoach.tracker import Tracker


def _det(u: float, v: float, cls: str, alias: str) -> Detection:
    return Detection(u=u, v=v, r=0.045, score=0.9, cls=cls, cls_probs=(0.0, 0.0, 0.0), alias=alias)


def _roster() -> _Roster:
    members = (_Member("Yone", 0, "self", "ORDER"), _Member("Lux", 0, "ally", "ORDER"),
               _Member("Yone", 0, "enemy", "CHAOS"), _Member("Zed", 0, "enemy", "CHAOS"))
    return _Roster(members=members, feats=np.zeros((4, 1), np.float32), n_variants=1,
                   member_side=np.int8([1, 1, 0, 0]), my_team="ORDER", has_me=True)


def test_trusted_alias_takes_the_detection_side_in_a_mirror() -> None:
    dets = [_det(0.1, 0.2, "ally", "Yone"), _det(0.15, 0.2, "enemy", "Yone"), _det(0.5, 0.5, "enemy", "Zed")]
    out = [Identified(det=d, alias=None, relation="ally", team=None, id_score=0.0) for d in dets]
    ChampionIdentifier._trust_aliases(dets, _roster(), out)
    assert (out[0].alias, out[0].relation, out[0].team) == ("Yone", "self", "ORDER")
    assert (out[1].alias, out[1].relation, out[1].team) == ("Yone", "enemy", "CHAOS")   # never "me"
    assert (out[2].alias, out[2].relation) == ("Zed", "enemy")


def test_tracker_get_side_never_crosses_teams() -> None:
    tr = Tracker()
    me = Identified(det=_det(0.1, 0.2, "ally", "Yone"), alias="Yone", relation="self", team="ORDER", id_score=0.9)
    foe = Identified(det=_det(0.6, 0.6, "enemy", "Yone"), alias="Yone", relation="enemy", team="CHAOS",
                     id_score=0.9)
    for k in range(6):
        tr.update(k * 0.125, [me, foe])
    mine, theirs = tr.get_side("Yone", "self"), tr.get_side("Yone", "enemy")
    assert mine is not None and theirs is not None and mine.key != theirs.key
    assert abs(mine.position()[0] - 0.1) < 0.02 and abs(theirs.position()[0] - 0.6) < 0.02
    assert tr.get_side("Lux", "ally") is None and tr.get_side("", "enemy") is None


def test_gym_mirror_lane_me_and_enemy_copy_kept_apart() -> None:
    """det_gym mirror suite: me top vs the same champion top. Before the (champion, team) fix
    the enemy copy was drawn on me every frame (team errors) and my position was lost."""
    import sys
    from pathlib import Path

    import pytest

    pytest.importorskip("cv2")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    import det_gym as G
    from training.real_art import RealArt
    from treeaicoach.champions import get_default_db

    sc = [s for s in G.mirror_scenarios(quick=True) if s.name == "mirror_lane"][0]
    sc.seconds = 8.0
    G._patch_clock()
    try:
        M = G.run_game(sc, get_default_db(), RealArt())
    finally:
        G._unpatch_clock()
    assert M.team_wrong == 0
    assert M.me_n > 20 and M.me_bad <= 0.1 * M.me_n
