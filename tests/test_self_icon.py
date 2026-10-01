"""Tests of the learned minimap icons (treeaicoach/self_icon.py): who is who even with a
custom skin mod, and of their integration in the roster matcher / roles / engine."""

from __future__ import annotations

import math
import time
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from treeaicoach import self_icon as SI
from treeaicoach.roster_matcher import RingColorModel, RosterEntry, RosterMatcher

FIXTURE = Path(__file__).parent / "fixtures" / "real_minimap_306.png"
REAL_GT = {"Zaahen": ("self", 87.2, 23.1), "Kassadin": ("enemy", 181.4, 130.0),
           "Jinx": ("ally", 167.1, 141.2), "Ahri": ("ally", 195.2, 279.6),
           "Shen": ("enemy", 235.7, 275.1)}
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


RELS = ["self"] + ["ally"] * 4 + ["enemy"] * 5


class _Game:
    """Moving champions on a rendered minimap; ``modded`` entries are drawn with an unrelated
    portrait (custom skin); the camera follows me when ``locked``."""

    def __init__(self, db, renderer, seed, modded=(0,), locked=True, size=300, rad=0.045,
                 my_icon=None):
        rng = np.random.default_rng(seed)
        self.my_icon = my_icon
        names = list(rng.choice([e.alias for e in db.all()], 10 + len(modded), replace=False))
        self.db, self.r, self.rad, self.size, self.locked = db, renderer, rad, size, locked
        self.roster = names[:10]
        self.skin = {k: names[10 + j] for j, k in enumerate(modded)}
        self.pos = rng.uniform(0.12, 0.88, (10, 2))
        self.vel = rng.normal(0, 0.02, (10, 2))
        self.tex = str(rng.choice(renderer.textures()))

    def entries(self):
        return [RosterEntry(a, r, self.db.load_icon(a)) for a, r in zip(self.roster, RELS)]

    def frame(self, t, dt=0.2):
        from treeaicoach.render import ChampionSprite, Scene

        self.pos += self.vel * dt
        for k in range(10):
            for d in range(2):
                if not 0.1 < self.pos[k, d] < 0.9:
                    self.vel[k, d] *= -1
                    self.pos[k, d] = min(0.9, max(0.1, self.pos[k, d]))
        champs = [ChampionSprite(u=self.pos[k, 0], v=self.pos[k, 1], r=self.rad, relation=RELS[k],
                                 icon=self.my_icon if k == 0 and self.my_icon is not None
                                 else self.db.load_icon(self.skin.get(k, self.roster[k])),
                                 self_glow=0.8 if k == 0 else 0.0) for k in range(10)]
        if self.locked:
            cu, cv_ = self.pos[0, 0], self.pos[0, 1] - 0.14 * 0.155
        else:
            cu, cv_ = 0.5 + 0.3 * math.sin(t / 3), 0.5 + 0.3 * math.cos(t / 4)
        return self.r.render(Scene(texture=self.tex, size=self.size, champions=champs,
                                   camera=(cu - 0.1375, cv_ - 0.0775, cu + 0.1375, cv_ + 0.0775),
                                   texts=[(0.3, 0.3, "10:29")],
                                   vision=[(u, v, 0.15) for u, v in self.pos]))

    def isolated(self, k):
        return min(math.hypot(*(self.pos[j] - self.pos[k])) for j in range(10) if j != k) \
            > 2.2 * self.rad

    def at(self, dets, k):
        d = [x for x in dets if x.alias == self.roster[k]]
        return bool(d) and math.hypot(d[0].u - self.pos[k, 0], d[0].v - self.pos[k, 1]) \
            < 0.5 * self.rad


def _run(game, m, n, k=0, dt=0.2):
    ok = tot = 0
    for f in range(n):
        dets = m.detect(game.frame(f * dt, dt), t=f * dt)
        if f >= 3 and game.isolated(k):
            tot += 1
            ok += game.at(dets, k)
    return ok, tot


# ======================================================================================
# Ring candidates
# ======================================================================================


