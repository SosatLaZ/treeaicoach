"""User configuration: dataclass with defaults, validation and atomic JSON persistence.

* ``Config.validated()`` returns a sanitized copy: wrong types -> field default, numbers
  clamped to their range, enum strings normalized, ``manual_minimap_rect`` checked.
* ``load_config()`` never raises: a missing file gives the defaults, a corrupt file is
  renamed ``config.json.bak`` and the defaults are used, unknown keys are ignored.
* ``save_config()`` writes atomically (temp file in the same folder + ``os.replace``), UTF-8.

The ranges/choices below are public so that the UI can build its sliders and menus from them.
"""

from __future__ import annotations

import copy
import json
import logging
import math
import numbers
import os
import tempfile
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

from treeaicoach import paths

log = logging.getLogger(__name__)

CONFIG_VERSION = 1                 # written as "config_version" (ignored on load for now)
MAX_CONFIG_BYTES = 1_000_000       # a bigger config.json is treated as corrupt
BACKUP_SUFFIX = ".bak"

INT_RANGES: dict[str, tuple[int, int]] = {
    "voice_rate": (-10, 10),       # SAPI rate
    "voice_volume": (0, 100),
}
FLOAT_RANGES: dict[str, tuple[float, float]] = {
    "sensitivity": (0.6, 1.6),
    "warn_radius": (0.05, 0.50),   # normalized by minimap width
    "danger_radius": (0.03, 0.35),
    "target_fps": (2.0, 20.0),
    "detection_threshold": (0.0, 0.95),   # 0 = use model_meta.json
    "collect_interval_s": (0.5, 60.0),
}
# A non-zero detection threshold is raised to at least this value (0 keeps its special meaning).
DETECTION_THRESHOLD_MIN = 0.05
CHOICES: dict[str, tuple[str, ...]] = {
    "detector_backend": ("auto", "onnx", "classic"),
    "minimap_mode": ("auto", "manual"),
    "minimap_side": ("auto", "right", "left"),
}
BOOL_FIELDS: tuple[str, ...] = (
    "beep_on_danger",
    "alert_jungler_approach",
    "alert_roam",
    "alert_collapse",
    "alert_jungler_spotted",
    "alert_laner_mia",
    "download_skin_icons",
    "autostart",
    "collect_samples",
    "show_preview",
)
VOICE_NAME_MAX_LEN = 256

# manual_minimap_rect: {"screen_w","screen_h","x","y","w","h"} in physical screen pixels.
RECT_KEYS: tuple[str, ...] = ("screen_w", "screen_h", "x", "y", "w", "h")
RECT_SCREEN_RANGE = (200, 32768)   # screen_w / screen_h
RECT_SIZE_MIN = 32                 # minimap w / h (and w <= screen_w, h <= screen_h)
RECT_COORD_LIMIT = 65536           # |x|, |y| (multi-monitor virtual coords may be negative)

_META_KEYS = frozenset({"config_version"})
_io_lock = threading.RLock()


class _Invalid:
    """Sentinel for 'value of the wrong type / unusable'."""

    def __repr__(self) -> str:
        return "<invalid>"


_INVALID: Any = _Invalid()


@dataclass
class Config:
    """All user settings, with defaults (see docs/ARCHITECTURE.md §4.2)."""

    # voice
    voice_name: str = ""            # "" = best available French voice
    voice_rate: int = 2             # -10..10 (SAPI)
    voice_volume: int = 100         # 0..100
    beep_on_danger: bool = True
    # alerts
    alert_jungler_approach: bool = True
    alert_roam: bool = True
    alert_collapse: bool = True
    alert_jungler_spotted: bool = True
    alert_laner_mia: bool = False
    sensitivity: float = 1.0        # 0.6..1.6, multiplies the radii
    warn_radius: float = 0.22       # normalized minimap (~3300 game units)
    danger_radius: float = 0.12     # (~1800 units)
    # capture / detection
    target_fps: float = 8.0         # 2..20
    detector_backend: str = "auto"  # "auto" | "onnx" | "classic"
    detection_threshold: float = 0.0  # 0 = value from model_meta.json
    minimap_mode: str = "auto"      # "auto" | "manual"
    minimap_side: str = "auto"      # "auto" | "right" | "left"
    manual_minimap_rect: dict | None = None   # {"screen_w","screen_h","x","y","w","h"} screen pixels
    download_skin_icons: bool = True
    # misc
    autostart: bool = True          # start the analysis at launch
    collect_samples: bool = False   # save minimaps for re-training
    collect_interval_s: float = 2.0
    show_preview: bool = False

    def effective_warn_radius(self) -> float:
        """``warn_radius * sensitivity`` (clamped; defaults if the fields are invalid)."""
        return self._field_ok("warn_radius") * self._field_ok("sensitivity")

    def effective_danger_radius(self) -> float:
        """``danger_radius * sensitivity`` (clamped, <= warn radius; defaults if invalid)."""
        danger = min(self._field_ok("danger_radius"), self._field_ok("warn_radius"))
        return danger * self._field_ok("sensitivity")

    def _field_ok(self, name: str) -> Any:
        """Sanitized value of one field, silently (cheap: safe to call every frame)."""
        default = _DEFAULTS.get(name)
        try:
            return _validate_field(name, getattr(self, name, default), default)
        except Exception:
            return default

    def validated(self) -> Config:
        """Return a sanitized copy (types fixed, values clamped). Never raises."""
        try:
            return _validate(self)
        except Exception:  # defensive: validation must never take the app down
            log.exception("Config validation failed; using defaults")
            return Config()

    def to_dict(self) -> dict[str, Any]:
        """Plain dict of all fields (deep copy)."""
        out: dict[str, Any] = {}
        for f in fields(self):
            value = getattr(self, f.name, None)
            try:
                out[f.name] = copy.deepcopy(value)
            except Exception:
                out[f.name] = value
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> Config:
        """Build a validated Config from a mapping; unknown keys ignored. Never raises."""
        try:
            if not isinstance(data, Mapping):
                if data is not None:
                    log.warning("Config data is %s, not an object; using defaults", type(data).__name__)
                return cls()
            names = {f.name for f in fields(cls)}
            known = {k: v for k, v in data.items() if isinstance(k, str) and k in names}
            unknown = [str(k) for k in data if k not in names and k not in _META_KEYS]
            if unknown:
                log.info("Ignoring unknown config keys: %s", ", ".join(sorted(unknown)[:20]))
            return cls(**known).validated()
        except Exception:
            log.exception("Cannot build Config from data; using defaults")
            return cls()


