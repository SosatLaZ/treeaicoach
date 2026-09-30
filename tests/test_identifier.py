"""Tests for treeaicoach.identifier (portrait matching, relations, "self" by identity)."""

from __future__ import annotations

import math
import random
import threading
import time
from pathlib import Path

import cv2
import numpy as np
import pytest

from treeaicoach.champions import ChampionDB
from treeaicoach.detector import Detection
from treeaicoach.identifier import ChampionIdentifier, Identified, find_camera_center
from treeaicoach.live_client import GameInfo, PlayerInfo
from treeaicoach.render import ChampionSprite, MinimapRenderer, Scene

ALLY_P = (0.06, 0.9, 0.04)
ENEMY_P = (0.9, 0.07, 0.03)


@pytest.fixture(scope="module")
def db() -> ChampionDB:
    d = ChampionDB(cache_dir=Path("/nonexistent-treeaicoach-cache"))
    if len(d.all()) < 20:
        pytest.skip("champion icons missing")
    return d


@pytest.fixture(scope="module")
def renderer() -> MinimapRenderer:
    return MinimapRenderer()


def _game(names: list[str], me_team: str = "ORDER", spectator: bool = False) -> GameInfo:
    other = "CHAOS" if me_team == "ORDER" else "ORDER"
    me = None if spectator else PlayerInfo(champion_alias=names[0], team=me_team)
    allies = [PlayerInfo(champion_alias=a, team=me_team) for a in names[1:5]]
    if spectator:
        allies = [PlayerInfo(champion_alias=a, team=me_team) for a in names[:5]]
    enemies = [PlayerInfo(champion_alias=a, team=other) for a in names[5:10]]
    return GameInfo(me=me, allies=allies, enemies=enemies)


def _relation(i: int) -> str:
    return "self" if i == 0 else ("ally" if i < 5 else "enemy")


def _scene(rng, renderer, db, names, idx, size, camera=None, icon_override=None):
    """Render the champions ``idx`` (indices into names) at random non-overlapping places."""
    champs, gts = [], []
    for i in idx:
        for _ in range(300):
            r = rng.uniform(0.043, 0.052)
            u, v = rng.uniform(0.06, 0.94), rng.uniform(0.06, 0.94)
            if all((u - g["u"]) ** 2 + (v - g["v"]) ** 2 > (2.2 * max(r, g["r"])) ** 2
                   for g in gts):
                break
        rel = _relation(i)
        icon = (icon_override or {}).get(i)
        if icon is None:
            icon = db.load_icon(names[i])
        champs.append(ChampionSprite(u, v, r, rel, icon))
        gts.append({"u": u, "v": v, "r": r, "i": i, "rel": rel})
    sc = Scene(texture=rng.choice(renderer.textures()), size=size, champions=champs,
               vision=[(g["u"], g["v"], 0.08) for g in gts if g["rel"] != "enemy"],
               camera=camera)
    return renderer.render(sc), gts


def _degrade(rng, img):
    img = cv2.GaussianBlur(img, (0, 0), rng.uniform(0.3, 0.8))
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, rng.randint(65, 90)])
    assert ok
    return cv2.imdecode(buf, cv2.IMREAD_COLOR)


def _det(rng, g, noise=0.15):
    a = rng.uniform(0.0, 2.0 * math.pi)
    m = rng.uniform(0.0, noise) * g["r"]
    enemy = g["rel"] == "enemy"
    return Detection(u=g["u"] + m * math.cos(a), v=g["v"] + m * math.sin(a),
                     r=g["r"] * rng.uniform(0.93, 1.07), score=0.9,
                     cls="enemy" if enemy else "ally", cls_probs=ENEMY_P if enemy else ALLY_P)


# ======================================================================================
# accuracy
# ======================================================================================


