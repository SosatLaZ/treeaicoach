"""Detection measures of the 15-ideas round (DETECTION_IDEAS.md): elimination of my team's one
missing champion (ideas 29 / 69 / 20) and the sequence replay of the real cases."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np

import treeaicoach.roster_matcher as RM

ROOT = Path(__file__).resolve().parents[1]


def _ring_matcher(ents, score):
    m = RM.RosterMatcher.__new__(RM.RosterMatcher)
    m._entries = ents
    S = np.zeros((40, 40), np.float32)
    S[20, 20] = 0.3                                    # one unexplained ring of my team's colour
    m.ring_map = lambda feat, R: {"ally": S}
    m._structures = lambda: []
    m._get_verifier = lambda: None

    def rescue(c, *a, **k):
        m.last_rescue_pos = (c.x, c.y)
        return score.get(ents[c.i].alias)
    m._rescue = rescue
    return m


def _props(m):
    return m._ring_proposals(None, None, [], set(), set(), 1.0, 1.0, 4.0, 8.0, 0.8)


def test_one_missing_ally_needs_a_weaker_portrait_score():
    me = SimpleNamespace(alias="Garen", relation="self")
    ally = SimpleNamespace(alias="Lux", relation="ally")
    enemy = SimpleNamespace(alias="Zed", relation="enemy")
    weak = (RM.ELIM_ALLY_ID_MIN + RM.RING_PROP_ID_MIN) / 2       # below the usual minimum
    # me matched (used), Lux the only missing ally: the ring is Lux
    m = _ring_matcher([me, ally, enemy], {"Lux": weak})
    out = m._ring_proposals(None, None, [], {0}, set(), 1.0, 1.0, 4.0, 8.0, 0.8)
    assert [m._entries[c.i].alias for c in out] == ["Lux"]
    # two allies missing: the usual identity rule (no elimination)
    ally2 = SimpleNamespace(alias="Ashe", relation="ally")
    m = _ring_matcher([me, ally, ally2, enemy], {"Lux": weak, "Ashe": 0.1})
    assert m._ring_proposals(None, None, [], {0}, set(), 1.0, 1.0, 4.0, 8.0, 0.8) == []
    # only I am missing: no elimination (my position has its own rules)
    m = _ring_matcher([me, ally, enemy], {"Garen": weak})
    assert m._ring_proposals(None, None, [], {1}, set(), 1.0, 1.0, 4.0, 8.0, 0.8) == []
    # below the elimination minimum: nothing
    m = _ring_matcher([me, ally, enemy], {"Lux": RM.ELIM_ALLY_ID_MIN - 0.05})
    assert m._ring_proposals(None, None, [], {0}, set(), 1.0, 1.0, 4.0, 8.0, 0.8) == []


def test_sequence_replay_keeps_one_detector_and_the_clock(monkeypatch, tmp_path):
    sys.path.insert(0, str(ROOT / "tools"))
    import real_minimap_bench as B

    import cv2
    cv2.imwrite(str(tmp_path / "a.png"), np.zeros((40, 40, 3), np.uint8))
    truth = {"rosters": {"g": {}}, "images": {
        "late": {"game": "g", "file": "a.png", "time": 70.0, "champions": []},
        "early": {"game": "g", "file": "a.png", "time": 10.0, "champions": []}}}
    (tmp_path / "ground_truth.json").write_text(__import__("json").dumps(truth))
    seen, made, rosters = [], [], []

    class M:
        has_roster = False

        def set_entries(self, e):
            rosters.append(1)
            self.has_roster = True

        def detect(self, bgr, t=None):
            seen.append(t)
            return []

        def set_dead(self, aliases, now=None):
            pass

    class Det:
        def __init__(self):
            self.matcher = M()
            made.append(self)

        def detect(self, img):
            return self.matcher.detect(img)

    import treeaicoach.champions as C
    import treeaicoach.detector as DT
    monkeypatch.setattr(DT, "create_detector", lambda *a, **k: Det())
    monkeypatch.setattr(C, "get_default_db", lambda: None)
    monkeypatch.setattr(B, "roster_entries", lambda game, db: [])
    res = B.run_all(fix=tmp_path, sequence=True)
    assert list(res) == ["early", "late"]                  # time order
    assert len(made) == 1 and len(rosters) == 1           # one detector, roster set once
    assert seen[0] < seen[B.FRAMES - 1] < seen[B.FRAMES] and seen[B.FRAMES] - seen[0] >= 60.0


def test_held_icon_seen_again_elsewhere_is_not_an_impossible_jump():
    import treeaicoach.tracker as T

    t = T.Track(key="Zed", alias="Zed", relation="enemy", team=None, first_seen=0.0, last_seen=0.0)
    assert t.observe(0.0, 0.5, 0.5)
    t._stack_pos, t._stack_t = (0.5, 0.5), 10.0           # held under an ally icon until t=10
    # 0.25 from the pile right now, but on foot from his last sighting (10 s ago): accepted
    assert not t._impossible(10.1, 0.75, 0.5)
    # farther than he can have walked from both: held back
    assert t._impossible(10.1, 0.5 + T.MAX_WALK_SPEED * 10.1 + T.JUMP_SLACK + 0.05, 0.5)


def test_icon_cut_by_the_corner_named_by_its_visible_part():
    """Idea 28: a portrait cut by the map corner (fountain) is scored on its part inside."""
    import pytest

    from treeaicoach.champions import ChampionDB
    from treeaicoach.render import ChampionSprite, MinimapRenderer, Scene
    from treeaicoach.roster_matcher import RosterEntry, RosterMatcher

    db = ChampionDB(cache_dir=Path("/nonexistent-treeaicoach-cache"))
    r = MinimapRenderer()
    if len(db) < 20 or not r.textures():
        pytest.skip("icons / textures missing")
    names = ["Garen", "Lux", "Ashe", "Zed", "Ahri"]
    icons = {a: db.load_icon(a) for a in names}
    if any(v is None for v in icons.values()):
        pytest.skip("icons missing")
    ents = [RosterEntry(a, rel, icons[a]) for a, rel in
            zip(names, ["self", "ally", "ally", "enemy", "enemy"])]
    m = RosterMatcher(db=None)
    m.set_entries(ents)
    rad, size = 0.047, 300
    sc = Scene(texture=str(r.textures()[0]), size=size, camera=(0.5, 0.1, 0.775, 0.255),
               champions=[ChampionSprite(u=0.03, v=0.968, r=rad, relation="ally", icon=icons["Lux"]),
                          ChampionSprite(u=0.5, v=0.5, r=rad, relation="self", icon=icons["Garen"])])
    img = r.render(sc)
    m._state.scale = 2 * rad
    alias, best, second = m.name_partial(img, 0.03, 0.968, ["Lux", "Ashe"])
    assert alias == "Lux" and best >= 0.6 and best - second >= 0.15
    assert m.name_partial(img, 0.03, 0.968, []) == (None, 0.0, 0.0)
    assert m.name_partial(None, 0.03, 0.968, ["Lux"]) == (None, 0.0, 0.0)     # never raises
