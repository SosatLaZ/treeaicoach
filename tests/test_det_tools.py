"""Detection-gym method tools: history / comparison / regression guard, failure gallery,
tuning overrides, micro-gyms, the diagnostic-bundle converter (fast checks)."""

from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

pytest.importorskip("cv2")


def _entry(rec=0.9, prec=0.95, g_live=10, real_rec=0.87):
    tot = {"rec": rec, "prec": prec, "idsw": 3, "frag": 50, "id_wrong": 10, "team": 5, "err95": 0.008,
           "lag": 0.1, "g_live": g_live, "g_dead": 0, "me95": 0.02, "me_bad": 0.01, "ms": 30.0, "cpu": 30.0,
           "n_vis": 1000}
    real = {"rec": real_rec, "prec": 1.0, "team": 1.0, "id": 1.0}
    import det_gym as G

    return {"suite": "main", "quick": False, "only": None, "total": tot, "real": real,
            "score": G.score(tot, real), "rev": "x", "time": "t"}


def test_regression_guard_and_compare():
    import det_gym as G

    a, b = _entry(), _entry(rec=0.88, g_live=30, real_rec=0.85)
    bad = G.regressions(b, a)
    assert any(s.startswith("rec") for s in bad) and any(s.startswith("g_live") for s in bad)
    assert any(s.startswith("real.rec") for s in bad)
    assert G.regressions(a, a) == []
    # real metrics compare only runs scored on the same real icons (45 -> 50 labelled icons)
    c, d = _entry(real_rec=0.8889), _entry(real_rec=0.88)
    c["real"]["gt"], d["real"]["gt"] = 45, 50
    assert not any(s.startswith("real.") for s in G.regressions(d, c))
    d["real"]["gt"] = 45
    assert any(s.startswith("real.rec") for s in G.regressions(d, c))
    assert G.score(a["total"]) > G.score(b["total"])
    txt = G.compare(b, [a])
    assert "REGRESSION" in txt and "score" in txt


def test_committed_history_does_not_regress():
    """The last recorded full main-suite run is not worse than the best one beyond tolerance
    (tools/det_gym_history.jsonl, appended by every det_gym.py run)."""
    import det_gym as G

    # only runs under the exe's pinned numpy / OpenCV / onnxruntime (the reference: OpenCV 5
    # draws other label glyphs, a run recorded in a venv once looked 3 recall points better)
    runs = [h for h in G.load_history() if h.get("suite") == "main" and not h.get("quick")
            and not h.get("only") and h.get("real") and not h.get("stale")
            and G.entry_deps(h) == G.REF_DEPS]
    if len(runs) < 2:
        pytest.skip("no comparable history")
    best = max(runs, key=lambda h: h.get("quality", h["score"]))
    assert G.regressions(runs[-1], best) == [], G.compare(runs[-1], runs[:-1])


def test_history_compares_only_same_library_versions():
    import det_gym as G

    e = G.history_entry({"total": _entry()["total"], "games": {}, "suite": "main"})
    assert e["deps"] == G.deps_versions() and set(e["deps"]) == {"numpy", "opencv", "onnxruntime"}
    a, b = _entry(), _entry(rec=0.8)
    assert G.comparable(a, b)                                   # no "deps": the reference
    b["deps"] = dict(G.REF_DEPS)
    assert G.comparable(a, b)
    b["deps"]["opencv"] = "5.0.0"
    assert not G.comparable(a, b) and "no previous run" in G.compare(b, [a])
    b["deps"] = dict(G.REF_DEPS)
    a["stale"] = "putText of OpenCV 5"
    assert not G.comparable(a, b)
    # the venv run of 749c563 (OpenCV 5 labels) is tagged and out of the guard
    hist = G.load_history()
    tagged = [h for h in hist if h.get("rev") == "749c563"]
    assert tagged and all(h.get("stale") and G.entry_deps(h)["opencv"] == "5.0.0" for h in tagged)