def test_identification_accuracy_rendered(db, renderer):
    rng = random.Random(11)
    aliases = [e.alias for e in db.all()]
    total = correct = wrong = rel_ok = self_ok = self_n = 0
    times = []
    for _ in range(6):                                   # 6 matches x 3 frames x 6-10 icons
        names = rng.sample(aliases, 10)
        ident = ChampionIdentifier(db)
        ident.set_roster(_game(names))
        for _ in range(3):
            idx = rng.sample(range(10), rng.randint(6, 10))
            img, gts = _scene(rng, renderer, db, names, idx, rng.randint(220, 400))
            img = _degrade(rng, img)
            dets = [_det(rng, g) for g in gts]
            t0 = time.perf_counter()
            res = ident.identify(img, dets)
            times.append(time.perf_counter() - t0)
            assert len(res) == len(dets)
            for g, o, d in zip(gts, res, dets):
                assert isinstance(o, Identified) and o.det is d
                total += 1
                if o.alias == names[g["i"]]:
                    correct += 1
                elif o.alias is not None:
                    wrong += 1
                rel_ok += o.relation == g["rel"]
                if g["rel"] == "self":
                    self_n += 1
                    self_ok += o.relation == "self"
                if o.alias is not None:
                    assert o.team == ("CHAOS" if g["rel"] == "enemy" else "ORDER")
                    assert 0.0 < o.id_score <= 1.0
    acc = correct / total
    print(f"identifier: top1={acc:.3f} wrong={wrong / total:.3f} relation={rel_ok / total:.3f} "
          f"self={self_ok}/{self_n} n={total} median {1000 * float(np.median(times)):.2f} ms")
    assert total >= 100
    assert acc >= 0.9
    assert wrong / total <= 0.05
    assert self_ok == self_n
    assert float(np.median(times)) < 0.05      # < 3 ms on an idle CPU; loose for shared CI


def test_skin_template_is_used(db, renderer, tmp_path):
    """A cached skin icon (different portrait) is recognised when the player uses that skin."""
    skin_db = ChampionDB(cache_dir=tmp_path)
    rng = random.Random(4)
    names = rng.sample([e.alias for e in db.all()], 11)
    # fake "skin 7" of the first enemy: portrait of an unrelated champion
    skin_icon = db.load_icon(names[10])
    rgba = skin_icon.copy()
    cv2.imwrite(str(tmp_path / f"{names[5]}_7.png"), cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGRA))
    game = _game(names[:10])
    game.enemies[0].skin_id = 7
    ident = ChampionIdentifier(skin_db)
    ident.set_roster(game)
    img, gts = _scene(rng, renderer, db, names, [5], 300, icon_override={5: skin_icon})
    res = ident.identify(img, [_det(rng, gts[0], 0.05)])
    assert res[0].alias == names[5] and res[0].relation == "enemy"


# ======================================================================================
# relations, self, fallbacks
# ======================================================================================


def test_no_roster_uses_detector_classes(db, renderer):
    rng = random.Random(1)
    names = rng.sample([e.alias for e in db.all()], 10)
    img, gts = _scene(rng, renderer, db, names, [0, 3, 7], 280)
    dets = [_det(rng, g, 0.0) for g in gts]
    dets.append(Detection(0.5, 0.5, 0.047, 0.8, "self", (0.1, 0.2, 0.7)))
    ident = ChampionIdentifier(db)
    ident.set_roster(None)
    res = ident.identify(img, dets)
    assert [o.alias for o in res] == [None] * 4
    assert [o.relation for o in res] == [d.cls for d in dets]
    assert all(o.team is None for o in res)


def test_unidentified_relations_follow_ring_and_team(db, renderer):
    rng = random.Random(2)
    names = rng.sample([e.alias for e in db.all()], 13)
    ident = ChampionIdentifier(db)
    ident.set_roster(_game(names[:10], me_team="CHAOS"))
    # champions NOT in the roster -> no identity, relation from the class, never "self"
    img, gts = _scene(rng, renderer, db, names, [10, 11, 12], 300)
    dets = [Detection(g["u"], g["v"], g["r"], 0.9, c, p) for g, (c, p) in
            zip(gts, [("enemy", ENEMY_P), ("ally", ALLY_P), ("self", (0.1, 0.2, 0.7))])]
    res = ident.identify(img, dets)
    assert all(o.alias is None for o in res)
    assert [o.relation for o in res] == ["enemy", "ally", "ally"]
    assert [o.team for o in res] == ["ORDER", "CHAOS", "CHAOS"]


