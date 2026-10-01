"""Small shared helpers: tolerant number coercion and French time formatting.

Pure Python (no numpy), safe to import from any module. Every function is total: bad input
(``None``, strings, NaN, inf, huge ints) gives the documented fallback, never an exception.
"""

from __future__ import annotations

import math
from typing import Any


def finite(x: Any, default: float | None = None) -> float | None:
    """``float(x)`` when it is a finite number, else ``default``. ``None`` and booleans are not
    numbers here (a ``True`` flag never becomes ``1.0``)."""
    if x is None or isinstance(x, bool):
        return default
    try:
        v = float(x)
    except (TypeError, ValueError, OverflowError):
        return default
    return v if math.isfinite(v) else default


def finite_loose(x: Any, default: float | None = None) -> float | None:
    """Like :func:`finite` but booleans count as ``0.0`` / ``1.0`` (plain ``float()`` rules)."""
    try:
        v = float(x)
    except (TypeError, ValueError, OverflowError):
        return default
    return v if math.isfinite(v) else default


def clock(seconds: Any, missing: str = "?", *, clamp: bool = False, rounded: bool = False,
          hours: bool = False) -> str:
    """Game time as ``m:ss`` (``"4:07"``, ``"28:14"``).

    ``missing`` is returned for a non-number and, unless ``clamp`` (negative -> ``0:00``), for a
    negative value. ``rounded`` rounds to the nearest second instead of truncating; ``hours``
    switches to ``h:mm:ss`` from one hour on.
    """
    v = finite_loose(seconds)
    if v is None:
        return missing
    if v < 0:
        if not clamp:
            return missing
        v = 0.0
    s = int(round(v)) if rounded else int(v)
    if hours and s >= 3600:
        h, rem = divmod(s, 3600)
        return f"{h}:{rem // 60:02d}:{rem % 60:02d}"
    return f"{s // 60}:{s % 60:02d}"


def seconds_fr(n: int) -> str:
    """Spoken French duration: ``"une seconde"`` / ``"23 secondes"`` / ``"une minute"`` /
    ``"1 minute 30"`` / ``"2 minutes 5"``."""
    n = max(0, int(n))
    if n < 60:
        return "une seconde" if n <= 1 else f"{n} secondes"
    m, s = divmod(n, 60)
    head = "une minute" if m == 1 else f"{m} minutes"
    return head if s == 0 else f"{m} minute{'s' if m > 1 else ''} {s}"
