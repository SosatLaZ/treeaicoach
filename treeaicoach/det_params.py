"""Validated overrides of the detection / tracking thresholds (``assets/model/det_params.json``).

``tools/det_tune.py`` searches the parameters of :data:`TUNABLE` with the detection gym
(``tools/det_gym.py``: tune suite, then holdout suite + real crops) and writes the winners to
``det_params.json`` ONLY when they were validated (holdout better, real crops not worse).
The modules call :func:`apply` once at import; without the file the code defaults stand.

File format: ``{"params": {"roster_matcher.TRACK_RELAX": 0.12, ...}, "validated": {...}}``.
Keys are ``<module>.<NAME>`` (module constants) or ``<module>.<Class>.<NAME>`` (class
attributes); only keys of :data:`TUNABLE` are applied, clamped to their range. Never raises.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

PARAMS_FILE = Path(__file__).resolve().parent / "assets" / "model" / "det_params.json"

#: key -> (low, high, kind): the search space of det_tune.py and the clamp of the overrides.
TUNABLE: dict[str, tuple[float, float, str]] = {
    "roster_matcher.THR_MARGIN": (0.08, 0.20, "float"),
    "roster_matcher.TRACK_RELAX": (0.0, 0.25, "float"),
    "roster_matcher.TRACK_RELAX_OWN": (0.1, 0.6, "float"),
    "roster_matcher.VERIFY_ZONE": (0.0, 0.25, "float"),
    "roster_matcher.VERIFY_MIN": (0.4, 0.95, "float"),
    "roster_matcher.VETO_MARGIN": (0.0, 0.25, "float"),
    "roster_matcher.VETO_MAX": (0.005, 0.2, "float"),
    "roster_matcher.RING_VERIFY_OWN": (0.1, 0.6, "float"),
    "roster_matcher.RING_VERIFY_TEAM": (0.6, 0.99, "float"),
    "roster_matcher.JUMP_CONFIRM_S": (0.5, 3.0, "float"),
    "roster_matcher.STACK_RING_MARGIN": (0.0, 0.15, "float"),
    "tracker.HIDE_AFTER": (0.3, 1.2, "float"),
    "tracker.HIDE_FRAMES": (2.0, 6.0, "float"),
    "tracker.IDENTITY_COAST_S": (0.3, 2.0, "float"),
    "tracker.STACK_HOLD_S": (1.0, 8.0, "float"),
    "detector.HybridDetector.EXTRA_VERIFY": (0.0, 0.5, "float"),
}

_LOADED: dict[str, Any] | None = None


def load(path: Path | None = None) -> dict[str, Any]:
    """The validated overrides ``{key: value}`` ({} without a file). Cached."""
    global _LOADED
    if path is None and _LOADED is not None:
        return _LOADED
    p = path or PARAMS_FILE
    out: dict[str, Any] = {}
    try:
        data = json.loads(Path(p).read_text(encoding="utf-8"))
        for k, v in (data.get("params") or {}).items():
            if k in TUNABLE:
                lo, hi, _kind = TUNABLE[k]
                out[k] = min(hi, max(lo, float(v)))
    except FileNotFoundError:
        pass
    except Exception:
        log.warning("Ignoring invalid %s", p, exc_info=True)
    if path is None:
        _LOADED = out
    return out


def apply(module: str, namespace: dict | None = None) -> None:
    """Apply the overrides of ``module`` (``"roster_matcher"`` ...) to its globals / classes."""
    try:
        for key, val in load().items():
            set_value(key, val, namespace=namespace, module=module)
    except Exception:
        log.debug("det_params.apply failed", exc_info=True)


def set_value(key: str, value: Any, namespace: dict | None = None, module: str | None = None) -> bool:
    """Set one tunable (module constant or class attribute) at run time. True when set."""
    parts = key.split(".")
    mod = parts[0]
    if module is not None and mod != module:
        return False
    ns = namespace
    if ns is None:
        m = sys.modules.get(f"treeaicoach.{mod}")
        if m is None:
            import importlib

            m = importlib.import_module(f"treeaicoach.{mod}")
        ns = vars(m)
    if len(parts) == 2:
        ns[parts[1]] = value
        return True
    if len(parts) == 3 and parts[1] in ns:
        setattr(ns[parts[1]], parts[2], value)
        return True
    return False


def get_value(key: str) -> Any:
    import importlib

    parts = key.split(".")
    m = importlib.import_module(f"treeaicoach.{parts[0]}")
    obj: Any = m
    for p in parts[1:]:
        obj = getattr(obj, p)
    return obj
