"""Tests of the roster-driven portrait matcher (treeaicoach/roster_matcher.py) and of its
integration (HybridDetector, identifier alias trust, config persistence)."""

from __future__ import annotations

import math
import time
from pathlib import Path

import cv2
import numpy as np
import pytest

from treeaicoach import roster_matcher as RMod
from treeaicoach.detector import ClassicDetector, Detection, HybridDetector, create_detector
from treeaicoach.roster_matcher import RingColorModel, RosterEntry, RosterMatcher, ncc_maps

FIXTURE = Path(__file__).parent / "fixtures" / "real_minimap_306.png"
#: Ground truth of the real 306 px minimap (user's game at 1:48), checked by eye.
REAL_GT = {
    "Zaahen": ("self", 87.2, 23.1),
    "Kassadin": ("enemy", 181.4, 130.0),
    "Jinx": ("ally", 167.1, 141.2),
    "Ahri": ("ally", 195.2, 279.6),
    "Shen": ("enemy", 235.7, 275.1),
}
DISTRACTORS = [("Sivir", "ally"), ("Rell", "ally"), ("Swain", "enemy"), ("Xerath", "enemy"),
               ("Jayce", "enemy")]


@pytest.fixture(scope="module")
def db():
    from treeaicoach.champions import ChampionDB

    d = ChampionDB(cache_dir=Path("/nonexistent-treeaicoach-cache"))
    if len(d) < 20:
        pytest.skip("champion icons missing")
    return d


@pytest.fixture(scope="module")
def renderer():
    from treeaicoach.render import MinimapRenderer

    r = MinimapRenderer()
    if not r.textures():
        pytest.skip("minimap textures missing")
    return r


def _entries(db, pairs):
    out = []
    for alias, rel in pairs:
        icon = db.load_icon(alias)
        if icon is None:
            pytest.skip(f"icon {alias} missing")
        out.append(RosterEntry(alias, rel, icon))
    return out


def _game(db, rng, n_visible=8):
    aliases = [e.alias for e in db.all()]
    names = list(rng.choice(aliases, 10, replace=False))
    rels = ["self"] + ["ally"] * 4 + ["enemy"] * 5
    return list(zip(names, rels)), set(int(i) for i in rng.choice(10, n_visible, replace=False))


def _scene(renderer, db, roster, visible, rng, size, rad, degrade=False, glow=True):
    from treeaicoach.render import ChampionSprite, Scene

    champs, truth, pts = [], {}, []
    for k, (alias, rel) in enumerate(roster):
        if k not in visible:
            continue
        for _ in range(200):
            u, v = rng.uniform(0.07, 0.93, 2)
            if all(math.hypot(u - a, v - b) > 2.4 * rad for a, b in pts):
                break
        pts.append((u, v))
        champs.append(ChampionSprite(u=u, v=v, r=rad, relation=rel, icon=db.load_icon(alias),
                                     self_glow=0.8 if (rel == "self" and glow) else 0.0))
        truth[alias] = (rel, u, v)
    cam = (rng.uniform(0, 0.6), rng.uniform(0, 0.7))
    scene = Scene(texture=str(rng.choice(renderer.textures())), size=size, champions=champs,
                  camera=(cam[0], cam[1], cam[0] + 0.275, cam[1] + 0.155),
                  texts=[(0.3, 0.3, "10:29"), (0.6, 0.55, "1:24"), (0.45, 0.8, "3:20")],
                  vision=[(u, v, 0.15) for u, v in pts])
    img = renderer.render(scene)
    if degrade:
        img = cv2.GaussianBlur(img, (0, 0), 0.7)
        img = cv2.imdecode(cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 45])[1], 1)
    return img, truth


def _score(dets, truth, rad):
    tp = fp = 0
    for d in dets:
        t = truth.get(d.alias)
        if t is not None and math.hypot(d.u - t[1], d.v - t[2]) < 0.5 * rad:
            tp += 1
            assert d.cls == ("enemy" if t[0] == "enemy" else "ally")
        else:
            fp += 1
    return tp, fp


# ======================================================================================
# NCC core
# ======================================================================================


