"""Fast, exactly reproducible detection experiments: det_gym + real bench from caches.

Same numbers as ``tools/det_gym.py`` (+ the real crops / real cases of
``tools/real_minimap_bench.py``), but:

1. **Frame cache** - every gym game is rendered ONCE per (scenario, renderer source,
   ``GYM_VERSION``, OpenCV/numpy) into a compressed shard (lossless PNG frames + truth + game
   info): ``<cache>/frames/``.
2. **Stage cache** - the detector stage of a game (every call the engine makes on the
   detector / roster matcher / icon learner, with its result) is recorded once per
   (frames, detector key) into a tape: ``<cache>/tape/``. The detector key hashes the
   detector-side sources (:data:`DET_MODULES`), their module constants (so a sweep of a
   ``roster_matcher`` knob re-runs the detector, a ``tracker`` knob does not), the model
   assets and the library versions. A replay feeds the engine the recorded results and
   checks every call (name + argument hash) and attribute read: any difference (e.g. a
   downstream change makes the engine call the detector differently) raises
   :class:`Divergence` and that game is re-run for real. Raw ONNX outputs are cached per
   (model hash, input tensor hash) (``detector.STAGE_CACHE`` hook): ``<cache>/onnx/``.
   Real-bench images are cached per (image bytes, labels, detector key): ``<cache>/real/``.
3. **Deterministic clock** - the gym's simulated clock (``det_gym.CLOCK_MODULES``) and
   ``PYTHONHASHSEED=0`` (the tool re-executes itself): two runs give byte-identical metrics
   (``ms`` / ``cpu`` / wall times are kept apart in ``timing``).
4. **Hard subset** - ``hard --build``: windows (2 s pre-roll, fresh pipeline) around the
   frames where HEAD errs, plus a fixed 5 % random control set; ``--subset hard`` scores only
   those windows (quick score; ``sweep`` reports its correlation with the full score).
5. **Process pool** - one game / one real image per task, 1 thread per process.
6. **Sweeps** - ``sweep --param roster_matcher.X=a,b,c`` (several ``--param``: cartesian
   product), every point evaluated from the caches, one table of full metrics.

    python tools/det_fast.py run [--suites main,hard,...] [--no-real] [--set mod.X=v] [--json OUT]
    python tools/det_fast.py sweep --param tracker.X=1,2 [--param ...] [--subset hard]
    python tools/det_fast.py verify          # cached == uncached pool == det_gym.py, x2 identical
    python tools/det_fast.py hard --build    # hard subset from the current (HEAD) run
    python tools/det_fast.py info | clean

Cache: ``$DET_FAST_CACHE`` (default ``<tmp>/det_fast_cache``); everything in it is rebuilt
on demand (delete it any time). See tools/README_detection_gym.md.
"""

from __future__ import annotations

import os
import sys

# one thread per process (set before numpy / OpenCV / onnxruntime load)
for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")

import argparse  # noqa: E402
import ast  # noqa: E402
import hashlib  # noqa: E402
import importlib  # noqa: E402
import itertools  # noqa: E402
import json  # noqa: E402
import math  # noqa: E402
import pickle  # noqa: E402
import tempfile  # noqa: E402
import time  # noqa: E402
import zlib  # noqa: E402
from dataclasses import is_dataclass  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import det_gym as G  # noqa: E402

import cv2  # noqa: E402
import numpy as np  # noqa: E402

ROOT = G.ROOT
CACHE = Path(os.environ.get("DET_FAST_CACHE") or Path(tempfile.gettempdir()) / "det_fast_cache")
DEFAULT_SUITES = ("main", "hard", "botlane", "mirror", "holdout")
#: Detector-side modules: their sources + module constants make the detector key.
DET_MODULES = ("detector", "roster_matcher", "self_icon", "patch_classifier", "det_params",
               "camera_proj", "champions", "identifier", "render", "roles", "gpu_runtime", "paths",
               "geometry")
#: Timing fields (machine-dependent): kept out of the metrics.
TIMING = ("ms", "ms95", "cpu", "games_s", "real_s")
TAPE_VERSION = 2          # tape format (frames hashed by their stored PNG bytes)
PRE_ROLL_S = 2.0          # hard-subset window: fresh pipeline this long before the scored frames
CONTROL_FRAC = 0.05


# ======================================================================================
# Keys
# ======================================================================================

