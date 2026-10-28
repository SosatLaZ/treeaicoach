"""Training data collected during the user's games (active learning + hard negatives).

The detector only gets better on the user's own client with the user's own frames. This module
is the per-game collection hook; the engine / analysis report / export call it, nothing here
touches the screen or the game (frames are the minimap crops the engine already has).

* **Hard negatives (idea 35)** - detections that are certainly wrong:
  a detection named after a champion the Live Client says is DEAD, and the excess of a team
  (more icons of a side than its alive champions: the lowest scores of that side are false).
* **Active learning (idea 34)** - frames with uncertain detections (score in
  :data:`UNCERTAIN_BAND`, or an identity score below :data:`LOW_ID`): saved for review
  (``tools/real_truth_case.py`` review flow) and never trained on until reviewed.
* **"Faux" feedback (ideas 120 / 121)** - :meth:`DetCollector.mark_wrong` (hotkey / voice /
  Discord friend): the frames of the last seconds are kept as disputed (review first).

:meth:`DetCollector.export_case` writes a folder in the real-case format
(``ground_truth.json`` + PNGs, like ``tests/fixtures/real_cases/*``); hard-negative frames carry
``focus`` discs (only those discs are trained, everything else is ignored: the frame's other
icons are unknown) and ``tools/det_train_fast.py --extra-cases <dir>`` mixes them into the
fine-tune. :meth:`DetCollector.zip_entries` gives the same files as ``{name: bytes}`` for the
analysis-report zip (the exporter adds them; nothing is sent anywhere by this module).

Bounded memory (:data:`MAX_FRAMES` PNG-encoded frames per game), rate-limited, never raises.
"""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

log = logging.getLogger(__name__)

MAX_FRAMES = 40                 # frames kept per game (PNG, ~60 kB each)
MIN_GAP_S = 8.0                 # between two collected frames of the same kind
UNCERTAIN_BAND = (0.25, 0.55)   # detection score: neither clearly an icon nor clearly not
LOW_ID = 0.6                    # identity score under which a named detection is "uncertain"
FAUX_WINDOW_S = 12.0            # "faux": the frames of the last seconds are disputed
FOCUS_R = 0.06                  # normalized radius of a hard negative's training disc
MAX_SIDE = 400                  # frames larger than this are downscaled before encoding


@dataclass
class _Frame:
    t: float
    kind: str                         # "negative" | "uncertain" | "disputed"
    png: bytes
    dets: list[dict]
    negatives: list[dict] = field(default_factory=list)
    uncertain: list[dict] = field(default_factory=list)
    game_time: float | None = None


def _det_dict(d: Any) -> dict:
    return {"u": round(float(d.u), 4), "v": round(float(d.v), 4),
            "r": round(float(getattr(d, "r", 0.045) or 0.045), 4),
            "score": round(float(getattr(d, "score", 0.0) or 0.0), 3),
            "team": "enemy" if getattr(d, "cls", "") == "enemy" else "ally",
            "alias": getattr(d, "alias", None)}


def hard_negatives(dets: Sequence[Any], dead: Iterable[str] = (), alive_counts: dict | None = None
                   ) -> list[int]:
    """Indices of detections that are certainly false: named after a dead champion, or the
    lowest-scored excess of a side with more icons than alive champions (``alive_counts``:
    ``{"ally": n, "enemy": n}``, me included in ally)."""
    try:
        dead = {str(a) for a in dead}
        out = {i for i, d in enumerate(dets) if getattr(d, "alias", None) and d.alias in dead}
        for team, n in (alive_counts or {}).items():
            side = sorted((i for i, d in enumerate(dets) if i not in out
                           and ("enemy" if d.cls == "enemy" else "ally") == team),
                          key=lambda i: float(dets[i].score))
            excess = len(side) - max(0, int(n))
            out.update(side[:max(0, excess)])
        return sorted(out)
    except Exception:
        log.debug("hard_negatives failed", exc_info=True)
        return []


def uncertain(dets: Sequence[Any], id_scores: dict | None = None) -> list[int]:
    """Indices of uncertain detections (score in the band, or a weak identity)."""
    lo, hi = UNCERTAIN_BAND
    out = []
    for i, d in enumerate(dets):
        try:
            s = float(d.score)
            ids = (id_scores or {}).get(getattr(d, "alias", None))
            if lo <= s < hi or (ids is not None and float(ids) < LOW_ID):
                out.append(i)
        except Exception:
            continue
    return out


def _encode(bgr: Any) -> bytes | None:
    try:
        import cv2

        h, w = bgr.shape[:2]
        if max(h, w) > MAX_SIDE:
            f = MAX_SIDE / max(h, w)
            bgr = cv2.resize(bgr, (max(1, round(w * f)), max(1, round(h * f))), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".png", bgr)
        return bytes(buf) if ok else None
    except Exception:
        return None