def test_ncc_maps_peak_at_template_position(db):
    m = RosterMatcher(db=db)
    m.set_entries(_entries(db, [("Ahri", "ally"), ("Garen", "enemy")]))
    bank = m._bank(15.0)
    rng = np.random.default_rng(0)
    img = rng.integers(0, 255, (80, 90, 3), dtype=np.uint8)
    img = cv2.GaussianBlur(img, (0, 0), 2)
    # paste the Ahri template content (rebuilt from the icon at the same geometry)
    icon = RMod._icon_bgr(db.load_icon("Ahri"))
    side = int(round(2.0 * RMod.PORTRAIT_RATIO * (7.5 / RMod.INNER_RATIO) / RMod.PORTRAIT_FILL))
    patch = cv2.resize(icon, (side, side), interpolation=cv2.INTER_AREA)
    y0, x0 = 30, 40
    img[y0:y0 + side, x0:x0 + side] = patch
    feat = RMod._features(img)
    maps, std = ncc_maps(feat, bank)
    assert maps.shape[0] == 2 and std.shape == maps.shape[1:]
    y, x = np.unravel_index(int(np.argmax(maps[0])), maps[0].shape)
    cx, cy = x + (bank.size - 1) / 2.0, y + (bank.size - 1) / 2.0
    assert abs(cx - (x0 + side / 2.0 - 0.5)) <= 1.5 and abs(cy - (y0 + side / 2.0 - 0.5)) <= 1.5
    assert maps[0].max() > 0.85 and maps[0].max() > maps[1].max() + 0.2
    assert np.all(np.isfinite(maps)) and maps.max() <= 1.0001


# ======================================================================================
# Real minimap (user's screenshot)
# ======================================================================================


def test_real_minimap_all_visible_champions_found(db):
    img = cv2.imread(str(FIXTURE))
    assert img is not None
    pairs = [(a, r) for a, (r, _, _) in REAL_GT.items()] + DISTRACTORS
    m = RosterMatcher(db=db)
    m.set_entries(_entries(db, pairs))
    for _ in range(4):
        dets = m.detect(img)
    W = img.shape[1]
    assert m.scale is not None and 0.086 <= m.scale <= 0.100
    found = {d.alias: d for d in dets}
    assert set(found) == set(REAL_GT), sorted(found)
    for alias, (rel, x, y) in REAL_GT.items():
        d = found[alias]
        assert math.hypot(d.u * W - x, d.v * W - y) < 3.0
        assert d.cls == ("enemy" if rel == "enemy" else "ally")
        assert d.cls_probs[0] + d.cls_probs[1] + d.cls_probs[2] == pytest.approx(1.0)
    # ring colours were learned from the confident matches
    rc = m.ring_colors
    assert "enemy" in rc and "ally" in rc
    b, g, r = rc["enemy"]
    assert r > b + 30
    b, g, r = rc["ally"]
    assert b > r + 20


def test_real_minimap_jpeg_degraded(db):
    img = cv2.imread(str(FIXTURE))
    img = cv2.imdecode(cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 40])[1], 1)
    pairs = [(a, r) for a, (r, _, _) in REAL_GT.items()] + DISTRACTORS
    m = RosterMatcher(db=db)
    m.set_entries(_entries(db, pairs))
    for _ in range(4):
        dets = m.detect(img)
    W = img.shape[1]
    ok = sum(1 for d in dets if d.alias in REAL_GT
             and math.hypot(d.u * W - REAL_GT[d.alias][1], d.v * W - REAL_GT[d.alias][2]) < 4)
    assert ok >= 4 and len(dets) - ok <= 1


# ======================================================================================
# Rendered minimaps with known rosters
# ======================================================================================


@pytest.mark.parametrize("degrade", [False, True])
def test_rendered_games_precision_recall(db, renderer, degrade):
    rng = np.random.default_rng(7 if degrade else 3)
    TP = FP = N = 0
    scale_err = []
    times = []
    for _ in range(4):
        roster, visible = _game(db, rng, n_visible=int(rng.integers(6, 10)))
        size = int(rng.choice([240, 280, 316]))
        rad = float(rng.uniform(0.044, 0.05))
        m = RosterMatcher(db=db)
        m.set_entries(_entries(db, roster))
        for f in range(5):
            img, truth = _scene(renderer, db, roster, visible, rng, size, rad, degrade)
            t0 = time.perf_counter()
            dets = m.detect(img)
            if f >= 3:
                times.append(time.perf_counter() - t0)
            tp, fp = _score(dets, truth, rad)
            TP, FP, N = TP + tp, FP + fp, N + len(truth)
        scale_err.append(abs(m.scale / (2 * rad) - 1))
    precision, recall = TP / max(1, TP + FP), TP / max(1, N)
    print(f"roster matcher degrade={degrade}: P={precision:.3f} R={recall:.3f} "
          f"scale err {100 * np.median(scale_err):.1f}% {1000 * np.median(times):.1f} ms")
    assert precision >= 0.95
    assert recall >= (0.8 if degrade else 0.9)
    assert np.median(scale_err) < 0.08
    assert np.median(times) < 0.08          # generous: CI machines are slow


# ======================================================================================
# Calibration, persistence, robustness
# ======================================================================================