def _sha(*parts: Any) -> str:
    h = hashlib.blake2b(digest_size=16)
    for p in parts:
        h.update(p if isinstance(p, (bytes, bytearray, memoryview)) else repr(p).encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()


def _file_sha(path: Path) -> str:
    try:
        return _sha(Path(path).read_bytes())
    except OSError:
        return "missing"


_STATIC: dict = {}


def _static_det_part() -> str:
    """Sources of the detector modules, model / data assets, library versions (per process)."""
    if "det" not in _STATIC:
        pkg = Path(sys.modules["treeaicoach"].__file__).parent
        parts = [G.deps_versions()]
        for m in DET_MODULES:
            parts.append((m, _file_sha(pkg / f"{m}.py")))
        assets = pkg / "assets"
        for p in sorted(assets.rglob("*")) if assets.is_dir() else []:
            if p.is_file():
                rel = str(p.relative_to(assets))
                big = p.suffix.lower() in (".onnx", ".json", ".npz", ".npy")
                parts.append((rel, p.stat().st_size, _file_sha(p) if big else ""))
        _STATIC["det"] = _sha(*parts)
    return _STATIC["det"]


def _constants(mod: Any) -> list:
    out = []
    for k, v in sorted(vars(mod).items()):
        if k.isupper() and isinstance(v, (int, float, str, bool, tuple, frozenset, type(None))):
            out.append((k, v))
    return out


def det_key() -> str:
    """Detector key: static part + the CURRENT detector-side module constants (overrides)."""
    parts = [_static_det_part()]
    for m in DET_MODULES:
        try:      # (all imported: lazily imported modules would change the key later)
            mod = importlib.import_module(f"treeaicoach.{m}")
        except Exception:
            continue
        parts.append((m, _constants(mod)))
    return _sha(*parts)


def frames_key(sc: Any) -> str:
    """Rendered frames of a scenario: renderer sources (det_gym up to the pipeline, real_art,
    render), GYM_VERSION, the scenario, library versions."""
    if "render" not in _STATIC:
        src = Path(G.__file__).read_text(encoding="utf-8")
        src = src[: src.find("class _Voice")]
        _STATIC["render"] = _sha(src, _file_sha(ROOT / "training" / "real_art.py"),
                                 _file_sha(ROOT / "treeaicoach" / "render.py"),
                                 _file_sha(ROOT / "treeaicoach" / "champions.py"),
                                 G.GYM_VERSION, G.deps_versions())
    return _sha(_STATIC["render"], repr(sorted(vars(sc).items())))


# ======================================================================================
# Small store
# ======================================================================================

def _load(path: Path) -> Any:
    try:
        return pickle.loads(zlib.decompress(path.read_bytes()))
    except (OSError, zlib.error, pickle.UnpicklingError, EOFError, AttributeError, ValueError):
        return None


def _save(path: Path, obj: Any, level: int = 1) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".tmp{os.getpid()}")
    tmp.write_bytes(zlib.compress(pickle.dumps(obj, protocol=5), level))
    os.replace(tmp, path)


# ======================================================================================
# 1. Frame cache
# ======================================================================================

class CachedSource:
    """det_gym frame source from a cached shard (rendered once by :class:`det_gym.SimSource`)."""

    def __init__(self, sc: Any, db: Any, art_fn: Any, f_range: tuple | None = None) -> None:
        self.key = frames_key(sc)
        path = CACHE / "frames" / f"{sc.name}_{self.key}.pkl.z"
        data = _load(path)
        self.built = data is None
        if data is None:
            src = G.SimSource(sc, db, art_fn())
            recs = []
            for r in src.frames():
                ok, png = cv2.imencode(".png", r.img, [cv2.IMWRITE_PNG_COMPRESSION, 1])
                assert ok and np.array_equal(cv2.imdecode(png, cv2.IMREAD_UNCHANGED), r.img)
                recs.append((r.f, r.t, png.tobytes(), r.truth, r.dead, pickle.dumps(r.game, 5),
                             r.me_pos, r.me_alive, r.me_recall))
            data = {"me_tid": src.me_tid, "mirrored": src.mirrored, "recs": recs}
            _save(path, data, level=0)
        self.me_tid, self.mirrored, self.recs = data["me_tid"], data["mirrored"], data["recs"]
        self.f_range = f_range
        self.keep = False
        self._dec: dict = {}
        self._tok: dict = {}

    def __len__(self) -> int:
        return len(self.recs)

    def frames(self):
        lo, hi = self.f_range or (0, len(self.recs) - 1)
        for f, t, png, truth, dead, game, me_pos, me_alive, me_recall in self.recs[lo: hi + 1]:
            img = self._img(f, png)
            _FRAME_TOKEN.clear()
            _FRAME_TOKEN[id(img)] = (img, ("frame", self._tok[f]))
            yield G.FrameRec(f, t, img, truth, dead, pickle.loads(game), me_pos, me_alive, me_recall)
        _FRAME_TOKEN.clear()

    def _img(self, f: int, png: bytes) -> np.ndarray:
        """Decoded frame (kept for the next sweep point / window of this task)."""
        img = self._dec.get(f)
        if img is None:
            img = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_UNCHANGED)
            self._tok[f] = _sha(png)
            if self.keep:
                self._dec[f] = img
        return img.copy() if self.keep else img


# ======================================================================================
# 2. Stage cache: detector record / replay tape, ONNX outputs
# ======================================================================================

class Divergence(BaseException):
    """The replay does not match the tape (BaseException: the engine's ``except Exception``
    must not swallow it)."""


_SIMPLE = (list, tuple, dict, set, frozenset, str, bytes, int, float, bool, type(None), np.ndarray,
           np.generic)


def _proxyable(v: Any) -> bool:
    return (not isinstance(v, _SIMPLE) and not callable(v) and not is_dataclass(v)
            and str(getattr(type(v), "__module__", "")).startswith("treeaicoach"))


#: id(frame array) -> (array, token): cached frames are hashed by their stored bytes, once.
_FRAME_TOKEN: dict = {}


def _arg_sha(a: tuple, k: dict) -> str:
    if _FRAME_TOKEN:
        a = tuple(_FRAME_TOKEN[id(x)][1] if isinstance(x, np.ndarray) and id(x) in _FRAME_TOKEN
                  and _FRAME_TOKEN[id(x)][0] is x else x for x in a)
    try:
        return _sha(pickle.dumps((a, sorted(k.items())), protocol=5))
    except Exception:
        return _sha(tuple(type(x).__name__ for x in a), sorted(k))