# --------------------------------------------------------------------------- validation

_DEFAULTS: dict[str, Any] = {f.name: f.default for f in fields(Config)}


def _real(value: Any) -> Any:
    """Finite float from a real number (not bool), else _INVALID."""
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        return _INVALID
    try:
        f = float(value)
    except (OverflowError, ValueError, TypeError):
        return _INVALID
    return f if math.isfinite(f) else _INVALID


def _as_bool(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, numbers.Integral) and value in (0, 1):
        return bool(value)
    return _INVALID


def _as_int(value: Any, lo: int, hi: int) -> Any:
    f = _real(value)
    if f is _INVALID:
        return _INVALID
    return int(min(max(round(f), lo), hi))


def _as_float(value: Any, lo: float, hi: float) -> Any:
    f = _real(value)
    if f is _INVALID:
        return _INVALID
    return float(min(max(f, lo), hi))


def _as_choice(value: Any, choices: tuple[str, ...]) -> Any:
    if not isinstance(value, str):
        return _INVALID
    s = value.strip().lower()
    return s if s in choices else _INVALID


def _as_voice_name(value: Any) -> Any:
    if not isinstance(value, str):
        return _INVALID
    s = "".join(ch for ch in value if ch.isprintable()).strip()
    return s[:VOICE_NAME_MAX_LEN]


def _as_rect(value: Any) -> dict[str, int] | None:
    """Validate manual_minimap_rect; anything unusable -> None."""
    if value is None or not isinstance(value, Mapping):
        return None
    out: dict[str, int] = {}
    for k in RECT_KEYS:
        f = _real(value.get(k))
        if f is _INVALID:
            return None
        out[k] = int(round(f))
    s_lo, s_hi = RECT_SCREEN_RANGE
    if not (s_lo <= out["screen_w"] <= s_hi and s_lo <= out["screen_h"] <= s_hi):
        return None
    if not (RECT_SIZE_MIN <= out["w"] <= out["screen_w"] and RECT_SIZE_MIN <= out["h"] <= out["screen_h"]):
        return None
    if abs(out["x"]) > RECT_COORD_LIMIT or abs(out["y"]) > RECT_COORD_LIMIT:
        return None
    return out


def _validate_field(name: str, value: Any, default: Any) -> Any:
    """Sanitized value for one field (default if the value is unusable)."""
    if name in BOOL_FIELDS:
        res = _as_bool(value)
    elif name in INT_RANGES:
        res = _as_int(value, *INT_RANGES[name])
    elif name == "detection_threshold":
        res = _as_float(value, *FLOAT_RANGES[name])
        if res is not _INVALID and 0.0 < res < DETECTION_THRESHOLD_MIN:
            res = DETECTION_THRESHOLD_MIN
    elif name in FLOAT_RANGES:
        res = _as_float(value, *FLOAT_RANGES[name])
    elif name in CHOICES:
        res = _as_choice(value, CHOICES[name])
    elif name == "voice_name":
        res = _as_voice_name(value)
    elif name == "manual_minimap_rect":
        res = _as_rect(value)
    else:  # a field without a rule: keep it as is
        res = value
    return copy.deepcopy(default) if res is _INVALID else res


def _short_repr(value: Any, limit: int = 60) -> str:
    try:
        r = repr(value)
    except Exception:
        r = f"<{type(value).__name__}>"
    return r if len(r) <= limit else r[: limit - 3] + "..."


def _same(a: Any, b: Any) -> bool:
    try:
        if isinstance(a, float) and isinstance(b, float) and math.isnan(a) and math.isnan(b):
            return False
        return bool(a == b) and isinstance(a, bool) == isinstance(b, bool)
    except Exception:  # e.g. numpy arrays: ambiguous truth value
        return False