class DetCollector:
    """Per-game bank of minimap frames worth training on / reviewing. Thread-safe."""

    def __init__(self, max_frames: int = MAX_FRAMES, min_gap_s: float = MIN_GAP_S) -> None:
        self.max_frames = int(max_frames)
        self.min_gap_s = float(min_gap_s)
        self._lock = threading.Lock()
        self._frames: list[_Frame] = []
        self._last: dict[str, float] = {}
        self._recent: list[tuple[float, Any, list[dict], float | None]] = []   # for "faux"

    def reset(self) -> None:
        with self._lock:
            self._frames.clear()
            self._last.clear()
            self._recent.clear()

    def __len__(self) -> int:
        return len(self._frames)

    def counts(self) -> dict[str, int]:
        with self._lock:
            out: dict[str, int] = {}
            for f in self._frames:
                out[f.kind] = out.get(f.kind, 0) + 1
            return out

    def _add(self, fr: _Frame) -> None:
        self._frames.append(fr)
        self._last[fr.kind] = fr.t
        if len(self._frames) > self.max_frames:
            # drop the oldest of the most common kind (keeps a mix)
            kinds = [f.kind for f in self._frames]
            common = max(set(kinds), key=kinds.count)
            self._frames.pop(kinds.index(common))

    def feed(self, minimap_bgr: Any, dets: Sequence[Any], now: float, dead: Iterable[str] = (),
             alive_counts: dict | None = None, id_scores: dict | None = None,
             game_time: float | None = None) -> str | None:
        """One engine frame. Returns the kind collected (``"negative"`` / ``"uncertain"``) or None."""
        try:
            if minimap_bgr is None or getattr(minimap_bgr, "size", 0) == 0:
                return None
            dets = list(dets or [])
            dd = [_det_dict(d) for d in dets]
            with self._lock:
                self._recent = [r for r in self._recent if now - r[0] <= FAUX_WINDOW_S]
                if not self._recent or now - self._recent[-1][0] >= 1.0:
                    self._recent.append((now, minimap_bgr.copy(), dd, game_time))
            neg = hard_negatives(dets, dead, alive_counts)
            unc = [] if neg else uncertain(dets, id_scores)
            kind = "negative" if neg else ("uncertain" if unc else None)
            if kind is None:
                return None
            with self._lock:
                if now - self._last.get(kind, -1e9) < self.min_gap_s:
                    return None
            png = _encode(minimap_bgr)
            if png is None:
                return None
            with self._lock:
                self._add(_Frame(now, kind, png, dd, [dd[i] for i in neg], [dd[i] for i in unc], game_time))
            return kind
        except Exception:
            log.debug("DetCollector.feed failed", exc_info=True)
            return None

    def mark_wrong(self, now: float, window_s: float = FAUX_WINDOW_S) -> int:
        """"Faux" (hotkey / voice): the frames of the last ``window_s`` become disputed frames."""
        try:
            with self._lock:
                recent = [r for r in self._recent if 0 <= now - r[0] <= window_s]
            n = 0
            for t, img, dd, gt in recent[-3:]:
                png = _encode(img)
                if png is not None:
                    with self._lock:
                        self._add(_Frame(t, "disputed", png, dd, game_time=gt))
                    n += 1
            return n
        except Exception:
            log.debug("DetCollector.mark_wrong failed", exc_info=True)
            return 0

    def case_files(self, tag: str = "user") -> dict[str, bytes]:
        """``{relative name: bytes}``: the PNGs + ``ground_truth.json`` in the real-case format."""
        files: dict[str, bytes] = {}
        images: dict[str, dict] = {}
        with self._lock:
            frames = list(self._frames)
        for k, f in enumerate(frames):
            name = f"{tag}_{k:03d}_{f.kind}"
            files[name + ".png"] = f.png
            spec: dict[str, Any] = {"file": name + ".png", "game": "g1", "kind": f.kind,
                                    "champions": [], "detections": f.dets}
            if f.game_time is not None:
                spec["time"] = round(float(f.game_time), 1)
            if f.kind == "negative":
                # train only the false-positive discs; every other detection is unknown
                spec["focus"] = [{"u": n["u"], "v": n["v"], "r": FOCUS_R} for n in f.negatives]
                spec["negatives"] = f.negatives
            else:
                spec["needs_review"] = True
                spec["uncertain"] = f.uncertain
            spec["unsure"] = [{"u": d["u"], "v": d["v"], "r": max(0.05, d["r"]), "team": d["team"]}
                              for d in f.dets if d not in f.negatives]
            images[name] = spec
        if images:
            gt = {"source": "det_collect", "rosters": {"g1": {}}, "images": images}
            files["ground_truth.json"] = json.dumps(gt, indent=1).encode("utf-8")
        return files

    def zip_entries(self, prefix: str = "det_collect/") -> dict[str, bytes]:
        """Same files for the analysis-report zip (``prefix`` folder)."""
        return {prefix + k: v for k, v in self.case_files().items()}

    def export_case(self, out_dir: Path | str) -> Path | None:
        """Write the collected frames as a case folder; None when nothing was collected."""
        try:
            files = self.case_files()
            if not files:
                return None
            out = Path(out_dir)
            out.mkdir(parents=True, exist_ok=True)
            for k, v in files.items():
                (out / k).write_bytes(v)
            return out
        except Exception:
            log.debug("DetCollector.export_case failed", exc_info=True)
            return None