class Tape:
    """Calls (in order) and attribute reads (per call epoch) on the detector object graph."""

    def __init__(self, data: dict | None = None) -> None:
        self.play = data is not None
        self.calls: list = data["calls"] if data else []
        self.reads: dict = data["reads"] if data else {}
        self.epoch = 0
        self.unsafe = bool(data.get("unsafe")) if data else False
        self._last: tuple | None = None       # (object, bytes) of the last call result (record)

    def data(self) -> dict:
        return {"calls": self.calls, "reads": self.reads, "unsafe": self.unsafe}

    def check_last(self) -> None:
        """A result changed after it was returned (the detector mutates what it gave): replay
        stays exact for this code, but a downstream sweep could differ -> tape flagged."""
        if self._last is not None:
            obj, b = self._last
            try:
                if pickle.dumps(obj, protocol=5) != b:
                    self.unsafe = True
            except Exception:
                pass
            self._last = None


class _P:
    """Record / replay proxy of one object of the detector graph (path = attribute chain)."""

    __slots__ = ("_p_t", "_p_tape", "_p_path")

    def __init__(self, target: Any, tape: Tape, path: str) -> None:
        object.__setattr__(self, "_p_t", target)
        object.__setattr__(self, "_p_tape", tape)
        object.__setattr__(self, "_p_path", path)

    def __bool__(self) -> bool:
        return True

    def __setattr__(self, name: str, value: Any) -> None:
        if not self._p_tape.play:
            setattr(self._p_t, name, value)

    def __getattr__(self, name: str) -> Any:
        tape, path = self._p_tape, self._p_path
        key = (tape.epoch, path, name)
        if tape.play:
            e = tape.reads.get(key)
            if e is None:
                raise Divergence(f"read {path}.{name} at call {tape.epoch} not in the tape")
            kind = e[0]
            if kind == "noattr":
                raise AttributeError(name)
            if kind == "val":
                return pickle.loads(e[1])
            if kind == "proxy":
                return _P(None, tape, f"{path}.{name}")
            return self._p_call(name, None)
        try:
            v = getattr(self._p_t, name)
        except AttributeError:
            tape.reads.setdefault(key, ("noattr",))
            raise
        if _proxyable(v):
            tape.reads.setdefault(key, ("proxy",))
            return _P(v, tape, f"{path}.{name}")
        if callable(v):
            tape.reads.setdefault(key, ("call",))
            return self._p_call(name, v)
        b = pickle.dumps(v, protocol=5)
        old = tape.reads.setdefault(key, ("val", b))
        if old[1] != b:       # (same epoch, other value: state changed without a call)
            tape.unsafe = True
        return v

    def _p_call(self, name: str, fn: Any) -> Any:
        tape, path = self._p_tape, self._p_path

        def call(*a: Any, **k: Any) -> Any:
            sig = (path, name, _arg_sha(a, k))
            if tape.play:
                if tape.epoch >= len(tape.calls):
                    raise Divergence(f"call {path}.{name} beyond the tape")
                c = tape.calls[tape.epoch]
                if c[0] != sig:
                    raise Divergence(f"call {tape.epoch}: {sig[:2]} != tape {c[0][:2]}"
                                     + (" (other arguments)" if c[0][:2] == sig[:2] else ""))
                tape.epoch += 1
                kind, payload = c[1], c[2]
                if kind == "exc":
                    raise pickle.loads(payload)
                if kind == "proxy":
                    return _P(None, tape, f"{path}.{name}()#{tape.epoch}")
                return pickle.loads(payload)
            tape.check_last()
            try:
                r = fn(*a, **k)
            except Exception as exc:
                try:
                    pe = pickle.dumps(exc, protocol=5)
                except Exception:
                    pe = pickle.dumps(RuntimeError(repr(exc)), protocol=5)
                tape.calls.append((sig, "exc", pe))
                tape.epoch += 1
                raise
            tape.epoch += 1
            if _proxyable(r):
                tape.calls.append((sig, "proxy", None))
                return _P(r, tape, f"{path}.{name}()#{tape.epoch}")
            b = pickle.dumps(r, protocol=5)
            tape.calls.append((sig, "val", b))
            tape._last = (r, b)
            return r

        return call


class OnnxCache:
    """``detector.STAGE_CACHE``: raw ONNX outputs per (model hash, input tensor hash)."""

    def __init__(self) -> None:
        self.store: dict = {}
        self.path: Path | None = None
        self.dirty = False
        self.hits = self.misses = 0
        self._model: dict = {}

    def open(self, name: str) -> None:
        self.flush()
        self.path = CACHE / "onnx" / f"{name}.pkl.z"
        self.store = _load(self.path) or {}

    def flush(self) -> None:
        if self.path is not None and self.dirty:
            _save(self.path, self.store)
        self.dirty = False

    def onnx(self, det: Any, x: np.ndarray, run: Any) -> list:
        mid = id(det._model_bytes)
        if mid not in self._model:
            self._model[mid] = _sha(det._model_bytes)
        key = _sha(self._model[mid], x.shape, x.dtype.str, np.ascontiguousarray(x).tobytes())
        outs = self.store.get(key)
        if outs is None:
            self.misses += 1
            outs = [np.array(o, copy=True) for o in run()]
            self.store[key] = outs
            self.dirty = True
        else:
            self.hits += 1
        return [np.array(o, copy=True) for o in outs]


# ======================================================================================
# Worker
# ======================================================================================

_W: dict = {}


def _worker_init() -> None:
    import logging

    try:
        cv2.setNumThreads(1)
    except Exception:
        pass
    logging.disable(logging.WARNING)