def _validate(cfg: Config) -> Config:
    defaults = Config()
    out: dict[str, Any] = {}
    fixes: list[str] = []
    for f in fields(Config):
        default = getattr(defaults, f.name)
        raw = getattr(cfg, f.name, default)
        new = _validate_field(f.name, raw, default)
        out[f.name] = new
        if not _same(raw, new):
            fixes.append(f"{f.name}={_short_repr(raw)} -> {new!r}")

    # cross-field rules
    if out["danger_radius"] > out["warn_radius"]:
        fixes.append(f"danger_radius {out['danger_radius']!r} > warn_radius -> {out['warn_radius']!r}")
        out["danger_radius"] = out["warn_radius"]
    if out["minimap_mode"] == "manual" and out["manual_minimap_rect"] is None:
        fixes.append("minimap_mode 'manual' without manual_minimap_rect -> 'auto'")
        out["minimap_mode"] = "auto"

    if fixes:
        log.warning("Config: corrected %d value(s): %s", len(fixes), "; ".join(fixes))
    return Config(**out)


# --------------------------------------------------------------------------- persistence


def _replace_with_retry(src: str | os.PathLike[str], dst: str | os.PathLike[str],
                        attempts: int = 6, delay: float = 0.05) -> None:
    """``os.replace`` retried briefly (Windows: antivirus/indexer may hold the file). Raises on failure."""
    for i in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(delay * (i + 1))


def backup_path(path: Path) -> Path:
    """Where a corrupt config file is moved (``config.json.bak``)."""
    return path.with_name(path.name + BACKUP_SUFFIX)


def _backup_corrupt(path: Path, reason: str) -> None:
    bak = backup_path(path)
    try:
        _replace_with_retry(path, bak)
        log.warning("Config file %s is invalid (%s); moved to %s, using defaults", path, reason, bak)
    except Exception as exc:
        log.warning("Config file %s is invalid (%s) and could not be moved (%s); using defaults",
                    path, reason, exc)


def _resolve(path: str | os.PathLike[str] | None) -> Path:
    return Path(path) if path is not None else paths.config_path()


def load_config(path: str | os.PathLike[str] | None = None) -> Config:
    """Load the config (default path: ``paths.config_path()``). Never raises.

    Missing/unreadable file -> defaults; corrupt file -> renamed ``.bak`` + defaults;
    unknown keys ignored; invalid values replaced by their default.
    """
    try:
        p = _resolve(path)
    except Exception:
        log.exception("Invalid config path %r; using defaults", path)
        return Config()
    with _io_lock:
        try:
            if not p.exists():
                log.info("No config file at %s; using defaults", p)
                return Config()
            if not p.is_file():
                log.warning("Config path %s is not a file; using defaults", p)
                return Config()
            size = p.stat().st_size
            if size > MAX_CONFIG_BYTES:
                _backup_corrupt(p, f"file too large ({size} bytes)")
                return Config()
            raw = p.read_bytes()
        except Exception as exc:
            log.warning("Cannot read config file %s (%s); using defaults", p, exc)
            return Config()
        try:
            text = raw.decode("utf-8-sig")   # tolerate a BOM (Notepad)
            data = json.loads(text)
        except (ValueError, RecursionError) as exc:  # UnicodeDecodeError, JSONDecodeError
            _backup_corrupt(p, type(exc).__name__)
            return Config()
        except Exception as exc:
            log.warning("Unexpected error parsing %s (%s); using defaults", p, exc)
            return Config()
        if not isinstance(data, dict):
            _backup_corrupt(p, f"top-level JSON is {type(data).__name__}, not an object")
            return Config()
    cfg = Config.from_dict(data)
    log.info("Config loaded from %s", p)
    return cfg


def save_config(cfg: Config, path: str | os.PathLike[str] | None = None) -> bool:
    """Atomically write the (validated) config as UTF-8 JSON. Never raises.

    Returns True on success, False on failure (logged). The previous file is left intact
    if anything goes wrong.
    """
    tmp_name: str | None = None
    p: Path | None = None
    try:
        p = _resolve(path)
        if isinstance(cfg, Config):
            good = cfg.validated()
        else:
            good = Config.from_dict(cfg if isinstance(cfg, Mapping) else None)
        payload: dict[str, Any] = {"config_version": CONFIG_VERSION}
        payload.update(good.to_dict())
        text = json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
        with _io_lock:
            p.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(prefix=p.name + ".", suffix=".tmp", dir=str(p.parent))
            try:
                fh = os.fdopen(fd, "w", encoding="utf-8", newline="\n")
            except Exception:
                os.close(fd)
                raise
            with fh:
                fh.write(text)
                fh.flush()
                os.fsync(fh.fileno())
            _replace_with_retry(tmp_name, p)
            tmp_name = None
        log.debug("Config saved to %s", p)
        return True
    except Exception as exc:
        log.error("Cannot save config to %s: %s", p if p is not None else path, exc)
        return False
    finally:
        if tmp_name is not None:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