def test_ring_candidates_find_rendered_icons(db, renderer):
    g = _Game(db, renderer, seed=3, modded=())
    img = g.frame(0.0)
    structs = SI._structure_points()
    cands = SI.ring_candidates(img, g.rad * g.size, RingColorModel(), structs)
    found = n = 0
    for k in range(10):
        u, v = g.pos[k]
        if not g.isolated(k) or any(math.hypot(u - a, v - b) < 0.04 for a, b in structs) or \
                any(math.hypot(u - a, v - b) < 0.1 for a, b in SI._FOUNTAINS):
            continue                     # (round structure glyphs / fountains are excluded)
        side = "enemy" if RELS[k] == "enemy" else "ally"
        n += 1
        found += any(c.side == side and math.hypot(c.u - u, c.v - v) < 0.3 * g.rad
                     for c in cands)
    assert n >= 5 and found >= n - 1, (found, n)            # (one is under the timer text)
    me = [c for c in cands if math.hypot(c.u - g.pos[0, 0], c.v - g.pos[0, 1]) < 0.3 * g.rad]
    assert me and me[0].frac_self > 0.3                     # my teal outline
    # mostly icons (the tracking and the elimination deal with the few others)
    icons = sum(1 for c in cands if min(math.hypot(c.u - u, c.v - v) for u, v in g.pos)
                < 1.2 * g.rad)
    assert icons >= 0.7 * len(cands)
    t0 = time.perf_counter()
    for _ in range(10):
        SI.ring_candidates(img, g.rad * g.size, RingColorModel(), SI._structure_points())
    assert (time.perf_counter() - t0) / 10 < 0.012


def test_crop_align_median_roundtrip(db):
    icon = cv2.resize(db.load_icon("Ahri")[:, :, :3], (60, 60))
    img = np.full((120, 120, 3), 40, np.uint8)
    img[30:90, 30:90] = icon
    R = 30 / SI.CROP_HALF
    a = SI.crop_icon(img, 60.0, 60.0, R)
    b = SI.crop_icon(img, 61.3, 59.2, R)           # off by ~1.5 px
    al, s = SI.align_to(b, a)
    assert s > 0.9 and SI.icon_ncc(al, a) > SI.icon_ncc(b, a)
    med, consist = SI.median_icon([a, b, al])
    assert med.shape == (SI.LEARN_PX, SI.LEARN_PX, 3) and consist > 0.85


# ======================================================================================
# Custom skin of the local player
# ======================================================================================


@pytest.mark.parametrize("seed,locked", [(1, True), (3, False), (2, True)])
def test_custom_skin_self_is_tracked(db, renderer, seed, locked):
    """My minimap icon is an unrelated portrait: bootstrapped at once (camera / teal outline),
    learned within a few seconds, then matched like any champion."""
    g = _Game(db, renderer, seed=seed, locked=locked)
    m = RosterMatcher(db=db)
    m.set_entries(g.entries())
    ok, tot = _run(g, m, 60)
    assert tot >= 30 and ok >= 0.9 * tot, (ok, tot)
    assert m.learned_aliases() == {g.roster[0]: "learned"}
    e = m.entries[0]
    assert e.learned and e.icon.shape[:2] == (SI.LEARN_PX, SI.LEARN_PX)
    # learned: my position comes from the template match, not from the bootstrap
    dets = m.detect(g.frame(12.2), t=12.2)
    assert g.at(dets, 0)
    assert any(x.alias == g.roster[0] and x.accepted and x.reason in ("", "occluded")
               for x in m.last_matches)
    # cost: the learner is cheap once my icon is learned
    lms = []
    for f in range(10):
        m.detect(g.frame(12.4 + 0.2 * f), t=12.4 + 0.2 * f)
        lms.append(m.learner.last_ms)
    assert float(np.median(lms)) < 3.0


def _stick_figure() -> np.ndarray:
    """A hand-drawn custom portrait: black stick figure on light cyan (like the user's mod)."""
    im = np.zeros((64, 64, 4), np.uint8)
    cv2.circle(im, (32, 32), 31, (150, 225, 230, 255), -1)          # RGBA
    k = (20, 20, 20, 255)
    cv2.circle(im, (32, 18), 8, k, 2)
    for a, b in (((32, 26), (32, 44)), ((32, 32), (20, 26)), ((32, 32), (44, 26)),
                 ((32, 44), (22, 58)), ((32, 44), (42, 58))):
        cv2.line(im, a, b, k, 2)
    return im


@pytest.mark.parametrize("seed,locked", [(3, False), (1, True)])
def test_custom_cyan_portrait_self_is_tracked(db, renderer, seed, locked):
    """A cyan custom portrait inside my teal outline (no colour contrast with the ring)."""
    g = _Game(db, renderer, seed=seed, locked=locked, my_icon=_stick_figure())
    m = RosterMatcher(db=db)
    m.set_entries(g.entries())
    ok, tot = _run(g, m, 60)
    assert tot >= 30 and ok >= 0.85 * tot, (ok, tot)
    assert m.learned_aliases() == {g.roster[0]: "learned"}


def test_official_skins_learn_nothing(db, renderer):
    g = _Game(db, renderer, seed=4, modded=())
    m = RosterMatcher(db=db)
    m.set_entries(g.entries())
    ok, tot = _run(g, m, 50)
    assert ok >= 0.9 * tot
    assert m.learned_aliases() == {} and m.learner.events == []