def test_gym_label_text_is_opencv4_hershey():
    """The gym's overlay labels (training.real_art._text) are the exe's OpenCV-4 Hershey glyphs
    under every OpenCV version (OpenCV 5's putText draws thin, outline-free TrueType text)."""
    import cv2
    import numpy as np

    from treeaicoach import hershey

    img = np.full((40, 160, 3), 90, np.uint8)
    hershey.put_text(img, "ADC 12 s", (5, 25), 0.4, (25, 25, 25), 3, cv2.LINE_AA)
    hershey.put_text(img, "ADC 12 s", (5, 25), 0.4, (230, 230, 230), 1, cv2.LINE_AA)
    import hashlib

    assert hashlib.md5(img.tobytes()).hexdigest() == "14bd6559a8564ec250b58d93a10524a9"
    assert hershey.get_text_size("ADC 12 s", 0.4, 1) == ((61, 9), 4)
    if cv2.__version__.startswith("4."):                       # bit-identical to cv2.putText 4.x
        rng = np.random.default_rng(0)
        for k in range(40):
            s = "".join(chr(int(c)) for c in rng.integers(32, 127, int(rng.integers(1, 10))))
            sc, th = float(rng.uniform(0.2, 1.2)), int(rng.integers(1, 4))
            lt = (cv2.LINE_AA, cv2.LINE_8)[k % 2]
            a = rng.integers(0, 256, (50, 220, 3)).astype(np.uint8)
            b = a.copy()
            cv2.putText(a, s, (3, 35), cv2.FONT_HERSHEY_SIMPLEX, sc, (200, 30, 90), th, lt)
            hershey.put_text(b, s, (3, 35), sc, (200, 30, 90), th, lt)
            assert np.array_equal(a, b), (s, sc, th)
            assert hershey.get_text_size(s, sc, th) == cv2.getTextSize(s, cv2.FONT_HERSHEY_SIMPLEX, sc, th)


def test_det_params_overrides(tmp_path, monkeypatch):
    from treeaicoach import det_params as DP
    from treeaicoach import roster_matcher as RM

    f = tmp_path / "p.json"
    f.write_text(json.dumps({"params": {"roster_matcher.TRACK_RELAX": 9.0, "roster_matcher.NOPE": 1}}))
    vals = DP.load(f)
    assert vals == {"roster_matcher.TRACK_RELAX": DP.TUNABLE["roster_matcher.TRACK_RELAX"][1]}  # clamped
    old = RM.TRACK_RELAX
    try:
        assert DP.set_value("roster_matcher.TRACK_RELAX", 0.05) and RM.TRACK_RELAX == 0.05
        assert DP.get_value("detector.HybridDetector.EXTRA_VERIFY") >= 0.0
    finally:
        RM.TRACK_RELAX = old


def test_micro_tracker_runs_fast():
    import det_micro

    res = det_micro.micro_tracker(n=6, seed=1)
    assert res["frames"] > 100 and res["wrong_place_frames"] <= res["frames"] * 0.05


def test_gallery_and_diag_converter(tmp_path):
    """A tiny gym game: failure gallery written; a fake diagnostic bundle from the engine's
    own diag_snapshot converts into a scored real case."""
    import cv2

    import det_gym as G
    import diag_to_gym
    from training.real_art import RealArt
    from treeaicoach.champions import get_default_db
    from treeaicoach.diag import _jsonable

    db, art = get_default_db(), RealArt()
    sc = [s for s in G.scenarios(quick=True) if s.name == "customskin"][0]
    sc.seconds = 3.0
    gal = G.Gallery(tmp_path / "gal")
    G._patch_clock()
    try:
        G.run_game(sc, db, art, gallery=gal)
        counts = gal.finish()
        # fake bundle: 4 samples of another short game through the engine
        sim, pipe = G.Sim(sc, db, art), G.Pipeline(db)
        bundle = tmp_path / "diag_test"
        (bundle / "frames").mkdir(parents=True)
        for f in range(12):
            t = f / sc.fps
            sim.step(t, 1 / sc.fps)
            img, *_ = sim.render(1 / sc.fps)
            game = sim.game_info(t)
            ids, _ms = pipe.step(t, img, game)
            if f % 3 == 2:
                pipe.eng._frame, pipe.eng._identified = img, ids
                snap = pipe.eng.diag_snapshot()
                snap.pop("frame", None)
                snap.pop("preview", None)
                cv2.imwrite(str(bundle / "frames" / f"{f:03d}_minimap.png"), img)
                (bundle / "frames" / f"{f:03d}.json").write_text(json.dumps(_jsonable(snap)))
    finally:
        G._unpatch_clock()
    assert sum(counts.values()) >= 1 and any((tmp_path / "gal").glob("*.png"))
    zpath = tmp_path / "diag_test.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        for p in bundle.rglob("*"):
            zf.write(p, p.relative_to(tmp_path))
    out = tmp_path / "case"
    stats = diag_to_gym.convert(zpath, out)
    assert stats["images"] == 4 and stats["labels"] >= 4
    gt = json.loads((out / "ground_truth.json").read_text())
    assert gt["pseudo"] and all("needs_check" in v for v in gt["images"].values())
    r = G.run_real(out)
    assert r["gt"] == stats["labels"] and r["rec"] >= 0.8
