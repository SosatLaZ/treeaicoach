"""Tests of training/synth.py (synthetic minimaps). Only numpy / OpenCV are needed."""

from __future__ import annotations

import time
from collections import Counter
from pathlib import Path

import sys

import numpy as np
import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:   # plain `pytest` (not `python -m pytest`)
    sys.path.insert(0, str(_ROOT))

from training import synth  # noqa: E402


def _samples(seed: int, n: int, size: int = 256, **kw):
    rng = np.random.default_rng(seed)
    return [synth.generate_sample(rng, size, **kw) for _ in range(n)]


@pytest.fixture(scope="module")
def batch() -> list:
    return _samples(123, 60)


def test_shape_dtype(batch: list) -> None:
    for img, labels in batch:
        assert img.shape == (256, 256, 3) and img.dtype == np.uint8
        assert img.flags.c_contiguous
        assert isinstance(labels, list)


@pytest.mark.parametrize("size", [96, 128, 320])
def test_other_sizes(size: int) -> None:
    for img, labels in _samples(5, 3, size):
        assert img.shape == (size, size, 3) and img.dtype == np.uint8
        for lab in labels:
            assert 0.0 <= lab["u"] < 1.0 and 0.0 <= lab["v"] < 1.0


def test_labels_schema_and_bounds(batch: list) -> None:
    n_labels = 0
    for _img, labels in batch:
        for lab in labels:
            n_labels += 1
            assert {"u", "v", "r", "cls", "cls_valid"} <= set(lab)
            assert 0.0 <= lab["u"] < 1.0 and 0.0 <= lab["v"] < 1.0
            # icon diameter 0.07-0.12 of the width (+ small crop jitter)
            assert 0.03 < lab["r"] < 0.07
            assert lab["cls"] in ("enemy", "ally")          # "self" is labelled "ally"
            assert lab["cls"] in synth.CLASSES
            assert isinstance(lab["cls_valid"], bool)
            assert 0.0 <= lab.get("vis", 1.0) <= 1.0
    assert n_labels > 3 * len(batch)


def test_class_count_constraints(batch: list) -> None:
    empties = 0
    for _img, labels in batch:
        c = Counter(lab["cls"] for lab in labels)
        assert c["enemy"] <= 5
        assert c["ally"] <= 5                                # 4 allies + me
        assert len(labels) <= 10
        empties += not labels
    assert empties < len(batch) // 2


def test_class_statistics() -> None:
    """Both classes are frequent; ~10 % random-hue rings (cls_valid False)."""
    labels = [lab for _, labs in _samples(7, 80) for lab in labs]
    c = Counter(lab["cls"] for lab in labels)
    assert c["enemy"] > 0.15 * len(labels) and c["ally"] > 0.3 * len(labels)
    invalid = sum(not lab["cls_valid"] for lab in labels) / len(labels)
    assert 0.03 < invalid < 0.2


def test_determinism() -> None:
    a = _samples(42, 4)
    b = _samples(42, 4)
    for (ia, la), (ib, lb) in zip(a, b):
        assert np.array_equal(ia, ib)
        assert la == lb
    c = _samples(43, 1)
    assert not np.array_equal(a[0][0], c[0][0])


def test_native_size_range() -> None:
    rng = np.random.default_rng(0)
    sizes = [synth.sample_native_size(rng) for _ in range(300)]
    assert min(sizes) >= 170 and max(sizes) <= 440
    assert len(set(sizes)) > 50


def test_self_labelled_ally_and_positions_match_icons() -> None:
    """Without degradation, labels sit exactly on the rendered sprites."""
    cfg = synth.SynthConfig(p_jitter=0.0, p_video_downscale=0.0, p_jpeg=0.0, p_blur=0.0,
                            p_noise=0.0, p_color=0.0, p_empty=0.0, p_random_hue=0.0)
    rng = np.random.default_rng(3)
    seen_self = 0
    for _ in range(12):
        native = 256
        img, sprites, valid, scene = synth.render_native(rng, native, cfg)
        labels = synth._labels(sprites, valid, native, (0.0, 0.0, float(native), float(native)))
        labelled = [s for s in sprites if 0 <= s.u < 1 and 0 <= s.v < 1 and not s.grey]
        assert len(labels) == len(labelled)
        for s, lab in zip(labelled, labels):
            assert lab["u"] == pytest.approx(s.u, abs=1e-4)
            assert lab["v"] == pytest.approx(s.v, abs=1e-4)
            assert lab["r"] == pytest.approx(s.r, abs=1e-4)
            if s.relation == "self":
                seen_self += 1
                assert lab["cls"] == "ally"
            else:
                assert lab["cls"] == ("enemy" if s.relation == "enemy" else "ally")
        assert img.shape == (native, native, 3)
    assert seen_self > 0


def test_empty_maps_exist() -> None:
    cfg = synth.SynthConfig(p_empty=1.0)
    for img, labels in _samples(9, 3, cfg=cfg):
        assert labels == [] and img.shape == (256, 256, 3)


def test_draw_labels_and_contact_sheet(batch: list) -> None:
    img, labels = batch[0]
    drawn = synth.draw_labels(img, labels)
    assert drawn.shape == img.shape
    sheet = synth.contact_sheet([b[0] for b in batch[:5]], cols=3)
    assert sheet.shape[0] > 2 * 256 and sheet.shape[1] > 3 * 256


def test_cli_preview(tmp_path: Path) -> None:
    rc = synth.main(["--preview", "2", "--out", str(tmp_path), "--size", "96"])
    assert rc == 0
    assert (tmp_path / "contact_sheet.png").is_file()
    assert (tmp_path / "synth_000.png").is_file() and (tmp_path / "synth_001.json").is_file()


def test_throughput_smoke() -> None:
    rng = np.random.default_rng(1)
    for _ in range(5):
        synth.generate_sample(rng, 256)        # warm-up (asset loading)
    n = 30
    t0 = time.perf_counter()
    for _ in range(n):
        synth.generate_sample(rng, 256)
    rate = n / (time.perf_counter() - t0)
    assert rate > 10.0, f"{rate:.1f} samples/s"   # target >= 150/s on a free core
