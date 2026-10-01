"""Real-game diagnostic bundle ("Diagnostic", hotkey Ctrl+F8 / UI button).

Our tests only see a few screenshots and synthetic minimaps; this recorder captures what the
pipeline really sees and does on the user's machine so that failures can be reproduced:

* once: ``meta.json`` (app version, OS, CPU / cores, performance budget, process priority,
  capture backend + its statistics, game window / minimap rectangles, DPI scale, monitors, the
  game's own display settings, a whitelist of our settings - never API keys / tokens), and a
  thumbnail of the game window (``screen.jpg``, <= 1280 px wide);
* every ``interval_s`` (default 2 s) for ``duration_s`` (default 60 s): the raw minimap crop
  (``frames/NNN_minimap.png``), the annotated preview (``NNN_annotated.png``) and
  ``NNN.json``: detections (position, radius, scores, class, identity, which detector path /
  matcher mode), tracks (smoothed + Kalman positions, age, stacked...), engine state, health
  (capture fps, timings, champions seen / expected, minimap score, CPU %), Live Client roster
  reduced to champion names / teams / roles / levels / summoner spells (no summoner name, no
  Riot ID);
* at the end: ``summary.json`` + the tail of the application log (user folder paths masked),
  everything zipped as ``<user data>/diagnostics/diag_YYYYmmdd_HHMMSS.zip``; the folder is
  opened in the file explorer.

Screen pixels and official API data only (the same as what the app already uses). The
recorder runs in its own daemon thread, at a low rate (one sample / 2 s: a PNG encode of
~300 x 300 px), and never raises.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import re
import shutil
import sys
import threading
import time
import zipfile
from pathlib import Path
from typing import Any, Callable

import numpy as np

log = logging.getLogger(__name__)

#: Settings copied into meta.json (whitelist: nothing personal, no key / token / name).
CONFIG_WHITELIST = (
    "target_fps", "detector_backend", "detection_threshold", "minimap_mode", "minimap_side",
    "manual_minimap_rect", "overlay_enabled", "overlay_mode", "overlay_hide_from_capture", "hud_enabled",
    "hud_position", "radar_position", "radar_scale", "overlay_fps", "overlay_scale", "overlay_opacity",
    "capture_backend", "perf_mode", "adaptive_rate", "low_priority", "eco_qos", "pause_when_unfocused",
    "fog_mode", "safe_mode", "sensitivity", "warn_radius", "danger_radius", "voice_level", "skill_level",
    "icon_scale_by_res", "colorblind", "ui_scaling",
)
LOG_TAIL_LINES = 600
THUMB_MAX_W = 1280


def _jsonable(x: Any) -> Any:
    """Best-effort JSON conversion (numpy scalars, dataclasses, Rects, paths...)."""
    try:
        if x is None or isinstance(x, (bool, int, str)):
            return x
        if isinstance(x, float):
            return x if np.isfinite(x) else None
        if isinstance(x, (np.integer,)):
            return int(x)
        if isinstance(x, (np.floating,)):
            v = float(x)
            return v if np.isfinite(v) else None
        if isinstance(x, np.ndarray):
            return None if x.size > 64 else x.tolist()
        if isinstance(x, dict):
            return {str(k): _jsonable(v) for k, v in x.items()}
        if isinstance(x, (list, tuple, set)):
            return [_jsonable(v) for v in x]
        if isinstance(x, Path):
            return str(x)
        to_dict = getattr(x, "to_dict", None)
        if callable(to_dict):
            return _jsonable(to_dict())
        import dataclasses

        if dataclasses.is_dataclass(x) and not isinstance(x, type):
            return {f.name: _jsonable(getattr(x, f.name)) for f in dataclasses.fields(x)}
        return str(x)
    except Exception:
        return None


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(_jsonable(data), ensure_ascii=False, indent=1), encoding="utf-8")


def _mask_paths(text: str) -> str:
    """Hide the user's folder names in log lines (``C:\\Users\\Name`` -> ``%USERPROFILE%``)."""
    try:
        home = str(Path.home())
        if home and len(home) > 3:
            text = text.replace(home, "%USERPROFILE%")
        return re.sub(r"(?i)([A-Z]:\\Users\\)[^\\\s]+", r"\1<user>", text)
    except Exception:
        return text