def test_scale_store_and_callback(db, renderer):
    rng = np.random.default_rng(11)
    roster, visible = _game(db, rng, n_visible=8)
    img, truth = _scene(renderer, db, roster, visible, rng, 280, 0.047)
    calls = []
    store: dict = {}
    m = RosterMatcher(db=db, scale_store=store, on_scale=lambda k, v: calls.append((k, v)))
    m.set_entries(_entries(db, roster))
    m.detect(img)
    assert "280x280" in store and calls and calls[-1][0] == "280x280"
    assert abs(store["280x280"] / 0.094 - 1) < 0.1
    # the stored ratio is the prior of a new matcher before calibration
    m2 = RosterMatcher(db=db, scale_store={"280x280": 0.0931})
    m2.set_entries(_entries(db, roster))
    assert m2._stored_scale(img) == pytest.approx(0.0931)
    # explicit calibration API
    assert abs(m2.calibrate(img, store=False) / 0.094 - 1) < 0.1


def test_never_raises_and_empty_roster(db):
    m = RosterMatcher(db=db)
    img = np.zeros((200, 200, 3), np.uint8)
    assert m.detect(img) == []                       # no roster
    m.set_entries(_entries(db, [("Ahri", "ally"), ("Garen", "enemy")]))
    for bad in (None, "x", np.zeros((5, 5, 3), np.uint8), np.zeros((300, 300), np.float32),
                np.full((256, 256, 3), 255, np.uint8)):
        assert isinstance(m.detect(bad), list)        # type: ignore[arg-type]
    noise = np.random.default_rng(0).integers(0, 256, (256, 256, 3), dtype=np.uint8)
    assert m.detect(noise) == [] or all(d.alias in ("Ahri", "Garen") for d in m.detect(noise))
    assert m.calibrate(None) is None                  # type: ignore[arg-type]
    m.set_entries([RosterEntry("Bad", "ally", np.zeros((3, 3, 4), np.uint8))])
    assert not m.has_roster


def test_set_roster_from_game_and_skin_refresh(db, tmp_path):
    from treeaicoach.live_client import GameInfo, PlayerInfo

    class FakeDB:
        def __init__(self):
            self.cached: set = set()

        def load_icon(self, alias, skin=0):
            return db.load_icon(alias, 0)

        def cached_icon_path(self, alias, skin):
            p = tmp_path / f"{alias}_{skin}.png"
            if (alias, skin) in self.cached:
                p.write_bytes(b"x")
            return p

    fdb = FakeDB()
    me = PlayerInfo(champion_alias="Ahri", team="ORDER", skin_id=3)
    game = GameInfo(me=me, allies=[PlayerInfo(champion_alias="Garen", team="ORDER")],
                    enemies=[PlayerInfo(champion_alias="Darius", team="CHAOS"),
                             PlayerInfo(champion_alias="NoSuchChampion", team="CHAOS")])
    m = RosterMatcher(db=fdb)
    m.set_roster(game)
    assert m.aliases == ["Ahri", "Garen", "Darius"]
    assert [e.relation for e in m.entries] == ["self", "ally", "enemy"]
    assert not m.entries[0].exact                     # skin portrait not downloaded yet
    fdb.cached.add(("Ahri", 3))
    m._refresh_skins()
    assert m.entries[0].exact
    m.set_roster(None)
    assert not m.has_roster


def test_ring_color_model_learns_colourblind_colours():
    rm = RingColorModel()
    magenta = cv2.cvtColor(np.full((1, 60, 3), (203, 79, 255), np.uint8),
                           cv2.COLOR_BGR2LAB).reshape(-1, 3).astype(np.float32)
    for _ in range(5):
        assert rm.learn("enemy", magenta + np.random.default_rng(0).normal(0, 2, magenta.shape)
                        .astype(np.float32))
    assert "enemy" in rm.learned()
    fe, fa = rm.classify(magenta)
    assert fe > 0.8 and fa < 0.1
    # an ally ring never learns the enemy colour
    rm = RingColorModel()
    red = cv2.cvtColor(np.full((1, 60, 3), (51, 51, 200), np.uint8),
                       cv2.COLOR_BGR2LAB).reshape(-1, 3).astype(np.float32)
    assert not rm.learn("ally", red)


# ======================================================================================
# Integration: HybridDetector, classic calibration, identifier, config
# ======================================================================================


