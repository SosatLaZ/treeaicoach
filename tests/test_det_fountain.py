"""Hybrid detector: an ally icon cut by the corner in our fountain (portrait not recognised)."""
from __future__ import annotations

import types

import numpy as np

from treeaicoach.detector import Detection, HybridDetector, OnnxDetector


class _Onnx(OnnxDetector):
    def __init__(self, dets):          # no session: only detect() is used
        self.dets = dets
        self.threshold = 0.4

    def detect(self, img):
        return list(self.dets)


def _ent(alias, rel):
    return types.SimpleNamespace(alias=alias, relation=rel)


class _Matcher:
    has_roster = True
    last_dead: tuple = ()
    last_changed = False

    def __init__(self, dets, ents):
        self.dets, self.entries = dets, ents

    def detect(self, img):
        return list(self.dets)


def _det(u, v, score, cls="ally", alias=None):
    return Detection(u=u, v=v, r=0.045, score=score, cls=cls, cls_probs=(0.0, 1.0, 0.0), alias=alias)


def _hybrid(generic, matched, ents):
    h = HybridDetector(_Onnx(generic), _Matcher(matched, ents))
    h.EXTRA_VERIFY = 0.0
    h._structures, h._fountains = [], [(0.0265, 0.9692), (0.9644, 0.0394)]
    return h


ENTS = [_ent(a, "ally") for a in ("A", "B", "C", "D", "Me")] + [_ent(e, "enemy") for e in "VWXYZ"]
IMG = np.zeros((200, 200, 3), np.uint8)


def test_strong_corner_icon_is_kept_and_named_when_one_ally_is_missing():
    matched = [_det(0.3 + 0.1 * k, 0.5, 0.9, alias=a) for k, a in enumerate("ABCD")]
    out = _hybrid([_det(0.032, 0.967, 0.69)], matched, ENTS).detect(IMG)
    extra = [d for d in out if d.u < 0.1]
    assert len(extra) == 1 and extra[0].alias == "Me"


def test_two_allies_missing_is_not_drawn_anonymous():
    matched = [_det(0.3 + 0.1 * k, 0.5, 0.9, alias=a) for k, a in enumerate("ABC")]
    out = _hybrid([_det(0.032, 0.967, 0.69)], matched, ENTS).detect(IMG)
    assert not [d for d in out if d.u < 0.1]


def test_weak_fountain_glyph_is_still_ignored():
    matched = [_det(0.3 + 0.1 * k, 0.5, 0.9, alias=a) for k, a in enumerate("ABCD")]
    out = _hybrid([_det(0.012, 0.96, 0.38)], matched, ENTS).detect(IMG)
    assert not [d for d in out if d.u < 0.1]