def test_self_by_identity_even_with_ally_ring(db, renderer):
    rng = random.Random(3)
    names = rng.sample([e.alias for e in db.all()], 10)
    ident = ChampionIdentifier(db)
    ident.set_roster(_game(names))
    img, gts = _scene(rng, renderer, db, names, [0, 1, 2, 5], 256)
    res = ident.identify(img, [_det(rng, g) for g in gts])
    assert res[0].relation == "self" and res[0].alias == names[0]
    assert [o.relation for o in res[1:]] == ["ally", "ally", "enemy"]


def test_spectator_has_no_self(db, renderer):
    rng = random.Random(8)
    names = rng.sample([e.alias for e in db.all()], 10)
    ident = ChampionIdentifier(db)
    ident.set_roster(_game(names, spectator=True))
    img, gts = _scene(rng, renderer, db, names, [0, 6], 300)
    res = ident.identify(img, [_det(rng, g, 0.05) for g in gts])
    assert [o.alias for o in res] == [names[0], names[6]]
    assert [o.relation for o in res] == ["ally", "enemy"]


def test_unique_assignment_with_duplicates(db, renderer):
    rng = random.Random(5)
    names = rng.sample([e.alias for e in db.all()], 10)
    ident = ChampionIdentifier(db)
    ident.set_roster(_game(names))
    img, gts = _scene(rng, renderer, db, names, [6], 300)
    g = gts[0]
    d1 = _det(rng, g, 0.0)
    d2 = Detection(g["u"] + 0.1 * g["r"], g["v"], g["r"], 0.5, "enemy", ENEMY_P)
    res = ident.identify(img, [d1, d2])
    assert [o.alias for o in res].count(names[6]) == 1
    assert res[0].alias == names[6]
    assert res[1].alias is None and res[1].relation == "enemy"


def test_camera_fallback_for_self(db, renderer):
    """My portrait is not recognisable (unknown skin): nearest ally to the camera centre."""
    rng = random.Random(6)
    names = rng.sample([e.alias for e in db.all()], 11)
    ident = ChampionIdentifier(db)
    ident.set_roster(_game(names[:10]))
    champs = [ChampionSprite(0.40, 0.55, 0.047, "self", db.load_icon(names[10])),
              ChampionSprite(0.75, 0.25, 0.047, "ally", db.load_icon(names[10]))]
    cam = (0.40 - 0.1375, 0.55 - 0.0775, 0.40 + 0.1375, 0.55 + 0.0775)
    img = renderer.render(Scene(size=300, champions=champs, camera=cam,
                                vision=[(0.4, 0.55, 0.1), (0.75, 0.25, 0.1)]))
    c = find_camera_center(img)
    assert c is not None and math.hypot(c[0] - 0.40, c[1] - 0.55) < 0.02
    dets = [Detection(0.75, 0.25, 0.047, 0.9, "ally", ALLY_P),
            Detection(0.40, 0.55, 0.047, 0.9, "ally", ALLY_P)]
    res = ident.identify(img, dets)
    assert [o.relation for o in res] == ["ally", "self"]
    assert res[1].alias is None and res[1].team == "ORDER"
    # no camera rectangle -> no guess
    img2 = renderer.render(Scene(size=300, champions=champs, camera=None))
    assert find_camera_center(img2) is None
    assert [o.relation for o in ident.identify(img2, dets)] == ["ally", "ally"]