def _ctx() -> dict:
    if not _W:
        from treeaicoach import detector as D
        from treeaicoach.champions import get_default_db

        _W["db"] = get_default_db()
        _W["art"] = None
        _W["onnx"] = OnnxCache()
        D.STAGE_CACHE = _W["onnx"]
    return _W


def _art() -> Any:
    if _W.get("art") is None:
        from training.real_art import RealArt

        _W["art"] = RealArt()
    return _W["art"]


def _apply(overrides: dict) -> dict:
    saved = {}
    for k, v in overrides.items():
        mod, name = k.rsplit(".", 1)
        m = importlib.import_module(mod if mod.startswith(("treeaicoach", "tools")) else f"treeaicoach.{mod}")
        if not hasattr(m, name):
            raise SystemExit(f"unknown parameter {k}")
        saved[(m, name)] = getattr(m, name)
        setattr(m, name, v)
    return saved


def _restore(saved: dict) -> None:
    for (m, name), v in saved.items():
        setattr(m, name, v)


def _run_game_task(sc: Any, mode: str, window: tuple | None, src: Any = None) -> dict:
    """One game (or one hard-subset window ``(f0, f1, t_score)``) -> GameMetrics + stats."""
    w = _ctx()
    db = w["db"]
    st = {"frames_built": False, "tape": "none", "div": "", "unsafe": False}
    G._patch_clock()
    try:
        if mode == "nocache":          # exactly det_gym.run_game (live render, live detector)
            from treeaicoach import detector as D

            D.STAGE_CACHE = None
            try:
                M = G.run_game(sc, db, _art())
            finally:
                D.STAGE_CACHE = w["onnx"]
            return {"M": M, "st": st}
        src = src if src is not None else CachedSource(sc, db, _art)
        src.f_range = window[:2] if window else None
        st["frames_built"] = src.built
        src.built = False
        warm = window[2] if window else G.WARMUP_S
        name = f"{sc.name}_{src.key}" + (f"_w{window[0]}-{window[1]}" if window else "")
        dkey = det_key()
        tpath = CACHE / "tape" / f"{name}_{dkey}_v{TAPE_VERSION}.pkl.z"
        data = _load(tpath) if mode == "auto" else None
        if data is not None:
            tape = Tape(data)
            try:
                M = G.run_game(sc, db, None, source=src, det=_P(None, tape, "det"), warmup_s=warm)
                st["tape"], st["unsafe"] = "replay", tape.unsafe
                return {"M": M, "st": st}
            except Divergence as exc:
                st["div"] = str(exc)[:200]
            finally:
                G.SIM.now = 0.0
        # record: the real detector behind a recording proxy (ONNX outputs cached)
        from treeaicoach.detector import create_detector

        w["onnx"].open(name.split("_w")[0] + "_" + G.deps_versions()["onnxruntime"])
        det = create_detector("auto", db=db, learn_cache=None,
                              scale_store={f"{sc.size}x{sc.size}": float(sc.scale_prior)}
                              if sc.scale_prior > 0 else None)
        tape = Tape()
        M = G.run_game(sc, db, None, source=src, det=_P(det, tape, "det"), warmup_s=warm)
        tape.check_last()
        w["onnx"].flush()
        if mode == "auto":         # (after a divergence: the new call pattern replaces the old)
            _save(tpath, tape.data())
        st["tape"], st["unsafe"] = "record", tape.unsafe
        return {"M": M, "st": st}
    finally:
        G._unpatch_clock()


def _real_task(fix: str, names: list, mode: str) -> dict:
    """Real-bench images of one fixture folder (each: a fresh detector, as run_all)."""
    w = _ctx()
    import real_minimap_bench as RB

    db = w["db"]
    truth = RB.load_truth(Path(fix))
    dkey = det_key()
    out, built = {}, 0
    for name in names:
        spec = truth["images"][name]
        key = _sha(_file_sha(Path(fix) / spec["file"]), json.dumps(spec, sort_keys=True),
                   json.dumps(truth["rosters"][spec["game"]], sort_keys=True), dkey, RB.FRAMES,
                   _file_sha(Path(RB.__file__)))
        path = CACHE / "real" / f"{key}.pkl.z"
        r = _load(path) if mode == "auto" else None
        if r is None:
            from treeaicoach import detector as D

            if mode == "nocache":
                D.STAGE_CACHE = None
            else:
                w["onnx"].open(f"real_{key}")
            try:
                r = RB.run_image(name, spec, truth, db, fix=Path(fix))
            finally:
                D.STAGE_CACHE = w["onnx"]
                w["onnx"].flush()
            built += 1
            if mode == "auto":
                _save(path, r)
        out[name] = r
    return {"res": out, "built": built}


def _unit(kind: str, payload: tuple, mode: str) -> dict:
    """One game (all its hard-subset windows) or one real fixture folder."""
    w = _ctx()
    t0, c0 = time.perf_counter(), time.process_time()
    if kind == "game":
        _suite, sc, windows = payload
        src = None
        if mode != "nocache":
            src = CachedSource(sc, w["db"], _art)
            src.keep = windows is not None and len(windows) > 1
        parts = [_run_game_task(sc, mode, win, src) for win in (windows or [None])]
        r = parts[0]
        for q in parts[1:]:
            r["M"].merge(q["M"])
            for k in ("frames_built", "unsafe"):
                r["st"][k] = r["st"][k] or q["st"][k]
            r["st"]["div"] = r["st"]["div"] or q["st"]["div"]
            if q["st"]["tape"] == "record":
                r["st"]["tape"] = "record"
    else:
        r = _real_task(*payload, mode)
    r["wall"], r["cpu"] = time.perf_counter() - t0, time.process_time() - c0
    return r