def _default_opener(path: Path) -> None:
    try:
        if sys.platform == "win32":
            os.startfile(str(path))  # type: ignore[attr-defined]
    except Exception:
        log.debug("Cannot open %s", path, exc_info=True)


def system_info() -> dict[str, Any]:
    """OS / Python / CPU facts (no user name, no machine name)."""
    out: dict[str, Any] = {}
    try:
        from treeaicoach import __version__
        from treeaicoach.sysperf import cpu_count, cpu_name

        out.update(app_version=__version__, python=sys.version.split()[0], platform=platform.platform(),
                   machine=platform.machine(), frozen=bool(getattr(sys, "frozen", False)),
                   cpu=cpu_name(), cores=cpu_count())
        if sys.platform == "win32":
            v = sys.getwindowsversion()
            out["windows_build"] = int(v.build)
    except Exception:
        log.debug("system_info failed", exc_info=True)
    return out


class DiagRecorder:
    """Records one diagnostic bundle for ``engine`` (a :class:`~treeaicoach.engine.CoachEngine`)."""

    def __init__(self, engine: Any, duration_s: float = 60.0, interval_s: float = 2.0,
                 out_root: Path | None = None, opener: Callable[[Path], None] | None = None,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], Any] | None = None) -> None:
        self.engine = engine
        self.duration_s = float(min(max(duration_s, 1.0), 600.0))
        self.interval_s = float(min(max(interval_s, 0.2), 30.0))
        self._out_root = out_root
        self._opener = opener or _default_opener
        self._clock = clock
        self._stop = threading.Event()
        self._sleep = sleep or (lambda s: self._stop.wait(s))
        self._thread: threading.Thread | None = None
        self.folder: Path | None = None
        self.zip_path: Path | None = None
        self.samples = 0
        self.error: str | None = None
        self.running = False
        self._t0 = 0.0
        self._screen_saved = False
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ control
    def _root(self) -> Path:
        if self._out_root is not None:
            return Path(self._out_root)
        from treeaicoach.paths import user_data_dir

        return Path(user_data_dir()) / "diagnostics"

    def start(self) -> Path | None:
        """Create the bundle folder, write meta.json and start sampling. Never raises."""
        try:
            root = self._root()
            root.mkdir(parents=True, exist_ok=True)
            name = time.strftime("diag_%Y%m%d_%H%M%S")
            folder = root / name
            i = 1
            while folder.exists() or (root / f"{folder.name}.zip").exists():
                folder = root / f"{name}_{i}"
                i += 1
            (folder / "frames").mkdir(parents=True)
            self.folder = folder
            self.running = True
            self._t0 = self._clock()
            self._write_meta()
            req = getattr(self.engine, "request_diag_snapshot", None)
            if callable(req):
                req(full_screen=True)
            self._thread = threading.Thread(target=self._run, name="TreeAICoach-diag", daemon=True)
            self._thread.start()
            log.info("Diagnostic recording started: %s (%.0f s, every %.1f s)", folder, self.duration_s,
                     self.interval_s)
            return folder
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            self.running = False
            log.exception("Diagnostic start failed")
            return None

    def stop(self) -> None:
        """Finish early (the bundle is still zipped)."""
        self._stop.set()

    def join(self, timeout: float | None = None) -> None:
        th = self._thread
        if th is not None:
            th.join(timeout)

    def status(self) -> dict[str, Any]:
        el = max(0.0, self._clock() - self._t0) if self.running else self.duration_s
        return {"running": self.running, "progress": round(min(1.0, el / self.duration_s), 3),
                "samples": self.samples, "folder": str(self.folder) if self.folder else None,
                "zip": str(self.zip_path) if self.zip_path else None, "error": self.error}

    # ------------------------------------------------------------------ content
    def put_screen(self, img: np.ndarray) -> None:
        """Full game window capture (called once by the analysis thread): saved as a thumbnail."""
        with self._lock:
            if self._screen_saved or self.folder is None:
                return
            self._screen_saved = True
        try:
            import cv2

            h, w = img.shape[:2]
            if w > THUMB_MAX_W:
                img = cv2.resize(img, (THUMB_MAX_W, max(1, int(round(h * THUMB_MAX_W / w)))),
                                 interpolation=cv2.INTER_AREA)
            ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
            if ok:
                (self.folder / "screen.jpg").write_bytes(buf.tobytes())
            meta = {"full_size": [int(w), int(h)], "thumb_size": [int(img.shape[1]), int(img.shape[0])]}
            _write_json(self.folder / "screen.json", meta)
        except Exception:
            log.debug("Diagnostic screen save failed", exc_info=True)

    def _write_meta(self) -> None:
        eng = self.engine
        meta: dict[str, Any] = {"created": time.strftime("%Y-%m-%d %H:%M:%S"), "system": system_info(),
                                "duration_s": self.duration_s, "interval_s": self.interval_s}
        try:
            cfg = getattr(eng, "cfg", None)
            meta["config"] = {k: getattr(cfg, k, None) for k in CONFIG_WHITELIST if cfg is not None}
        except Exception:
            pass
        try:
            from treeaicoach import sysperf

            meta["process"] = dict(getattr(sysperf, "APPLIED", {}) or {})
        except Exception:
            pass
        try:
            w = getattr(eng, "_settings_watcher", None)
            gs = w.get() if w is not None else None
            meta["game_settings"] = gs
        except Exception:
            meta["game_settings"] = None
        try:
            from treeaicoach.capture import monitor_rects

            meta["monitors"] = [r.to_dict() for r in monitor_rects()]
        except Exception:
            meta["monitors"] = None
        try:
            meta["health"] = eng.health()
        except Exception:
            pass
        _write_json(self.folder / "meta.json", meta)

    def _sample(self) -> None:
        import cv2

        snap = self.engine.diag_snapshot()
        n = self.samples
        frames = self.folder / "frames"
        frame = snap.pop("frame", None)
        prev = snap.pop("preview", None)
        if isinstance(frame, np.ndarray) and frame.size:
            cv2.imwrite(str(frames / f"{n:03d}_minimap.png"), frame)
            snap["frame_shape"] = list(frame.shape)
        if isinstance(prev, np.ndarray) and prev.size:
            cv2.imwrite(str(frames / f"{n:03d}_annotated.png"), prev)
        snap["elapsed_s"] = round(self._clock() - self._t0, 2)
        _write_json(frames / f"{n:03d}.json", snap)
        self.samples += 1

    def _run(self) -> None:
        try:
            next_t = self._clock()
            while not self._stop.is_set():
                now = self._clock()
                if now - self._t0 > self.duration_s:
                    break
                if now >= next_t:
                    try:
                        self._sample()
                    except Exception:
                        log.exception("Diagnostic sample failed")
                    next_t += self.interval_s
                self._sleep(max(0.05, min(0.5, next_t - self._clock())))
            self._finish()
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            log.exception("Diagnostic recorder failed")
        finally:
            self.running = False

    def _finish(self) -> None:
        folder = self.folder
        if folder is None:
            return
        try:
            summary = {"samples": self.samples, "health_end": self.engine.health()}
            _write_json(folder / "summary.json", summary)
        except Exception:
            log.debug("Diagnostic summary failed", exc_info=True)
        try:
            from treeaicoach.logging_setup import LOG_FILE_NAME
            from treeaicoach.paths import logs_dir

            lf = Path(logs_dir()) / LOG_FILE_NAME
            if lf.is_file():
                lines = lf.read_text(encoding="utf-8", errors="replace").splitlines()[-LOG_TAIL_LINES:]
                (folder / "log_tail.txt").write_text(_mask_paths("\n".join(lines)) + "\n", encoding="utf-8")
        except Exception:
            log.debug("Diagnostic log tail failed", exc_info=True)
        zpath = folder.with_suffix(".zip")
        with zipfile.ZipFile(zpath, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for p in sorted(folder.rglob("*")):
                if p.is_file():
                    zf.write(p, p.relative_to(folder.parent))
        shutil.rmtree(folder, ignore_errors=True)
        self.zip_path = zpath
        log.info("Diagnostic bundle written: %s (%d samples)", zpath, self.samples)
        try:
            self._opener(zpath.parent)
        except Exception:
            pass