def test_dead_me_is_not_bootstrapped(db, renderer):
    g = _Game(db, renderer, seed=1)
    m = RosterMatcher(db=db)
    m.set_entries(g.entries())
    m.learner.set_status((), me_dead=True)
    for f in range(20):
        dets = m.detect(g.frame(f * 0.2), t=f * 0.2)
    assert not any(d.alias == g.roster[0] for d in dets)
    assert m.learned_aliases() == {}


def test_real_minimap_custom_skin_self(db):
    """Real 306 px minimap: my portrait replaced by an unrelated one (custom skin): my icon is
    found by the camera rectangle + the ring, learned, then matched."""
    img = cv2.imread(str(FIXTURE))
    assert img is not None
    ents = [RosterEntry(a, r, db.load_icon("Garen" if r == "self" else a))
            for a, (r, _, _) in REAL_GT.items()]
    ents += [RosterEntry(a, r, db.load_icon(a)) for a, r in DISTRACTORS]
    m = RosterMatcher(db=db)
    m.set_entries(ents)
    first = None
    for f in range(20):
        dets = m.detect(img, t=0.2 * f)
        me = [d for d in dets if d.alias == "Zaahen"]
        if me and math.hypot(me[0].u * 306 - 87.2, me[0].v * 306 - 23.1) < 5.0 and first is None:
            first = f
    assert first is not None and first <= 6
    assert m.learned_aliases() == {"Zaahen": "learned"}
    found = {d.alias for d in dets}
    assert set(REAL_GT) <= found


# ======================================================================================
# Persistence / unlearning
# ======================================================================================


def test_learned_icon_persisted_and_reused(db, renderer, tmp_path):
    g = _Game(db, renderer, seed=1)
    m = RosterMatcher(db=db, learn_cache=tmp_path)
    m.set_entries(g.entries())
    _run(g, m, 30)
    files = list(tmp_path.glob("*.png"))
    assert [f.name for f in files] == [f"{g.roster[0]}_0.png"]
    # next game, same champion + skin: my icon is known from the first frames
    game = SimpleNamespace(me=SimpleNamespace(champion_alias=g.roster[0], skin_id=0, team="ORDER"),
                           allies=[SimpleNamespace(champion_alias=a, skin_id=0, team="ORDER")
                                   for a in g.roster[1:5]],
                           enemies=[SimpleNamespace(champion_alias=a, skin_id=0, team="CHAOS")
                                    for a in g.roster[5:]])
    m2 = RosterMatcher(db=db, learn_cache=tmp_path)
    m2.set_roster(game)
    assert m2.learned_aliases() == {g.roster[0]: "cached"} and m2.entries[0].learned
    ok = 0
    for f in range(6):
        dets = m2.detect(g.frame(10 + 0.2 * f), t=0.2 * f)
        ok += g.at(dets, 0) and any(x.alias == g.roster[0] and x.reason == ""
                                    for x in m2.last_matches)
    assert ok >= 4


def test_wrong_cached_icon_is_unlearned_and_relearned(db, renderer, tmp_path):
    g = _Game(db, renderer, seed=2)
    wrong = cv2.resize(db.load_icon("Teemo")[:, :, :3], (SI.LEARN_PX, SI.LEARN_PX))
    cv2.imwrite(str(tmp_path / f"{g.roster[0]}_0.png"), wrong)
    game = SimpleNamespace(me=SimpleNamespace(champion_alias=g.roster[0], skin_id=0, team="ORDER"),
                           allies=[SimpleNamespace(champion_alias=a, skin_id=0, team="ORDER")
                                   for a in g.roster[1:5]],
                           enemies=[SimpleNamespace(champion_alias=a, skin_id=0, team="CHAOS")
                                    for a in g.roster[5:]])
    m = RosterMatcher(db=db, learn_cache=tmp_path)
    m.set_roster(game)
    assert m.learned_aliases() == {g.roster[0]: "cached"}
    ok, tot = _run(g, m, 50)
    assert m.learned_aliases() == {g.roster[0]: "learned"}      # replaced by the real icon
    assert ok >= 0.85 * tot


def test_learned_icon_reverts_when_never_matched():
    lr = SI.IconLearner()
    ent = [SimpleNamespace(alias="A", relation="self", skin_id=0),
           SimpleNamespace(alias="B", relation="ally", skin_id=0)]
    lr.adopt(0, "A", np.zeros((48, 48, 3), np.uint8), 0.0, "cached")
    img = np.zeros((200, 200, 3), np.uint8)
    out = lr.step(img, 1.0, 9.0, ent, {}, None, None)
    assert out.revert == []
    out = lr.step(img, 1.0 + SI.REVERT_S + 1.0, 9.0, ent, {}, None, None)
    assert out.revert == [0] and 0 not in lr.learned