def test_hybrid_detector_uses_roster_and_fallback(db):
    from treeaicoach.live_client import GameInfo, PlayerInfo

    img = cv2.imread(str(FIXTURE))
    det = create_detector("classic", db=db)
    assert isinstance(det, HybridDetector) and det.name == "classic"
    plain = det.detect(img)
    assert plain and all(d.alias is None for d in plain)
    pl = lambda a, t: PlayerInfo(champion_alias=a, team=t)  # noqa: E731
    game = GameInfo(me=pl("Zaahen", "ORDER"),
                    allies=[pl("Jinx", "ORDER"), pl("Ahri", "ORDER"), pl("Sivir", "ORDER"),
                            pl("Rell", "ORDER")],
                    enemies=[pl("Kassadin", "CHAOS"), pl("Shen", "CHAOS"), pl("Swain", "CHAOS"),
                             pl("Xerath", "CHAOS"), pl("Jayce", "CHAOS")])
    det.set_roster(game)
    assert det.name == "roster+classic"
    for _ in range(4):
        dets = det.detect(img)
    named = {d.alias for d in dets if d.alias}
    assert named == set(REAL_GT)
    # extras (alias None) never sit on a roster match
    for d in dets:
        if d.alias is None:
            assert all(math.hypot(d.u - k.u, d.v - k.v) > 0.8 * (d.r + k.r)
                       for k in dets if k.alias)
    # the classic fallback received the live calibration
    assert det.fallback._scale is not None and det.fallback._learned
    det.set_roster(None)
    assert det.name == "classic"


def test_classic_learned_colours_detect_colourblind_rings(db, renderer):
    from treeaicoach.render import ChampionSprite, Scene

    icon = db.load_icon("Ahri")
    sc = Scene(texture=renderer.textures()[0], size=256, champions=[
        ChampionSprite(u=0.4, v=0.5, r=0.047, relation="enemy", icon=icon,
                       ring_bgr=(60, 200, 60)),            # unusual green ring
    ], vision=[(0.4, 0.5, 0.2)])
    img = renderer.render(sc)
    det = ClassicDetector()
    before = [d for d in det.detect(img) if abs(d.u - 0.4) < 0.03 and d.cls == "enemy"]
    det.set_ring_colors({"enemy": (60, 200, 60)})
    det.set_scale(0.094)
    after = [d for d in det.detect(img) if abs(d.u - 0.4) < 0.03 and abs(d.v - 0.5) < 0.03]
    assert not before
    assert after and after[0].cls == "enemy"
    det.set_ring_colors(None)
    det.set_scale("bad")                                   # type: ignore[arg-type]
    assert det._scale is None and not det._learned


def test_classic_detects_teal_self_outline_as_ally(db, renderer):
    from treeaicoach.render import ChampionSprite, Scene

    sc = Scene(texture=renderer.textures()[0], size=280, champions=[
        ChampionSprite(u=0.3, v=0.12, r=0.047, relation="self", icon=db.load_icon("Garen"),
                       self_glow=1.0)], camera=(0.15, 0.03, 0.43, 0.19),
        vision=[(0.3, 0.12, 0.2)])
    img = renderer.render(sc)
    dets = [d for d in ClassicDetector().detect(img)
            if math.hypot(d.u - 0.3, d.v - 0.12) < 0.03]
    assert dets and dets[0].cls == "ally"


def test_identifier_trusts_detector_alias(db):
    from treeaicoach.identifier import ChampionIdentifier
    from treeaicoach.live_client import GameInfo, PlayerInfo

    ident = ChampionIdentifier(db)
    game = GameInfo(me=PlayerInfo(champion_alias="Ahri", team="ORDER"),
                    allies=[PlayerInfo(champion_alias="Garen", team="ORDER")],
                    enemies=[PlayerInfo(champion_alias="Darius", team="CHAOS")])
    ident.set_roster(game)
    img = np.zeros((200, 200, 3), np.uint8)       # nothing to recognise visually
    dets = [Detection(0.2, 0.2, 0.047, 0.9, "ally", (0.03, 0.97, 0.0), alias="Ahri"),
            Detection(0.6, 0.6, 0.047, 0.8, "enemy", (0.97, 0.03, 0.0), alias="Darius"),
            Detection(0.8, 0.3, 0.047, 0.7, "enemy", (0.9, 0.1, 0.0))]
    out = ident.identify(img, dets)
    assert (out[0].alias, out[0].relation, out[0].team) == ("Ahri", "self", "ORDER")
    assert (out[1].alias, out[1].relation, out[1].team) == ("Darius", "enemy", "CHAOS")
    assert out[2].alias is None and out[2].relation == "enemy"
    assert out[0].id_score == pytest.approx(0.9)


def test_detection_alias_is_optional():
    d = Detection(0.5, 0.5, 0.05, 0.9, "ally", (0.1, 0.9, 0.0))
    assert d.alias is None


def test_config_icon_scale_by_res():
    from treeaicoach.config import Config

    c = Config.from_dict({"icon_scale_by_res": {"316x316": 0.094, "bad": 0.09, "300x300": 7,
                                                "200x200": True}})
    assert c.icon_scale_by_res == {"316x316": 0.094}
    assert Config.from_dict({"icon_scale_by_res": "nope"}).icon_scale_by_res == {}
    assert Config().icon_scale_by_res == {}