def test_camera_center_absent_on_plain_maps(renderer):
    rng = random.Random(9)
    for tex in renderer.textures()[:4]:
        img = renderer.render(Scene(texture=tex, size=rng.choice([220, 300, 400])))
        assert find_camera_center(img) is None
    assert find_camera_center(np.zeros((10, 10, 3), np.uint8)) is None
    assert find_camera_center(None) is None  # type: ignore[arg-type]


# ======================================================================================
# robustness
# ======================================================================================


def test_edge_cases_never_raise(db, renderer):
    rng = random.Random(7)
    names = rng.sample([e.alias for e in db.all()], 10)
    ident = ChampionIdentifier(db)
    ident.set_roster(_game(names))
    img, gts = _scene(rng, renderer, db, names, [2], 300)
    weird = [
        Detection(0.0, 0.5, 0.047, 0.9, "ally", ALLY_P),        # half out of frame
        Detection(-0.2, 1.4, 0.047, 0.9, "enemy", ENEMY_P),     # outside [0, 1]
        Detection(0.5, 0.5, 0.001, 0.9, "enemy", ENEMY_P),      # tiny radius
        Detection(0.5, 0.5, 3.0, 0.9, "enemy", ENEMY_P),        # huge radius
        Detection(float("nan"), 0.5, 0.047, 0.9, "ally", ALLY_P),
        Detection(0.5, 0.5, 0.047, 0.9, "weird", (0.0, 0.0, 0.0)),
        Detection(0.99, 0.99, 0.047, 0.9, "ally", (float("nan"), 1.0, 0.0)),
    ]
    res = ident.identify(img, weird)
    assert len(res) == len(weird)
    assert all(o.relation in ("self", "ally", "enemy") for o in res)
    assert ident.identify(img, []) == []
    for bad in (None, np.zeros((4, 4, 3), np.uint8), np.zeros((300, 300), np.uint8),
                np.zeros((300, 300, 4), np.uint8), np.zeros((300, 300, 3), np.float32), "x"):
        out = ident.identify(bad, weird[:1])  # type: ignore[arg-type]
        assert len(out) == 1 and out[0].alias is None


class _CountingDB:
    """ChampionDB stand-in counting icon loads."""

    def __init__(self, db: ChampionDB) -> None:
        self.db = db
        self.loads = 0

    def load_icon(self, alias: str, skin_id: int = 0):
        self.loads += 1
        return None if alias == "Unknown" else self.db.load_icon(alias, skin_id)

    def cached_icon_path(self, alias: str, skin_id: int):
        return None


def test_set_roster_idempotent_and_missing_icons(db):
    cdb = _CountingDB(db)
    names = [e.alias for e in db.all()[:9]] + ["Unknown"]
    ident = ChampionIdentifier(cdb)
    game = _game(names)
    ident.set_roster(game)
    n = cdb.loads
    assert n >= 10
    ident.set_roster(_game(names))                 # same roster -> nothing recomputed
    assert cdb.loads == n
    assert len(ident.roster_aliases) == 9 and "Unknown" not in ident.roster_aliases
    ident.set_roster(None)
    assert ident.roster_aliases == []
    ident.set_roster(object())                     # garbage: no exception
    ident.set_roster(GameInfo())                   # empty game


def test_concurrent_set_roster_and_identify(db, renderer):
    rng = random.Random(10)
    aliases = [e.alias for e in db.all()]
    rosters = [_game(rng.sample(aliases, 10)) for _ in range(3)]
    img, gts = _scene(rng, renderer, db, [a.champion_alias for a in rosters[0].allies] * 3,
                      [1, 2, 3], 300)
    dets = [_det(rng, g) for g in gts]
    ident = ChampionIdentifier(db)
    errors: list[BaseException] = []

    def writer():
        try:
            for k in range(6):
                ident.set_roster(rosters[k % 3])
        except BaseException as exc:  # pragma: no cover
            errors.append(exc)

    def reader():
        try:
            for _ in range(30):
                assert len(ident.identify(img, dets)) == 3
        except BaseException as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=writer), threading.Thread(target=reader)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20)
    assert not errors