# ======================================================================================
# Other champions (custom skins too): elimination
# ======================================================================================


def test_custom_skin_ally_learned_by_elimination(db, renderer):
    g = _Game(db, renderer, seed=5, modded=(1,))
    m = RosterMatcher(db=db)
    m.set_entries(g.entries())
    first = None
    for f in range(150):
        dets = m.detect(g.frame(f * 0.2), t=f * 0.2)
        if first is None and g.at(dets, 1):
            first = f
    assert m.learned_aliases() == {g.roster[1]: "learned"}
    assert first is not None and first < 100
    assert g.at(dets, 1) or not g.isolated(1)
    assert g.at(dets, 0) or not g.isolated(0)


# ======================================================================================
# Lane / roles / skin guess / engine
# ======================================================================================


def test_observed_lane_and_roles_hook():
    from treeaicoach.roles import RoleResolver

    lr = SI.IconLearner()
    t = 0.0
    for k in range(200):                         # me in the top lane from 1:40 for 50 s
        t = 0.25 * k
        lr._self_seen = (0.08, 0.3, t)
        lr.observe_lane(t, 100.0 + t)
    assert lr.observed_lane() == "top"
    res = RoleResolver()
    res.my_lane_hook = lr.observed_lane
    me = SimpleNamespace(champion_alias="Ahri", team="ORDER", position="MIDDLE", has_smite=False,
                         spells=())
    allies = [SimpleNamespace(champion_alias=a, team="ORDER", position=p, has_smite=p == "JUNGLE",
                              spells=()) for a, p in (("Garen", "TOP"), ("LeeSin", "JUNGLE"),
                                                      ("Jinx", "BOTTOM"), ("Thresh", "UTILITY"))]
    game = SimpleNamespace(me=me, allies=allies, enemies=[], game_time=200.0)
    res.update(1.0, None, game)
    assert res.my_role() == "TOP" and res.me().source == "observed"


def test_skin_guesser_picks_official_skin(db, tmp_path):
    from treeaicoach.champions import ChampionDB

    d = ChampionDB(cache_dir=tmp_path)
    # "skins" 3 and 7 of Ahri: other portraits stored as Ahri skin icons in the cache
    for n, src in ((3, "Lux"), (7, "Garen")):
        icon = db.load_icon(src)
        cv2.imwrite(str(tmp_path / f"Ahri_{n}.png"), cv2.cvtColor(icon, cv2.COLOR_RGBA2BGRA))
    g = SI.SkinGuesser(d, allow_network=False, skin_list_fn=lambda a: [3, 7, 9])
    g.min_period_s = 0.0
    lux = db.load_icon("Lux")
    hud = cv2.resize(cv2.cvtColor(lux, cv2.COLOR_RGBA2BGR), (64, 64))
    hud = cv2.GaussianBlur(hud, (0, 0), 0.8)
    assert g.guess("Ahri", hud, now=0.0) is None              # needs two looks
    assert g.guess("Ahri", hud, now=1.0) == 3
    noise = np.random.default_rng(0).integers(0, 255, (64, 64, 3), dtype=np.uint8)
    g2 = SI.SkinGuesser(d, allow_network=False, skin_list_fn=lambda a: [3, 7])
    g2.min_period_s = 0.0
    g2.guess("Ahri", noise, now=0.0)
    assert g2.guess("Ahri", noise, now=1.0) is None           # custom portrait: no guess


def test_learner_never_raises():
    lr = SI.IconLearner()
    out = lr.step(np.zeros((5, 5, 3), np.uint8), 0.0, 1.0, [], {}, None, None)
    assert out.register == {} and out.self_pos is None
    lr.feed_hud(None, None)
    lr.set_status(["x"], None, float("nan"))
    lr.observe_lane(0.0, None)
    assert lr.observed_lane() is None and lr.self_position(0.0) is None
    assert lr.load_cached("Ahri", 0) is None


def test_engine_hooks(db):
    from treeaicoach.config import Config
    from treeaicoach.engine import CoachEngine

    eng = CoachEngine(Config(), SimpleNamespace(say=lambda *a, **k: None),
                      frame_source=SimpleNamespace(next=lambda t: (None, None)))
    assert eng.my_observed_lane() is None
    eng._self_icon_tick(1.0, 100.0, None)                        # no detector yet: no-op
    m = RosterMatcher(db=db)
    eng._detector = SimpleNamespace(matcher=m)
    m.learner._lane_obs = SimpleNamespace(lane="mid")
    assert eng.my_observed_lane() == "mid"
    eng._self_icon_tick(2.0, 120.0, SimpleNamespace(me=SimpleNamespace(is_dead=True),
                                                   allies=[], enemies=[]))
    assert m.learner._me_dead is True and m.learner._gt == 120.0