def _task(args: tuple) -> tuple:
    """A SEQUENCE of units (the games of one suite in det_gym's order, or every real fixture),
    one sweep point, in one fresh process: the same per-process history as det_gym.py (HEAD's
    detection keeps some per-process state from one game / image to the next, so the order
    matters; measured 2026-10-29: one holdout game and one real case differ when each runs
    alone)."""
    i_point, units, overrides, mode = args
    _ctx()
    saved = _apply(overrides)
    try:
        return i_point, [(k, pl, _unit(k, pl, mode)) for k, pl in units]
    finally:
        _restore(saved)


# ======================================================================================
# Driver
# ======================================================================================

def real_fixtures() -> list[Path]:
    cases = sorted(p.parent for p in (ROOT / "tests" / "fixtures" / "real_cases").glob("*/ground_truth.json"))
    return [ROOT / "tests" / "fixtures" / "real"] + cases


def build_tasks(suites: list[str], real: bool, points: list, mode: str, subset: dict | None = None) -> list:
    """One task per (suite, point) and one per (real bench, point); real first (longest cold)."""
    tasks = []
    for i, pt in enumerate(points):
        if real:
            import real_minimap_bench as RB

            units = [("real", (str(fix), list(RB.load_truth(fix)["images"]))) for fix in real_fixtures()]
            tasks.append((i, units, pt, mode))
        for suite in suites:
            units = []
            for sc in G.suite_scenarios(suite, False):
                if subset is not None:
                    wins = [tuple(w_) for w_ in subset.get(f"{suite}/{sc.name}", [])]
                    if wins:
                        units.append(("game", (suite, sc, wins)))
                else:
                    units.append(("game", (suite, sc, None)))
            if units:
                tasks.append((i, units, pt, mode))
    return tasks


_POOL: Any = None


def pool(jobs: int) -> Any:
    global _POOL
    if _POOL is None and jobs > 1:
        import multiprocessing as mp

        # every task in a fresh fork of this (pristine) process: no state leaks from one game /
        # image to the next (module caches, learned icons), so any order / split gives the same
        # numbers; the champion db and the imports are loaded once, here
        _ctx()
        _POOL = mp.get_context("fork").Pool(jobs, initializer=_worker_init, maxtasksperchild=1)
    return _POOL


def evaluate(suites: list[str], real: bool = True, overrides: dict | None = None, mode: str = "auto",
             jobs: int = 3, subset: dict | None = None) -> dict:
    """Same structure as ``det_gym.run`` (+ "timing", "stats"): one entry per suite."""
    return evaluate_points(suites, real, [dict(overrides or {})], mode, jobs, subset)[0]


def evaluate_points(suites: list[str], real: bool, points: list[dict], mode: str = "auto", jobs: int = 3,
                    subset: dict | None = None) -> list[dict]:
    """:func:`evaluate` of several override sets (sweep points): each task loads / decodes its
    game once and runs every point."""
    tasks = build_tasks(suites, real, points, mode, subset)
    t0 = time.perf_counter()
    p = pool(jobs)
    if p is None:
        _worker_init()
        results = [_task(t) for t in tasks]
    else:
        results = list(p.imap(_task, tasks, chunksize=1))
    wall = (time.perf_counter() - t0) / len(points)
    per: list = [[] for _ in points]
    for i, units in results:
        per[i].extend(units)
    for i in range(len(points)):      # det_gym order: suites in order, then real
        per[i].sort(key=lambda u: (u[0] == "real", suites.index(u[1][0]) if u[0] == "game" else 0))
    return [_assemble(suites, real, per[i], len(tasks) // len(points), wall) for i in range(len(points))]


def _assemble(suites: list[str], real: bool, results: list, n_tasks: int, wall: float) -> dict:
    out: dict = {"suites": {}, "timing": {"wall_s": wall}, "stats": {}}
    stats = {"tasks": n_tasks, "frames_built": 0, "tape_replay": 0, "tape_record": 0, "divergences": [],
             "unsafe": 0, "real_built": 0, "task_cpu_s": 0.0}
    by_suite: dict = {}
    real_res: dict = {}
    for kind, payload, r in results:
        stats["task_cpu_s"] += r["cpu"]
        if kind == "game":
            suite, sc, _w = payload
            st = r["st"]
            stats["frames_built"] += int(st["frames_built"])
            stats["tape_replay"] += int(st["tape"] == "replay")
            stats["tape_record"] += int(st["tape"] == "record")
            stats["unsafe"] += int(st["unsafe"])
            if st["div"]:
                stats["divergences"].append(f"{sc.name}: {st['div']}")
            by_suite.setdefault(suite, {}).setdefault(sc.name, G.GameMetrics()).merge(r["M"])
        else:
            real_res.setdefault(payload[0], {}).update(r["res"])
            stats["real_built"] += r["built"]
    for suite in suites:
        games = by_suite.get(suite, {})
        so: dict = {"games": {}}
        tot = G.GameMetrics()
        for name, M in games.items():          # (scenario order: tasks are in order)
            so["games"][name] = M.summary()
            so["games"][name]["fails"] = M.fails
            tot.merge(M)
        so["total"] = tot.summary()
        so["fails"] = tot.fails
        so["suite"], so["quick"] = suite, False
        out["suites"][suite] = so
    if real:
        import real_minimap_bench as RB

        fixes = real_fixtures()
        main_fix = str(fixes[0])
        ordered = {n: real_res[main_fix][n] for n in RB.load_truth(fixes[0])["images"]}
        out["real"] = G.real_summary(ordered)
        out["real_cases"] = {}
        for fix in fixes[1:]:
            res = {n: real_res[str(fix)][n] for n in RB.load_truth(fix)["images"]}
            out["real_cases"][fix.name] = {k: v for k, v in G.real_summary(res).items()
                                           if k not in ("misses", "errors")}
    out["stats"] = stats
    return out


def metrics_only(out: dict) -> dict:
    """``out`` without the machine-dependent fields (wall / CPU times, cache statistics)."""
    def strip(x: Any) -> Any:
        if isinstance(x, dict):
            return {k: strip(v) for k, v in x.items() if k not in TIMING + ("timing", "stats", "gallery")}
        if isinstance(x, (list, tuple)):
            return [strip(v) for v in x]
        return x
    return strip(out)


def canon(out: dict) -> str:
    return json.dumps(metrics_only(out), sort_keys=True, default=str)


def quality(so: dict, real: dict | None = None) -> float:
    return G.score(so["total"], real, cost=False)


def summary_row(out: dict) -> dict:
    """One line of numbers for a sweep table (all suites pooled + per-suite quality)."""
    row: dict = {}
    for suite, so in out["suites"].items():
        row[f"q_{suite}"] = quality(so)
    # pooled over the suites: counts summed, rates averaged
    keys = ("rec", "prec", "idsw", "frag", "g_live", "g_dead", "me_bad", "stk", "resets", "mjmp")
    for k in keys:
        vals = [so["total"][k] for so in out["suites"].values()]
        row[k] = float(np.nansum(vals)) if k in ("idsw", "frag", "g_live", "g_dead", "resets", "mjmp") \
            else float(np.nanmean(vals))
    if "real" in out:
        row["real_rec"], row["real_id"] = out["real"]["rec"], out["real"]["id"]
        cs = list(out.get("real_cases", {}).values())
        if cs:
            row["cases_rec"] = float(np.mean([c["rec"] for c in cs]))
            row["cases_id"] = float(np.mean([c["id"] for c in cs]))
    row["quality"] = float(np.mean([v for k, v in row.items() if k.startswith("q_")])) if out["suites"] else float("nan")
    return row


def report(out: dict) -> str:
    lines = []
    for suite, so in out["suites"].items():
        lines.append(f"== {suite}")
        lines.append(G.report(dict(so, games_s=0.0)).rsplit("\ntime:", 1)[0].rstrip())
        lines.append(f"quality {quality(so):.4f}")
    if "real" in out:
        r = out["real"]
        lines.append(f"REAL crops rec {r['rec']:.3f} prec {r['prec']:.3f} team {r['team']:.3f} id {r['id']:.3f}")
        for n, c in out.get("real_cases", {}).items():
            lines.append(f"REAL CASE {n:28s} rec {c['rec']:.3f} prec {c['prec']:.3f} "
                         f"team {c['team']:.3f} id {c['id']:.3f}")
    s, tm = out["stats"], out["timing"]
    lines.append(f"wall {tm['wall_s']:.1f} s | tasks {s['tasks']} | frames built {s['frames_built']} | "
                 f"tape replay {s['tape_replay']} record {s['tape_record']} | real built {s['real_built']} | "
                 f"divergences {len(s['divergences'])} | unsafe tapes {s['unsafe']}")
    for d in s["divergences"][:5]:
        lines.append(f"  divergence: {d}")
    return "\n".join(lines)


# ======================================================================================
# Hard subset
# ======================================================================================

HARD_SUBSET = "hard_subset.json"


def build_subset(out: dict, seed: int = 0) -> dict:
    """Windows ``(f0, f1, t_score)`` per game: the frames with a failure (HEAD), plus a fixed
    5 % random control set; each window starts PRE_ROLL_S earlier (fresh pipeline)."""
    rng = np.random.default_rng(seed)
    sub: dict = {}
    n_all = n_run = n_scored = 0
    for suite, so in out["suites"].items():
        for sc in G.suite_scenarios(suite, False):
            if sc.name not in so["games"]:
                continue
            n = int(round(sc.seconds * sc.fps))
            dt = 1.0 / sc.fps
            bad = {int(round(t / dt)) for _n, t, *_ in so["games"][sc.name]["fails"]}
            first = int(math.ceil(G.WARMUP_S / dt - 1e-9))
            ctrl = {int(f) for f in rng.choice(np.arange(first, n), max(1, int(round(CONTROL_FRAC * (n - first)))),
                                               replace=False)}
            frames = sorted(bad | ctrl)
            pre = int(round(PRE_ROLL_S / dt))
            wins: list = []
            for f in frames:          # merge: scored [f, f]; run from f - pre
                if wins and f - pre <= wins[-1][1] + 1:
                    wins[-1][1] = f
                else:
                    wins.append([max(0, f - pre), f, f])
            # scored range = [first scored frame, end]; t_score = time of the first scored frame
            sub[f"{suite}/{sc.name}"] = [(a, b, round(c * dt, 6) - 1e-9 if c > 0 else 0.0) for a, b, c in wins]
            n_all += n
            n_run += sum(b - a + 1 for a, b, _c in wins)
            n_scored += sum(b - c + 1 for _a, b, c in wins)
    sub["_info"] = {"frames": n_all, "run": n_run, "scored": n_scored, "pre_roll_s": PRE_ROLL_S,
                    "control": CONTROL_FRAC, "seed": seed}
    return sub


def load_subset() -> dict:
    p = CACHE / HARD_SUBSET
    if not p.is_file():
        raise SystemExit(f"no hard subset: run `det_fast.py hard --build` first ({p})")
    return json.loads(p.read_text(encoding="utf-8"))


# ======================================================================================
# CLI
# ======================================================================================

def _parse_val(s: str) -> Any:
    try:
        return ast.literal_eval(s)
    except (ValueError, SyntaxError):
        return s


def _overrides(items: list[str] | None) -> dict:
    out = {}
    for it in items or []:
        k, v = it.split("=", 1)
        out[k.strip()] = _parse_val(v.strip())
    return out


def _reexec_hashseed() -> None:
    """PYTHONHASHSEED=0: set iteration orders (and so every metric) identical run to run."""
    if os.environ.get("PYTHONHASHSEED") != "0":
        env = dict(os.environ, PYTHONHASHSEED="0")
        os.execve(sys.executable, [sys.executable] + sys.argv, env)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p: Any) -> None:
        p.add_argument("--suites", default=",".join(DEFAULT_SUITES))
        p.add_argument("--no-real", action="store_true")
        p.add_argument("--jobs", type=int, default=3)
        p.add_argument("--subset", choices=("hard",), help="score only the hard-subset windows")

    p_run = sub.add_parser("run", help="evaluate (caches built on demand)")
    common(p_run)
    p_run.add_argument("--set", action="append", help="override mod.NAME=value (e.g. tracker.X=0.5)")
    p_run.add_argument("--mode", default="auto", choices=("auto", "record", "nocache"),
                       help="auto: caches; record: frames cached, detector re-run, no tape/real "
                            "cache read; nocache: det_gym's own path in the pool")
    p_run.add_argument("--json")
    p_sw = sub.add_parser("sweep", help="parameter sweep from the caches")
    common(p_sw)
    p_sw.add_argument("--param", action="append", required=True, help="mod.NAME=a,b,c")
    p_sw.add_argument("--json")
    p_sw.add_argument("--corr", action="store_true", help="with --subset: also the full score of every "
                                                           "point and the subset / full correlation")
    p_v = sub.add_parser("verify", help="equivalence + reproducibility check")
    common(p_v)
    p_v.add_argument("--gym-json", nargs="*", help="det_gym.py --json outputs to compare with "
                                                   "(one per suite, same order as --suites)")
    p_h = sub.add_parser("hard", help="hard subset")
    common(p_h)
    p_h.add_argument("--build", action="store_true")
    sub.add_parser("info")
    sub.add_parser("clean")
    a = ap.parse_args(argv)
    if a.cmd in ("run", "sweep", "verify", "hard"):
        _reexec_hashseed()
    CACHE.mkdir(parents=True, exist_ok=True)
    if a.cmd == "info":
        tot = 0
        for d in sorted(p for p in CACHE.iterdir() if p.is_dir()):
            n = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
            tot += n
            print(f"{d.name:8s} {len(list(d.iterdir())):5d} files {n / 1e6:8.1f} MB")
        print(f"total {tot / 1e6:.1f} MB in {CACHE}")
        return 0
    if a.cmd == "clean":
        import shutil

        shutil.rmtree(CACHE, ignore_errors=True)
        print("removed", CACHE)
        return 0
    suites = [s for s in a.suites.split(",") if s]
    real = not a.no_real
    subset = load_subset() if getattr(a, "subset", None) else None
    if subset is not None:
        real = False
    if a.cmd == "run":
        mode = {"record": "fresh"}.get(a.mode, a.mode)
        out = evaluate(suites, real, _overrides(a.set), mode=mode, jobs=a.jobs, subset=subset)
        print(report(out))
        if a.json:
            Path(a.json).write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
        return 0
    if a.cmd == "hard":
        out = evaluate(suites, False, {}, jobs=a.jobs)
        sub_ = build_subset(out)
        (CACHE / HARD_SUBSET).write_text(json.dumps(sub_, indent=1), encoding="utf-8")
        i = sub_["_info"]
        print(f"hard subset: {i['scored']} scored frames / {i['frames']} ({i['scored'] / i['frames']:.1%}), "
              f"{i['run']} frames run ({i['run'] / i['frames']:.1%}) -> {CACHE / HARD_SUBSET}")
        if a.build:
            evaluate(suites, False, {}, jobs=a.jobs, subset=sub_)   # windows' tapes
        return 0
    if a.cmd == "verify":
        return verify(suites, real, a.jobs, a.gym_json)
    if a.cmd == "sweep":
        return sweep(suites, real, a.param, a.jobs, subset, a.json, a.corr)
    return 0


def sweep(suites: list[str], real: bool, params: list[str], jobs: int, subset: dict | None,
          json_out: str | None, corr: bool = False) -> int:
    axes = []
    for p in params:
        k, vals = p.split("=", 1)
        axes.append([(k.strip(), _parse_val(v.strip())) for v in vals.split(",")])
    points = [{}] + [dict(c) for c in itertools.product(*axes)]
    rows = []
    t0 = time.perf_counter()
    outs = evaluate_points(suites, real, points, jobs=jobs, subset=subset)
    t1 = time.perf_counter()
    fulls = evaluate_points(suites, False, points, jobs=jobs) if corr and subset is not None else None
    t2 = time.perf_counter()
    print(f"{len(points)} points in {t1 - t0:.1f} s ({(t1 - t0) / len(points):.1f} s / point)"
          + (f"; full suites for the correlation {t2 - t1:.1f} s ({(t2 - t1) / len(points):.1f} s / point)"
             if fulls else ""))
    for i, (pt, out) in enumerate(zip(points, outs)):
        row = summary_row(out)
        row["point"] = ", ".join(f"{k.split('.')[-1]}={v}" for k, v in pt.items()) or "(defaults)"
        row["s"] = out["timing"]["wall_s"]
        row["div"] = len(out["stats"]["divergences"])
        row["rec_det"] = out["stats"]["tape_record"]
        if fulls:
            row["full_quality"] = summary_row(fulls[i])["quality"]
            row["full_s"] = fulls[i]["timing"]["wall_s"]
        rows.append(row)
        print(f"  {row['point']}: quality {row['quality']:.4f} ({row['s']:.1f} s, detector re-run "
              f"{row['rec_det']} games, divergences {row['div']})"
              + (f" | full {row['full_quality']:.4f} ({row['full_s']:.1f} s)" if "full_quality" in row else ""),
              flush=True)
    cols = [c for c in ("quality", "full_quality", *[f"q_{s}" for s in suites], "rec", "prec", "idsw", "frag", "g_live",
                        "g_dead", "me_bad", "stk", "resets", "mjmp", "real_rec", "real_id", "cases_rec",
                        "cases_id", "s") if c in rows[0]]
    w = max(len(r["point"]) for r in rows)
    print(f"{'point':{w}s} " + " ".join(f"{c:>8s}" for c in cols))
    for r in rows:
        print(f"{r['point']:{w}s} " + " ".join(
            f"{r[c]:8.0f}" if c in ("idsw", "frag", "g_live", "g_dead", "resets", "mjmp")
            else f"{r[c]:8.1f}" if c == "s" else f"{r[c]:8.4f}" for c in cols))
    if len(rows) >= 3 and "full_quality" in rows[0]:
        q = np.array([r["quality"] for r in rows])
        fq = np.array([r["full_quality"] for r in rows])
        pear = float(np.corrcoef(q, fq)[0, 1]) if q.std() > 0 and fq.std() > 0 else float("nan")
        rk = np.argsort(np.argsort(q)), np.argsort(np.argsort(fq))
        spear = float(np.corrcoef(*rk)[0, 1]) if q.std() > 0 and fq.std() > 0 else float("nan")
        print(f"subset vs full quality over {len(rows)} points: Pearson {pear:.3f}, Spearman {spear:.3f}")
    if json_out:
        Path(json_out).write_text(json.dumps(rows, indent=1, default=str), encoding="utf-8")
    return 0


def verify(suites: list[str], real: bool, jobs: int, gym_json: list[str] | None) -> int:
    """Cached (warm) twice == byte-identical; == uncached pool (det_gym's own run_game path);
    == det_gym.py --json outputs when given."""
    ok = True
    t0 = time.perf_counter()
    a1 = evaluate(suites, real, jobs=jobs)
    t1 = time.perf_counter()
    a2 = evaluate(suites, real, jobs=jobs)
    t2 = time.perf_counter()
    same = canon(a1) == canon(a2)
    ok &= same
    print(f"cached run 1 {t1 - t0:.1f} s, run 2 {t2 - t1:.1f} s: metrics byte-identical: {same}")
    b = evaluate(suites, real, mode="nocache", jobs=jobs)
    t3 = time.perf_counter()
    same = canon(a2) == canon(b)
    ok &= same
    print(f"uncached pool (det_gym.run_game, live render + detector) {t3 - t2:.1f} s: identical: {same}")
    if not same:
        _diff(metrics_only(a2), metrics_only(b))
    for suite, path in zip(suites, gym_json or []):
        g = json.loads(Path(path).read_text(encoding="utf-8"))
        mine = json.loads(json.dumps(metrics_only(dict(a2["suites"][suite], real=a2.get("real"),
                                                       real_cases=a2.get("real_cases"))), default=str))
        theirs = json.loads(json.dumps(metrics_only(g), default=str))
        keys = [k for k in ("games", "total", "fails") + (("real", "real_cases") if "real" in theirs else ())]
        d = _diff({k: mine.get(k) for k in keys}, {k: theirs.get(k) for k in keys})
        ok &= d == 0
        print(f"det_gym.py {suite} ({path}): {'identical' if d == 0 else f'{d} differences'}")
    print("VERIFY", "OK" if ok else "FAILED")
    return 0 if ok else 1


def _diff(a: Any, b: Any, path: str = "", out: list | None = None) -> int:
    top = out is None
    out = [] if out is None else out
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b), key=str):
            _diff(a.get(k), b.get(k), f"{path}.{k}", out)
    elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        for i, (x, y) in enumerate(zip(a, b)):
            _diff(x, y, f"{path}[{i}]", out)
    else:
        eq = a == b
        if not eq and isinstance(a, float) and isinstance(b, float):
            eq = (math.isnan(a) and math.isnan(b)) or abs(a - b) <= 1e-9
        if not eq:
            out.append(f"{path}: {str(a)[:100]} != {str(b)[:100]}")
    if top:
        for line in out[:15]:
            print("  DIFF", line)
    return len(out)


if __name__ == "__main__":
    raise SystemExit(main())
